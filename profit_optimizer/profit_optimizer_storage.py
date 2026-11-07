#!/usr/bin/env python3
"""Persist daily profit_optimizer datasets (merge on write — never blind overwrite)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from profit_optimizer.path_classifier import enrich_trade_path_class, group_trades_by_path

log = logging.getLogger(__name__)

STRATEGY_ROOT = Path(__file__).resolve().parent.parent
PROFIT_OPTIMIZER_ROOT = Path(__file__).resolve().parent
DEFAULT_ANALYTICS_DIR = STRATEGY_ROOT / "analytics" / "profit_optimizer"
DEFAULT_RETRIEVED_ROOT = PROFIT_OPTIMIZER_ROOT / "retrieved_data" / "daily"
# Legacy flat prefixes (pre dual-app isolation). New writes use research_app/|final_app/.
GCS_DAILY_PREFIX = "profit_optimizer/daily/"
GCS_COHORT_PREFIX = "profit_optimizer/cohorts/"


def analytics_dir(custom: Optional[str] = None) -> Path:
    if custom and str(custom).strip():
        p = Path(str(custom).strip())
        return p if p.is_absolute() else STRATEGY_ROOT / p
    return DEFAULT_ANALYTICS_DIR


def default_retrieved_session_dir() -> Path:
    return DEFAULT_RETRIEVED_ROOT


def retrieved_session_dir(session_date: str, output_root: Optional[Path] = None) -> Path:
    root = output_root or DEFAULT_RETRIEVED_ROOT
    return root / session_date


def _runtime_gcs_namespace() -> str:
    try:
        from modules.runtime_mode import gcs_namespace

        return str(gcs_namespace() or "research_app").strip() or "research_app"
    except Exception:
        return "research_app"


def gcs_daily_json_path(session_date: str, *, namespace: Optional[str] = None) -> str:
    """Canonical daily JSON path for the running app (namespaced)."""
    try:
        from modules.runtime_mode import gcs_profit_optimizer_daily_object

        if not namespace:
            return gcs_profit_optimizer_daily_object(session_date)
    except Exception:
        pass
    ns = (namespace or _runtime_gcs_namespace()).strip().strip("/")
    return f"{ns}/profit_optimizer/daily/{session_date}.json"


def gcs_cohort_path(session_date: str, *, namespace: Optional[str] = None) -> str:
    try:
        from modules.runtime_mode import gcs_profit_optimizer_cohort_object

        if not namespace:
            return gcs_profit_optimizer_cohort_object(session_date)
    except Exception:
        pass
    ns = (namespace or _runtime_gcs_namespace()).strip().strip("/")
    return f"{ns}/profit_optimizer/cohorts/{session_date}_profit_optimizer_cohort.csv"


def gcs_daily_json_path_legacy(session_date: str) -> str:
    return f"{GCS_DAILY_PREFIX}{session_date}.json"


def gcs_profit_optimizer_list_prefixes() -> Tuple[str, ...]:
    try:
        from modules.runtime_mode import gcs_profit_optimizer_list_prefixes as _list

        return tuple(_list())
    except Exception:
        return (
            "research_app/profit_optimizer/daily/",
            "final_app/profit_optimizer/daily/",
            GCS_DAILY_PREFIX,
        )


def json_compact_storage() -> bool:
    """Compact JSON on runtime disk + GCS (retrieve script re-pretty-prints locally)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_JSON_COMPACT", "true")
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return True


def serialize_daily_payload(payload: Dict[str, Any]) -> str:
    if json_compact_storage():
        return json.dumps(payload, separators=(",", ":"), default=str)
    return json.dumps(payload, indent=2, default=str)


def list_gcs_session_dates(gcs: Any) -> List[str]:
    dates: List[str] = []
    for prefix in gcs_profit_optimizer_list_prefixes():
        try:
            paths = gcs.list_files(prefix=prefix)
        except Exception:
            continue
        for path in paths:
            name = path.split("/")[-1]
            if not name.endswith(".json"):
                continue
            date_str = name.replace(".json", "")
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
                dates.append(date_str)
            except ValueError:
                continue
    return sorted(set(dates))


