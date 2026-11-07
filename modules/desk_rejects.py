"""Today's trade-signal rejects for the hosted desk (Cloud Run, no Mac).

Python port of the desk's ``src/research-rejects.mjs`` (v2.45.2), which counted
rejects from the Mac sidecar. Same definition, so the count is the one Eric
uses (10/7: 454 = Trendline pipeline rejects):

* source: today's daily marker ``trendline_geometry.candidates`` (read only,
  via ``DailyRunTracker.get_trendline_geometry``); one record per candidate key
  (e.g. ``BP:bear:trendline0``) whose ``state_reason`` (else
  ``setup_payload.attempt_consumed_reason``) starts with a hard-reject prefix
  in ``REJECT_STAGE_BY_PREFIX``. Capacity waits (``slot_unavailable_reject``,
  ``slot_side_cooldown``) and TTL expiries are not rejects.
* union per PT date: prune_prior_slots_from_candidates drops earlier-slot rows
  when TL2/TL3 build, so records are kept in memory and in a small private
  object ``<ns>/desk_state/rejects/<ymd>.json`` (seeded once after a restart).
  The daily marker itself is never written here (its save also re-syncs the
  priority_optimizer daily signals file).

ORB1 / Breakout rejects are not in the geometry and are not counted (same as
the desk's v2.45.2 count). Read-only with respect to priority_optimizer data.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PT = ZoneInfo("America/Los_Angeles")
SOURCE = "cloud_run.desk_rejects"
CACHE_SEC = 20.0  # ~one discovery rotate; the marker is ~1.4 MB

REJECT_STAGE_BY_PREFIX = {
    "trade_profile_reject": "trade_profile",
    "exec_reject": "execution",
    "quality_reject": "quality",
    "pc_oi_filter_reject": "pc_oi_filter",
    "dir_pct_filter_reject": "dir_pct_filter",
    "chain_skew_filter_reject": "chain_skew_filter",
    "max_open_positions_reached": "max_open_positions",
}

_LOCK = threading.Lock()
_STORE: Dict[str, Any] = {"ymd": None, "records": {}, "seeded": False, "saved_n": 0}
_CACHE: Dict[str, Any] = {"at": 0.0, "ymd": None, "block": None}


def pt_ymd(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(PT).date().isoformat()


def reject_reason_family(detail: Any) -> str:
    """call_chain_volume_17.0_lt_19595.0 -> call_chain_volume; spread_gt_25pct -> spread."""
    raw = str(detail or "").strip()
    out: List[str] = []
    for tok in [t for t in raw.split("_") if t]:
        if re.match(r"^-?\d", tok):
            break
        out.append(tok)
    while len(out) > 1 and out[-1] in ("lt", "gt", "le", "ge", "eq"):
        out.pop()
    return "_".join(out) or raw[:60] or "unknown"


def parse_reject_reason(raw: Any) -> Optional[Dict[str, str]]:
    s = str(raw or "").strip()
    i = s.find(":")
    if i <= 0:
        return None
    stage = REJECT_STAGE_BY_PREFIX.get(s[:i])
    if not stage:
        return None
    detail = s[i + 1 :].split("|")[0].strip()
    return {"stage": stage, "reason": reject_reason_family(detail), "detail": detail}


def rejects_from_geometry(geo: Any, ymd: str) -> List[Dict[str, Any]]:
    cands = geo.get("candidates") if isinstance(geo, dict) else None
    if isinstance(cands, list):
        items = [(r.get("trade_id") if isinstance(r, dict) else None, r) for r in cands]
    elif isinstance(cands, dict):
        items = list(cands.items())
    else:
        return []
    out: List[Dict[str, Any]] = []
    for key, row in items:
        if not isinstance(row, dict):
            continue
        sp = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
        raw = row.get("state_reason") if parse_reject_reason(row.get("state_reason")) else sp.get("attempt_consumed_reason")
        parsed = parse_reject_reason(raw)
        trade_id = str(key or row.get("trade_id") or "").strip()
        if not parsed or not trade_id:
            continue
        out.append(
            {
                "id": f"{ymd}|{trade_id}",
                "trade_id": trade_id,
                "path": "TL",
                "symbol": row.get("symbol"),
                "side": sp.get("expected_option_side"),
                "setup": sp.get("setup_type"),
                "stage": parsed["stage"],
                "reason": parsed["reason"],
                "detail": str(raw)[:160],
                "at": row.get("qualified_at") or row.get("built_at"),
            }
        )
    return out


def _tally(records: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for rec in records:
        k = str(rec.get(key) or "unknown")
        counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _store_path(ymd: str) -> str:
    try:
        from modules.runtime_mode import gcs_namespace

        ns = str(gcs_namespace() or "research_app").strip("/") or "research_app"
    except Exception:
        ns = "research_app"
    return f"{ns}/desk_state/rejects/{ymd}.json"


def _gcs():
    try:
        from modules.gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        return gcs if gcs and getattr(gcs, "enabled", False) else None
    except Exception:
        return None


def _read_geometry() -> Dict[str, Any]:
    from modules.daily_run_tracker import get_daily_run_tracker

    return get_daily_run_tracker().get_trendline_geometry()


def rejects_block(
    now: Optional[datetime] = None,
    *,
    geometry: Optional[Dict[str, Any]] = None,
    persist: bool = True,
) -> Optional[Dict[str, Any]]:
    """Schema-1 rejects block for snapshot.json / discovery.json; None when unreadable."""
    now = now or datetime.now(timezone.utc)
    ymd = pt_ymd(now)
    mono = time.monotonic()
    if geometry is None:
        with _LOCK:
            cached = _CACHE.get("block")
            if cached is not None and _CACHE.get("ymd") == ymd and mono - float(_CACHE["at"]) < CACHE_SEC:
                return dict(cached)
        try:
            geometry = _read_geometry()
        except Exception as err:
            log.debug("DESK_REJECTS | geometry unreadable | %s", err)
            return None
    fresh = rejects_from_geometry(geometry, ymd)
    gcs = _gcs() if persist else None
    with _LOCK:
        if _STORE.get("ymd") != ymd:
            _STORE.update({"ymd": ymd, "records": {}, "seeded": False, "saved_n": 0})
        records: Dict[str, Dict[str, Any]] = _STORE["records"]
        seed_needed = gcs is not None and not _STORE["seeded"]
        _STORE["seeded"] = _STORE["seeded"] or gcs is not None
    if seed_needed:
        try:
            raw = gcs.read_string(_store_path(ymd))
            prior = json.loads(raw) if raw else {}
            if isinstance(prior, dict) and prior.get("ymd") == ymd and isinstance(prior.get("records"), dict):
                with _LOCK:
                    for rid, rec in prior["records"].items():
                        records.setdefault(str(rid), rec)
                    _STORE["saved_n"] = len(prior["records"])
        except Exception as err:
            log.debug("DESK_REJECTS | seed skipped | %s", err)
    with _LOCK:
        for rec in fresh:
            records[rec["id"]] = rec
        rows = list(records.values())
        dirty = gcs is not None and len(records) > int(_STORE.get("saved_n") or 0)
        body = json.dumps({"ymd": ymd, "records": records}, default=str, separators=(",", ":")) if dirty else None
    if dirty and body:
        try:
            if gcs.upload_string(_store_path(ymd), body):
                with _LOCK:
                    _STORE["saved_n"] = len(rows)
        except Exception as err:
            log.debug("DESK_REJECTS | persist skipped | %s", err)
    as_of = now.astimezone(timezone.utc).isoformat()
    block = {
        "schema": 1,
        "ymd": ymd,
        "as_of": as_of,
        "count": len({r.get("id") for r in rows}),
        "by_stage": _tally(rows, "stage"),
        "by_reason": _tally(rows, "reason"),
        "by_path": _tally(rows, "path"),
        "source": SOURCE,
    }
    with _LOCK:
        _CACHE.update({"at": mono, "ymd": ymd, "block": dict(block)})
    return block


def reset_for_tests() -> None:
    with _LOCK:
        _STORE.update({"ymd": None, "records": {}, "seeded": False, "saved_n": 0})
        _CACHE.update({"at": 0.0, "ymd": None, "block": None})
