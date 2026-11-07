"""Restart-safe persistence for the 5m chain ring (t166u, Oct 14 2026).

The ring (`system._central_tech_chain_ring`) holds call/put chain-volume
samples per symbol. `dir_pct_change_5m` needs a sample 3-10 minutes older than
the decision chain, so a Cloud Run restart used to wipe every prior for the
next 4+ minutes. This module persists a small copy of the ring with the same
mechanism as the trendline / ORB day freeze:

* writer: `modules.gcs_persistence.get_gcs_persistence().upload_string`
* path:   `gcs_daily_markers_path("chain_ring_5m/{YYYY-MM-DD}.json")`
          (`{research_app|final_app}/daily_markers/chain_ring_5m/...`), plus a
          local copy under `DAILY_MARKER_DIR/chain_ring_5m/`
* loader: local file first, then `gcs_read_daily_markers_string`
* cadence: debounced, at most once per `DIR5M_RING_PERSIST_DEBOUNCE_SEC` (60 s)
  and only when the ring changed; forced on shutdown. It is called from the
  discovery scan. The GCS write runs on a daemon thread, off the event loop.

It is a separate object, not a key inside `daily_markers/{date}.json`, so a
ring flush can never rewrite the ORB / trendline freeze. Only samples younger
than the ring TTL (60 min) are kept, at most N per symbol, and the whole object
is overwritten on each flush. Reload happens only for the same PT trading day.
No broker call.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SCHEMA = "chain_ring_5m_v1"
FIELDS = ("t", "call_chain_volume", "put_chain_volume", "representative_volume", "cumulative_volume", "open_interest")
LAST_FLUSH_ATTR = "_central_tech_chain_ring_last_flush_mono"
INFLIGHT_ATTR = "_central_tech_chain_ring_flush_inflight"
GCS_OVERRIDE_ATTR = "_chain_ring_persist_gcs"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _num(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def persist_enabled() -> bool:
    from central_tech.chain_ring import ring_v2_enabled

    return ring_v2_enabled() and _flag("DIR5M_RING_PERSIST_ENABLED", True)


def debounce_sec() -> float:
    return max(5.0, _num("DIR5M_RING_PERSIST_DEBOUNCE_SEC", 60.0))


def max_per_symbol() -> int:
    return int(max(4, _num("DIR5M_RING_PERSIST_MAX_PER_SYMBOL", 16)))


def trading_date_key(now: Optional[float] = None) -> str:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # pragma: no cover
        tz = None
    dt = datetime.fromtimestamp(float(now if now is not None else time.time()), tz)
    return dt.date().isoformat()


def relative_path(date_key: str) -> str:
    return f"chain_ring_5m/{date_key}.json"


def local_path(date_key: str) -> Path:
    base = Path(os.getenv("DAILY_MARKER_DIR", "/tmp/easy_etrade_markers"))
    return base / "chain_ring_5m" / f"{date_key}.json"


def _gcs(system: Any) -> Any:
    """GCS client, or None. Tests inject a fake; pytest never gets the real one."""
    override = getattr(system, GCS_OVERRIDE_ATTR, None) if system is not None else None
    if override is not None:
        return override
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        from modules.gcs_persistence import get_gcs_persistence

        g = get_gcs_persistence()
        return g if getattr(g, "enabled", False) else None
    except Exception:
        return None


def _round(v: Any) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 3) if abs(f - round(f)) > 1e-9 else int(round(f))


def serialize_ring(system: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    from central_tech.chain_ring import _ensure_chain_skew_ring, ring_ttl_sec

    now_s = float(now if now is not None else time.time())
    ttl = ring_ttl_sec()
    cap = max_per_symbol()
    ring = _ensure_chain_skew_ring(system)
    out: Dict[str, List[List[Any]]] = {}
    for sym, buf in list(ring.items()):
        rows = []
        for s in buf if isinstance(buf, list) else []:
            if not isinstance(s, dict):
                continue
            try:
                t = float(s.get("t"))
            except (TypeError, ValueError):
                continue
            if now_s - t > ttl or s.get("call_chain_volume") is None or s.get("put_chain_volume") is None:
                continue
            rows.append([round(t, 3)] + [_round(s.get(k)) for k in FIELDS[1:]])
        if rows:
            rows.sort(key=lambda r: r[0])
            out[str(sym).upper()] = rows[-cap:]
    return {
        "schema": SCHEMA,
        "date": trading_date_key(now_s),
        "saved_at": round(now_s, 3),
        "ttl_sec": ttl,
        "fields": list(FIELDS),
        "symbols": out,
    }


def _write(system: Any, payload: Dict[str, Any]) -> bool:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    date_key = str(payload.get("date"))
    ok = False
    try:
        p = local_path(date_key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(p)
        ok = True
    except Exception as exc:
        log.debug("CHAIN_RING_PERSIST | action=local_write_failed | err=%s", exc)
    g = _gcs(system)
    if g is not None:
        try:
            from modules.runtime_mode import gcs_daily_markers_path

            ok = bool(
                g.upload_string(
                    gcs_daily_markers_path(relative_path(date_key)),
                    body,
                    content_type="application/json",
                )
            ) or ok
        except Exception as exc:
            log.warning("CHAIN_RING_PERSIST | action=gcs_write_failed | err=%s", exc)
    n = sum(len(v) for v in (payload.get("symbols") or {}).values())
    log.info(
        "CHAIN_RING_PERSIST | action=flushed | date=%s | symbols=%d | samples=%d | bytes=%d | grep=CHAIN_RING_PERSIST",
        date_key, len(payload.get("symbols") or {}), n, len(body),
    )
    return ok


def maybe_flush_chain_ring(
    system: Any, *, force: bool = False, sync: bool = False, now: Optional[float] = None
) -> bool:
    """Flush when the ring changed and the debounce elapsed (or ``force``)."""
    if system is None or not persist_enabled():
        return False
    from central_tech.chain_ring import RING_DIRTY_ATTR

    if not force and not bool(getattr(system, RING_DIRTY_ATTR, False)):
        return False
    now_m = time.monotonic()
    last = float(getattr(system, LAST_FLUSH_ATTR, 0.0) or 0.0)
    if not force and last > 0 and now_m - last < debounce_sec():
        return False
    if not force and bool(getattr(system, INFLIGHT_ATTR, False)):
        return False
    payload = serialize_ring(system, now=now)
    try:
        setattr(system, RING_DIRTY_ATTR, False)
        setattr(system, LAST_FLUSH_ATTR, now_m)
    except Exception:
        pass
    if sync:
        return _write(system, payload)

    def _run() -> None:
        try:
            _write(system, payload)
        finally:
            try:
                setattr(system, INFLIGHT_ATTR, False)
            except Exception:
                pass

    try:
        setattr(system, INFLIGHT_ATTR, True)
        threading.Thread(target=_run, name="chain_ring_persist", daemon=True).start()
    except Exception:
        setattr(system, INFLIGHT_ATTR, False)
        return _write(system, payload)
    return True


def _load_payload(system: Any, date_key: str) -> Optional[Dict[str, Any]]:
    p = local_path(date_key)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("CHAIN_RING_PERSIST | action=local_read_failed | err=%s", exc)
    g = _gcs(system)
    if g is None:
        return None
    try:
        from modules.runtime_mode import gcs_read_daily_markers_string

        raw = gcs_read_daily_markers_string(g, relative_path(date_key))
        return json.loads(raw) if raw else None
    except Exception as exc:
        log.debug("CHAIN_RING_PERSIST | action=gcs_read_failed | err=%s", exc)
        return None


def hydrate_chain_ring(system: Any, *, now: Optional[float] = None) -> int:
    """Reload today's ring after a restart. Returns samples restored."""
    if system is None or not persist_enabled():
        return 0
    from central_tech.chain_ring import _CHAIN_RING_MAX, _ensure_chain_skew_ring, _f, ring_ttl_sec

    now_s = float(now if now is not None else time.time())
    date_key = trading_date_key(now_s)
    payload = _load_payload(system, date_key)
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or payload.get("date") != date_key:
        return 0
    fields = list(payload.get("fields") or FIELDS)
    ttl = ring_ttl_sec()
    ring = _ensure_chain_skew_ring(system)
    n = 0
    for sym, rows in (payload.get("symbols") or {}).items():
        buf = ring.setdefault(str(sym).upper(), [])
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, list) or len(r) != len(fields):
                continue
            s = dict(zip(fields, r))
            t = _f(s.get("t"))
            if t is None or now_s - t > ttl or t > now_s + 1.0:
                continue
            c, p = _f(s.get("call_chain_volume")), _f(s.get("put_chain_volume"))
            # R6 on reload: an observation already in the live ring is not added twice.
            if any(_f(x.get("call_chain_volume")) == c and _f(x.get("put_chain_volume")) == p for x in buf):
                continue
            buf.append({
                "t": t,
                "call_chain_volume": c,
                "put_chain_volume": p,
                "direction_volume_pct": None,
                "representative_volume": _f(s.get("representative_volume")),
                "cumulative_volume": _f(s.get("cumulative_volume")),
                "open_interest": _f(s.get("open_interest")),
                "restored": True,
            })
            n += 1
        buf.sort(key=lambda x: float(x.get("t") or 0.0))
        if len(buf) > _CHAIN_RING_MAX:
            del buf[: len(buf) - _CHAIN_RING_MAX]
    log.info(
        "CHAIN_RING_PERSIST | action=hydrated | date=%s | samples=%d | grep=CHAIN_RING_PERSIST", date_key, n
    )
    return n