def daily_file_path(session_date: str, base_dir: Optional[Path] = None) -> Path:
    root = base_dir or analytics_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{session_date}.json"


def _trade_key(trade: Dict[str, Any]) -> str:
    """Composite key so RESEARCH/FINAL demo trade_ids never merge across apps."""
    from profit_optimizer.app_identity import trade_app_label

    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    tid = str(entry.get("trade_id") or trade.get("trade_id") or "").strip()
    if not tid:
        return ""
    app = trade_app_label(trade)
    return f"{app}::{tid}" if app else tid


def _sort_samples(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(samples, key=lambda r: str(r.get("timestamp") or ""))


def _dedupe_samples(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for row in _sort_samples(samples):
        tick = str(row.get("monitor_tick_id") or "")
        ts = str(row.get("timestamp") or "")
        phase = str(row.get("path_phase") or "")
        app = str(row.get("source_app") or row.get("app") or "")
        key = f"{tick}|{phase}|{app}" if tick else f"{ts}|{phase}|{app}"
        if not tick and not ts:
            continue
        prior = seen.get(key)
        if prior is None:
            seen[key] = row
            continue
        # Keep row with more populated quote fields.
        def _score(d: Dict[str, Any]) -> int:
            return sum(1 for k in ("mark", "mid", "bid", "ask") if d.get(k) is not None)

        if _score(row) >= _score(prior):
            seen[key] = row
    return _sort_samples(list(seen.values()))


def persist_path_samples() -> int:
    """Deprecated stride cap. 2s JSONL is unbounded except the RAM safety ceiling."""
    from profit_optimizer.premium_tracker import max_path_samples

    return max_path_samples()


def max_daily_json_bytes() -> int:
    """Reject / recap daily JSON above this UTF-8 size (Cloud Run RAM + GIL)."""
    try:
        from modules.config_loader import get_config_value

        n = int(float(get_config_value("PROFIT_OPTIMIZER_MAX_DAILY_JSON_BYTES", "32000000") or 32000000))
    except Exception:
        n = 32_000_000
    return max(1_000_000, min(32_000_000, n))


def max_hydrate_bytes() -> int:
    """Skip json.loads of local/GCS daily files larger than this on startup."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_MAX_HYDRATE_BYTES", "0")
        n = int(float(str(raw or "0").strip() or 0))
    except Exception:
        n = 0
    if n <= 0:
        n = int(max_daily_json_bytes() * 1.25)
    return max(1_000_000, min(40_000_000, n))


def cap_trade_path_list(rows: List[Dict[str, Any]], *, max_n: Optional[int] = None) -> List[Dict[str, Any]]:
    from profit_optimizer.premium_tracker import cap_sample_path

    if not isinstance(rows, list) or not rows:
        return list(rows or [])
    if max_n is None:
        return list(rows)
    return cap_sample_path(rows, max_n=max_n)


def cap_trade_record_paths(trade: Dict[str, Any], *, max_n: Optional[int] = None) -> Dict[str, Any]:
    if not isinstance(trade, dict):
        return trade
    for key in ("premium_path", "underlying_path"):
        rows = trade.get(key)
        if isinstance(rows, list) and rows:
            trade[key] = cap_trade_path_list(rows, max_n=max_n)
    return trade


def _merge_path_lists(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _dedupe_samples((a or []) + (b or []))


_STATUS_RANK = {
    "open": 0,
    "shadow": 1,
    "closed": 2,
    "eod_open": 2,
}


def _prefer_status(prior_status: Any, incoming_status: Any) -> Optional[str]:
    """Advance open -> shadow -> closed/eod_open; never regress."""
    inc = str(incoming_status or "").strip().lower()
    pri = str(prior_status or "").strip().lower()
    if not inc:
        return pri or None
    if not pri:
        return inc
    if _STATUS_RANK.get(inc, -1) >= _STATUS_RANK.get(pri, -1):
        return inc
    return pri


def _merge_trade_record(prior: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(prior)
    for key in ("premium_path", "underlying_path"):
        merged[key] = cap_trade_path_list(
            _merge_path_lists(
                prior.get(key) if isinstance(prior.get(key), list) else [],
                incoming.get(key) if isinstance(incoming.get(key), list) else [],
            )
        )
    preferred = _prefer_status(prior.get("status"), incoming.get("status"))
    if preferred:
        merged["status"] = preferred
    if incoming.get("closed_at"):
        merged["closed_at"] = incoming["closed_at"]
    for key in (
        "premium_metrics",
        "premium_metrics_at_exit",
        "underlying_metrics",
        "peak_quote_metadata",
        "exit_observation",
    ):
        inc = incoming.get(key)
        if isinstance(inc, dict) and inc:
            merged[key] = inc
        elif key not in merged:
            merged[key] = prior.get(key)
    if incoming.get("shadow_tracking") is not None:
        merged["shadow_tracking"] = bool(incoming.get("shadow_tracking"))
    pe = prior.get("path_audit") if isinstance(prior.get("path_audit"), dict) else {}
    ie = incoming.get("path_audit") if isinstance(incoming.get("path_audit"), dict) else {}
    if pe or ie:
        merged_pa = {**pe, **ie}
        pg = list(pe.get("shadow_gaps") or []) + list(ie.get("shadow_gaps") or [])
        if pg:
            merged_pa["shadow_gaps"] = pg[-200:]
        merged["path_audit"] = merged_pa
    # Merge entry sub-documents without dropping prior fields.
    pe = prior.get("entry") if isinstance(prior.get("entry"), dict) else {}
    ie = incoming.get("entry") if isinstance(incoming.get("entry"), dict) else {}
    entry_merged = {**pe, **ie}
    for nested in ("contract_identity", "selector_metadata", "entry_execution_quality", "extra"):
        pn = pe.get(nested) if isinstance(pe.get(nested), dict) else {}
        inn = ie.get(nested) if isinstance(ie.get(nested), dict) else {}
        if pn or inn:
            entry_merged[nested] = {**pn, **inn}
    merged["entry"] = entry_merged
    return merged


def merge_trade_lists(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for t in existing:
        k = _trade_key(t)
        if k:
            by_id[k] = t
    for t in incoming:
        k = _trade_key(t)
        if not k:
            continue
        prior = by_id.get(k)
        if prior is None:
            by_id[k] = t
            continue
        by_id[k] = _merge_trade_record(prior, t)
    return sorted(by_id.values(), key=lambda x: str((x.get("entry") or {}).get("entry_time") or ""))


def _empty_daily(session_date: str) -> Dict[str, Any]:
    return {
        "date": session_date,
        "trades": [],
        "trades_by_path": {
            p: [] for p in ("orb_so", "orb_0dte", "trendline_0dte", "breakout_0dte")
        },
        "summary": {},
        "summary_by_path": {},
        "write_history": [],
    }


def _parse_daily_json(raw: str, session_date: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except Exception:
        return _empty_daily(session_date)
    if not isinstance(data, dict):
        return _empty_daily(session_date)
    data.setdefault("date", session_date)
    data.setdefault("trades", [])
    data.setdefault("trades_by_path", {})
    data.setdefault("summary", {})
    data.setdefault("summary_by_path", {})
    data.setdefault("write_history", [])
    return data


def _attach_jsonl_paths(
    session_date: str,
    data: Dict[str, Any],
    *,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    try:
        from profit_optimizer.path_jsonl import attach_jsonl_paths
    except Exception:
        return data
    trades = data.get("trades") if isinstance(data.get("trades"), list) else []
    for trade in trades:
        if isinstance(trade, dict):
            attach_jsonl_paths(session_date, trade, base_dir=base_dir)
    return data


def build_trades_by_path_payload(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped = group_trades_by_path(trades)
    return {k: [enrich_trade_path_class(t) for t in v] for k, v in grouped.items()}


def _hydrate_skip_payload(session_date: str, *, size_bytes: int, source: str) -> Dict[str, Any]:
    data = _empty_daily(session_date)
    data["_hydrate_skipped_oversize"] = True
    data["_hydrate_skipped_bytes"] = int(size_bytes)
    data["_hydrate_skipped_source"] = str(source or "")
    log.warning(
        "PROFIT_OPTIMIZER_STORAGE | action=hydrate_skipped | reason=payload_too_large | "
        "source=%s | bytes=%d | cap=%d",
        source,
        int(size_bytes),
        max_hydrate_bytes(),
    )
    return data


def load_daily_dataset(session_date: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = daily_file_path(session_date, base_dir)
    if not path.exists():
        return _empty_daily(session_date)
    try:
        size = int(path.stat().st_size)
        if size > max_hydrate_bytes():
            return _hydrate_skip_payload(session_date, size_bytes=size, source=str(path))
        with path.open(encoding="utf-8") as fp:
            return _parse_daily_json(fp.read(), session_date)
    except Exception as exc:
        log.warning("PROFIT_OPTIMIZER_STORAGE | action=load_failed | path=%s | error=%s", path, exc)
        return _empty_daily(session_date)


def _get_gcs_persistence_client() -> Any:
    from modules.gcs_persistence import get_gcs_persistence

    return get_gcs_persistence()


def load_daily_dataset_from_gcs(session_date: str) -> Dict[str, Any]:
    """Load canonical daily JSON from GCS when local runtime disk is empty (cold start)."""
    if not profit_optimizer_gcs_enabled():
        return _empty_daily(session_date)
    try:
        gcs = _get_gcs_persistence_client()
    except Exception as exc:
        log.debug("PROFIT_OPTIMIZER_STORAGE | gcs_load_skipped | reason=%s", exc)
        return _empty_daily(session_date)
    if not gcs.enabled:
        return _empty_daily(session_date)
    candidates = [gcs_daily_json_path(session_date), gcs_daily_json_path_legacy(session_date)]
    raw = None
    used = None
    hydrate_cap = max_hydrate_bytes()
    size_fn = getattr(gcs, "object_size_bytes", None)
    for path in candidates:
        if callable(size_fn):
            try:
                sz = size_fn(path)
            except Exception:
                sz = None
            if sz is not None and int(sz) > hydrate_cap:
                return _hydrate_skip_payload(session_date, size_bytes=int(sz), source=str(path))
        raw = gcs.read_string(path)
        if raw:
            used = path
            break
    if not raw:
        return _empty_daily(session_date)
    if len(raw.encode("utf-8")) > hydrate_cap:
        return _hydrate_skip_payload(
            session_date, size_bytes=len(raw.encode("utf-8")), source=str(used or "")
        )
    data = _parse_daily_json(raw, session_date)
    # Drop foreign-app rows if a mixed legacy blob is hydrated.
    try:
        from profit_optimizer.app_identity import resolve_app_identity, trade_app_label

        own = resolve_app_identity()["source_app"]
        trades = data.get("trades") if isinstance(data.get("trades"), list) else []
        filtered = []
        for t in trades:
            if not isinstance(t, dict):
                continue
            label = trade_app_label(t)
            if not label or label == own:
                filtered.append(t)
        data["trades"] = filtered
    except Exception:
        pass
    log.info(
        "PROFIT_OPTIMIZER_STORAGE | action=gcs_hydrated | date=%s | path=%s | trades=%d",
        session_date,
        used,
        len(data.get("trades") if isinstance(data.get("trades"), list) else []),
    )
    return data


def load_daily_dataset_durable(session_date: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Merge local runtime file with GCS checkpoint (survives scale-to-zero)."""
    local = load_daily_dataset(session_date, base_dir)
    if local.get("_hydrate_skipped_oversize"):
        # Local blob is already too large to parse. GCS size-check may still
        # allow a compact remote copy; never download a 100MB+ object here.
        remote = load_daily_dataset_from_gcs(session_date)
        if remote.get("_hydrate_skipped_oversize"):
            return remote
        remote_trades = remote.get("trades") if isinstance(remote.get("trades"), list) else []
        if remote_trades:
            return remote
        return local
    local_trades = local.get("trades") if isinstance(local.get("trades"), list) else []
    if local_trades:
        return local
    remote = load_daily_dataset_from_gcs(session_date)
    if remote.get("_hydrate_skipped_oversize"):
        return remote
    remote_trades = remote.get("trades") if isinstance(remote.get("trades"), list) else []
    if not remote_trades:
        return local
    merged_trades = merge_trade_lists([], remote_trades)
    return {
        "date": session_date,
        "trades": merged_trades,
        "summary": remote.get("summary") if isinstance(remote.get("summary"), dict) else {},
        "write_history": remote.get("write_history") if isinstance(remote.get("write_history"), list) else [],
    }


def persist_min_write_gap_sec() -> float:
    """Minimum seconds between whole-file PO daily writes (local + GCS)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_PERSIST_MIN_WRITE_GAP_SEC", "45")
        return max(0.0, float(str(raw).strip()))
    except Exception:
        return 45.0


def jsonl_persist_min_write_gap_sec() -> float:
    """Minimum seconds between per-trade JSONL appends (not the daily JSON rewrite)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_JSONL_MIN_WRITE_GAP_SEC", "5")
        return max(0.0, float(str(raw).strip()))
    except Exception:
        return 5.0


def checkpoint_interval_sec() -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_CHECKPOINT_SECONDS", "90")
        return max(0.0, float(str(raw).strip()))
    except Exception:
        return 90.0


def persist_lag_warn_sec() -> float:
    """Warn when in-memory PO rows have been unpersisted this long (seconds)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_PERSIST_LAG_WARN_SECONDS", "90")
        return max(15.0, float(str(raw).strip()))
    except Exception:
        return 90.0


def _note_includes_trades_by_path(run_note: str) -> bool:
    note = str(run_note or "").strip().lower()
    return note in {"eod", "shutdown", "finalize", "daily_finalize"} or note.endswith("finalized")


def _apply_path_caps(trades: List[Dict[str, Any]], *, max_n: int) -> None:
    """No-op. 2s paths are not stride-capped; JSONL is the size control."""
    return


def _shrink_paths_for_budget(trades: List[Dict[str, Any]], *, stage: str) -> None:
    """No-op. Never delete 2s research paths to fit a daily JSON budget."""
    return


def save_daily_dataset(
    session_date: str,
    trades: List[Dict[str, Any]],
    summary: Dict[str, Any],
    *,
    base_dir: Optional[Path] = None,
    run_note: str = "",
    summary_by_path: Optional[Dict[str, Any]] = None,
    sync_gcs: bool = True,
    prune_gcs: bool = False,
    merge_existing: bool = True,
    include_trades_by_path: Optional[bool] = None,
) -> Path:
    from profit_optimizer.app_identity import resolve_app_identity, stamp_trade_dict
    from profit_optimizer.trade_export import enrich_trade_export_dict

    path = daily_file_path(session_date, base_dir)
    stamped_incoming = [stamp_trade_dict(t) for t in trades if isinstance(t, dict)]
    existing_size = int(path.stat().st_size) if path.exists() else 0
    existing_oversize = existing_size > max_hydrate_bytes()
    history: List[Dict[str, Any]] = []
    existing: Dict[str, Any] = _empty_daily(session_date)

    if not stamped_incoming and existing_oversize:
        log.warning(
            "PROFIT_OPTIMIZER_STORAGE | action=save_skipped | reason=empty_incoming_oversize | "
            "path=%s | existing_bytes=%d",
            path,
            existing_size,
        )
        return path

    if merge_existing and not existing_oversize:
        existing = load_daily_dataset(session_date, base_dir)
        if existing.get("_hydrate_skipped_oversize"):
            existing_oversize = True
            existing = _empty_daily(session_date)

    if existing_oversize:
        # Memory is the live source of truth. Do not json.loads a 100MB+ blob
        # on the persist thread (GIL stall → Cloud Run TCP :8080 DEADLINE_EXCEEDED).
        merged_trades = stamped_incoming
        history = []
    elif merge_existing:
        merged_trades = merge_trade_lists(
            existing.get("trades") if isinstance(existing.get("trades"), list) else [],
            stamped_incoming,
        )
        history = existing.get("write_history") if isinstance(existing.get("write_history"), list) else []
    else:
        merged_trades = stamped_incoming
        history = []

    from profit_optimizer.path_jsonl import (
        compact_premium_row,
        compact_underlying_row,
        flush_trade_paths_jsonl,
        jsonl_covers_trade_paths,
        strip_paths_for_daily_json,
    )

    for trade in merged_trades:
        if isinstance(trade, dict):
            flush_trade_paths_jsonl(session_date, trade, base_dir=base_dir)
    # Re-enrich after merge so live/shadow tick counts and post_exit_* peaks
    # reflect the merged premium_path (early checkpoints must not freeze zeros).
    enriched = [enrich_trade_export_dict(t) for t in merged_trades]
    json_trades: List[Dict[str, Any]] = []
    for trade in enriched:
        if not isinstance(trade, dict):
            continue
        if jsonl_covers_trade_paths(session_date, trade, base_dir=base_dir):
            json_trades.append(strip_paths_for_daily_json(trade, session_date))
            continue
        kept = dict(trade)
        prem = kept.get("premium_path") if isinstance(kept.get("premium_path"), list) else []
        und = kept.get("underlying_path") if isinstance(kept.get("underlying_path"), list) else []
        kept["premium_path"] = [compact_premium_row(r) for r in prem if isinstance(r, dict)]
        kept["underlying_path"] = [compact_underlying_row(r) for r in und if isinstance(r, dict)]
        json_trades.append(kept)
        log.warning(
            "PROFIT_OPTIMIZER_STORAGE | action=kept_paths_in_daily_json | trade_id=%s | "
            "reason=jsonl_incomplete",
            kept.get("trade_id") or (kept.get("entry") or {}).get("trade_id"),
        )
    want_by_path = (
        bool(include_trades_by_path)
        if include_trades_by_path is not None
        else _note_includes_trades_by_path(run_note)
    )
    identity = resolve_app_identity()
    history.append(
        {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "incoming_trade_count": len(trades),
            "merged_trade_count": len(enriched),
            "note": run_note or "",
            **identity,
        }
    )

    budget = max_daily_json_bytes()
    trades_by_path: Dict[str, Any] = build_trades_by_path_payload(json_trades) if want_by_path else {}
    payload = {
        "date": session_date,
        **identity,
        "trades": json_trades,
        "trades_by_path": trades_by_path,
        "summary": summary,
        "summary_by_path": summary_by_path or {},
        "write_history": history[-50:],
        "path_store": "jsonl",
        "path_cadence_sec": 2,
    }
    body = serialize_daily_payload(payload)
    size = len(body.encode("utf-8"))
    if size > budget and trades_by_path:
        payload["trades_by_path"] = {}
        body = serialize_daily_payload(payload)
        size = len(body.encode("utf-8"))
        log.warning(
            "PROFIT_OPTIMIZER_STORAGE | action=dropped_trades_by_path | bytes=%d | cap=%d",
            size,
            budget,
        )
    if size > budget:
        log.error(
            "PROFIT_OPTIMIZER_STORAGE | action=payload_still_over_budget | bytes=%d | cap=%d | "
            "replay_source=jsonl | note=over-cap skips GCS daily JSON; JSONL is the replay source",
            size,
            budget,
        )

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    log.info(
        "PROFIT_OPTIMIZER_STORAGE | action=saved | path=%s | app=%s | trades=%d | bytes=%d",
        path,
        identity.get("source_app"),
        len(enriched),
        len(body.encode("utf-8")),
    )
    if sync_gcs:
        sync_daily_to_gcs(session_date, base_dir=base_dir, prune=prune_gcs)
    return path


def profit_optimizer_gcs_retention_days() -> int:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_GCS_RETENTION_DAYS", "2")
        return max(1, min(2, int(float(str(raw).strip()))))
    except Exception:
        return 2


def profit_optimizer_gcs_enabled() -> bool:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_GCS_SYNC", "true")
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return True


def _gcs_paths_for_session(session_date: str) -> Tuple[str, str]:
    return gcs_daily_json_path(session_date), gcs_cohort_path(session_date)


def sync_daily_to_gcs(
    session_date: str,
    *,
    base_dir: Optional[Path] = None,
    prune: bool = False,
    upload_daily_json: bool = True,
) -> None:
    """Upload JSONL paths + metrics daily JSON + cohort CSV (non-fatal on failure)."""
    if not profit_optimizer_gcs_enabled():
        return
    try:
        gcs = _get_gcs_persistence_client()
    except Exception as exc:
        log.debug("PROFIT_OPTIMIZER_STORAGE | gcs_sync_skipped | reason=%s", exc)
        return
    if not gcs.enabled:
        return

    json_local = daily_file_path(session_date, base_dir)
    cohort_local = (base_dir or analytics_dir()) / "cohorts" / f"{session_date}_profit_optimizer_cohort.csv"
    json_gcs, csv_gcs = _gcs_paths_for_session(session_date)

    try:
        from profit_optimizer.path_jsonl import upload_session_jsonl_to_gcs

        n_jsonl = upload_session_jsonl_to_gcs(gcs, session_date, base_dir=base_dir)
        if n_jsonl:
            log.info(
                "PROFIT_OPTIMIZER_STORAGE | action=gcs_jsonl_uploaded | date=%s | files=%d",
                session_date,
                n_jsonl,
            )
    except Exception as exc:
        log.warning("PROFIT_OPTIMIZER_STORAGE | action=gcs_jsonl_upload_error | error=%s", exc)

    if upload_daily_json and json_local.exists():
        local_bytes = int(json_local.stat().st_size)
        if local_bytes > max_daily_json_bytes():
            log.error(
                "PROFIT_OPTIMIZER_STORAGE | action=gcs_upload_skipped | reason=local_over_budget | "
                "path=%s | bytes=%d | cap=%d | replay_source=jsonl | "
                "note=over-cap skips GCS daily JSON; JSONL is the replay source",
                json_gcs,
                local_bytes,
                max_daily_json_bytes(),
            )
        else:
            ok = gcs.upload_file(str(json_local), json_gcs)
            if ok:
                log.info(
                    "PROFIT_OPTIMIZER_STORAGE | action=gcs_uploaded | path=%s | compact=%s | bytes=%d",
                    json_gcs,
                    json_compact_storage(),
                    local_bytes,
                )
    if cohort_local.exists():
        ok_csv = gcs.upload_file(str(cohort_local), csv_gcs)
        log.info(
            "PROFIT_OPTIMIZER_STORAGE | action=gcs_cohort_uploaded | path=%s | ok=%s",
            csv_gcs,
            str(bool(ok_csv)).lower(),
        )

    if prune:
        prune_profit_optimizer_gcs(gcs)


def prune_profit_optimizer_gcs(gcs: Any) -> None:
    """Delete profit_optimizer GCS objects older than PROFIT_OPTIMIZER_GCS_RETENTION_DAYS."""
    from datetime import date, datetime

    if not gcs or not gcs.enabled:
        return

    today = date.today()
    max_age = profit_optimizer_gcs_retention_days()
    prefixes = list(gcs_profit_optimizer_list_prefixes())
    for ns in ("research_app", "final_app"):
        prefixes.append(f"{ns}/profit_optimizer/cohorts/")
        prefixes.append(f"{ns}/profit_optimizer/paths/")
    prefixes.append(GCS_COHORT_PREFIX)
    prefixes.append("profit_optimizer/paths/")
    deleted = 0
    seen: set[str] = set()

    for prefix in prefixes:
        if prefix in seen:
            continue
        seen.add(prefix)
        for path in gcs.list_files(prefix=prefix):
            try:
                from modules.gcs_session_retention import parse_session_date_from_name

                file_date = parse_session_date_from_name(path)
            except Exception:
                file_date = None
            if file_date is None:
                name = path.split("/")[-1]
                date_str = name.split("_")[0].replace(".json", "")
                if "cohort" in name or prefix.endswith("cohorts/"):
                    date_str = name.split("_profit_optimizer")[0]
                try:
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
            if file_date > today or (today - file_date).days >= max_age:
                if gcs.delete_file(path):
                    deleted += 1

    if deleted:
        log.info(
            "PROFIT_OPTIMIZER_STORAGE | action=gcs_pruned | deleted=%s | retention_days=%s",
            deleted,
            max_age,
        )
