#!/usr/bin/env python3
"""Append-only 2s premium/underlying paths (JSONL).

Daily JSON must not hold full paths — that is what GIL-stalled Cloud Run.
Each profitable (and still-open) trade gets one local JSONL file at 2s cadence.
GCS mirrors the same objects. Retention is the 2-day session prune.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_LINE_COUNTS: Dict[str, int] = {}
_LINE_COUNT_LOCK = threading.Lock()

log = logging.getLogger(__name__)

_SAFE_TID = re.compile(r"[^A-Za-z0-9._-]+")

# Exit-tuning row. Omit greeks/chain/MACD blobs that bloated the 327 MiB JSON.
PREMIUM_JSONL_KEYS = (
    "timestamp",
    "path_phase",
    "trade_id",
    "contract_symbol",
    "strike",
    "option_side",
    "direction",
    "mark",
    "bid",
    "ask",
    "mid",
    "last",
    "underlying_price",
    "underlying_source",
    "underlying_last",
    "underlying_age_ms",
    "underlying_observation_timestamp",
    "underlying_stale",
    "underlying_reused",
    "underlying_quote_age_sec",
    "entry_premium",
    "entry_underlying",
    "entry_underlying_price",
    "underlying_vs_entry_pct",
    "underlying_move_pct",
    "pnl_pct",
    "spread_width",
    "spread_pct",
    "bid_size",
    "ask_size",
    "option_l1_imbalance",
    "call_chain_volume_now",
    "call_chain_volume_30s_ago",
    "call_chain_volume_5m_ago",
    "put_chain_volume_now",
    "put_chain_volume_30s_ago",
    "put_chain_volume_5m_ago",
    "selected_contract_volume_now",
    "selected_contract_volume_30s_ago",
    "selected_contract_volume_5m_ago",
    "selected_call_contract_volume_now",
    "selected_call_contract_volume_30s_ago",
    "selected_call_contract_volume_5m_ago",
    "selected_put_contract_volume_now",
    "selected_put_contract_volume_30s_ago",
    "selected_put_contract_volume_5m_ago",
    "bid_size_now",
    "bid_size_30s_ago",
    "bid_size_5m_ago",
    "ask_size_now",
    "ask_size_30s_ago",
    "ask_size_5m_ago",
    "delta",
    "gamma",
    "theta",
    "iv",
    "rsi",
    "rsi_missing",
    "macd",
    "macd_signal",
    "macd_histogram",
    "macd_missing",
    "vwap",
    "vwap_distance_pct",
    "vwap_missing",
    "volume_1m",
    "volume_3m",
    "volume_ratio",
    "orb_volume_ratio",
    "volume_ratio_missing",
    "price_volume_efficiency_3m",
    "price_change_3m_pct",
    "pve3_bar_at",
    "session_tech_asof_at",
    "session_tech_observed_at",
    "evaluated",
    "eval_now",
    "eval_age_s",
    "exit_reason",
    "atr",
    "bollinger_width",
    "bollinger_position",
    "distance_from_vwap_atr",
    "otm_pct",
    "call_share",
    "put_share",
    "dir_pct_change_5m",
    "underlying_l1_imbalance",
    "session_tech_bar_at",
    "premium_source",
    "quote_source",
    "quote_quality",
    "mark_quality",
    "mark_is_exit_grade",
    "path_v2_peak",
    "path_pret",
    "path_v2_reason",
    "quote_age_ms",
    "contracts",
    "quantity",
    "opening_fees",
    "closing_fees",
    "round_trip_fees",
    "shadow_trust",
    "monitor_tick_id",
    "prem_vel_10",
    "hwm_premium",
    "hwm_timestamp",
    "mfe_pct",
    "drawdown_from_hwm_pct",
    "mfe_giveback_armed",
    "giveback_floor_premium",
    "seconds_since_entry",
    "seconds_since_hwm",
    "actual_exit_reason",
    "actual_exit_timestamp",
    "actual_exit_premium",
    "actual_exit_bid",
    "seconds_since_actual_exit",
    "shadow_hwm_since_exit",
    "live_hwm_at_exit",
    "quote_age_seconds",
    "freshness_status",
    "observation_timestamp",
    "broker_quote_timestamp",
    "source_app",
    "app",
)

UNDERLYING_JSONL_KEYS = ("timestamp", "underlying_price", "volume", "path_phase")


def safe_trade_id(trade_id: str) -> str:
    tid = str(trade_id or "").strip() or "unknown"
    return _SAFE_TID.sub("_", tid)[:180]


def _trade_id_from_trade(trade: Dict[str, Any]) -> str:
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    return str(entry.get("trade_id") or trade.get("trade_id") or "").strip()


def compact_premium_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(row, dict):
        return out
    for key in PREMIUM_JSONL_KEYS:
        val = row.get(key)
        if val is None or val == "":
            continue
        out[key] = val
    return out


def compact_underlying_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(row, dict):
        return out
    for key in UNDERLYING_JSONL_KEYS:
        val = row.get(key)
        if val is None or val == "":
            continue
        out[key] = val
    return out


def paths_dir(session_date: str, *, base_dir: Optional[Path] = None) -> Path:
    from profit_optimizer.profit_optimizer_storage import analytics_dir

    root = Path(base_dir) if base_dir else analytics_dir()
    return root / "paths" / str(session_date).strip()[:10]


def local_jsonl_path(
    session_date: str,
    trade_id: str,
    *,
    kind: str = "premium",
    base_dir: Optional[Path] = None,
) -> Path:
    suffix = "underlying" if kind == "underlying" else "premium"
    return paths_dir(session_date, base_dir=base_dir) / f"{safe_trade_id(trade_id)}.{suffix}.jsonl"


def gcs_paths_prefix(session_date: str, *, namespace: Optional[str] = None) -> str:
    try:
        from modules.runtime_mode import gcs_namespace

        ns = (namespace or gcs_namespace() or "research_app").strip().strip("/")
    except Exception:
        ns = (namespace or "research_app").strip().strip("/")
    return f"{ns}/profit_optimizer/paths/{str(session_date).strip()[:10]}/"


def gcs_jsonl_object(
    session_date: str,
    trade_id: str,
    *,
    kind: str = "premium",
    namespace: Optional[str] = None,
) -> str:
    suffix = "underlying" if kind == "underlying" else "premium"
    return f"{gcs_paths_prefix(session_date, namespace=namespace)}{safe_trade_id(trade_id)}.{suffix}.jsonl"


def _cache_key(path: Path) -> str:
    return str(path)


def _scan_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("rb") as fp:
        for _ in fp:
            n += 1
    return n


def invalidate_line_count(path: Optional[Path] = None) -> None:
    """Drop cached JSONL counts after a GCS download or file replace."""
    with _LINE_COUNT_LOCK:
        if path is None:
            _LINE_COUNTS.clear()
            return
        _LINE_COUNTS.pop(_cache_key(path), None)


def _line_count(path: Path) -> int:
    """On-disk JSONL rows; remember the last write so persist does not re-read the file."""
    key = _cache_key(path)
    with _LINE_COUNT_LOCK:
        cached = _LINE_COUNTS.get(key)
        if cached is not None:
            if not path.exists():
                _LINE_COUNTS[key] = 0
                return 0
            return int(cached)
    n = _scan_line_count(path)
    with _LINE_COUNT_LOCK:
        _LINE_COUNTS[key] = n
    return n


def append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    """Append new compact rows. Returns number of lines written."""
    payload = [json.dumps(row, separators=(",", ":"), default=str) for row in rows if row]
    if not payload:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _cache_key(path)
    have = _line_count(path)
    with path.open("a", encoding="utf-8") as fp:
        fp.write("\n".join(payload) + "\n")
    with _LINE_COUNT_LOCK:
        _LINE_COUNTS[key] = have + len(payload)
    return len(payload)


def replace_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    """Overwrite a JSONL file with compact rows. Returns number of lines written."""
    payload = [json.dumps(row, separators=(",", ":"), default=str) for row in rows if row]
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _cache_key(path)
    if not payload:
        path.write_text("", encoding="utf-8")
        with _LINE_COUNT_LOCK:
            _LINE_COUNTS[key] = 0
        return 0
    path.write_text("\n".join(payload) + "\n", encoding="utf-8")
    with _LINE_COUNT_LOCK:
        _LINE_COUNTS[key] = len(payload)
    return len(payload)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def flush_trade_paths_jsonl(
    session_date: str,
    trade: Dict[str, Any],
    *,
    base_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Write any in-memory path rows not yet on disk. Does not rewrite the file."""
    tid = _trade_id_from_trade(trade)
    if not tid:
        return {"premium": 0, "underlying": 0}
    prem_path = local_jsonl_path(session_date, tid, kind="premium", base_dir=base_dir)
    und_path = local_jsonl_path(session_date, tid, kind="underlying", base_dir=base_dir)
    prem_rows = [
        compact_premium_row(r)
        for r in (trade.get("premium_path") if isinstance(trade.get("premium_path"), list) else [])
        if isinstance(r, dict)
    ]
    und_rows = [
        compact_underlying_row(r)
        for r in (trade.get("underlying_path") if isinstance(trade.get("underlying_path"), list) else [])
        if isinstance(r, dict)
    ]
    prem_have = _line_count(prem_path)
    und_have = _line_count(und_path)
    wrote_p = append_jsonl(prem_path, prem_rows[prem_have:])
    wrote_u = append_jsonl(und_path, und_rows[und_have:])
    if wrote_p or wrote_u:
        log.debug(
            "PO_PATH_JSONL | trade_id=%s | premium_new=%d | underlying_new=%d | premium_total=%d",
            tid,
            wrote_p,
            wrote_u,
            prem_have + wrote_p,
        )
    return {"premium": prem_have + wrote_p, "underlying": und_have + wrote_u}


def jsonl_covers_trade_paths(
    session_date: str,
    trade: Dict[str, Any],
    *,
    base_dir: Optional[Path] = None,
) -> bool:
    """True when on-disk JSONL has at least as many rows as in-memory paths.

    Persist must not strip premium_path from daily JSON unless leftover/live
    ticks are already on JSONL — otherwise capture review loses the tape.
    """
    tid = _trade_id_from_trade(trade)
    if not tid:
        return True
    prem = trade.get("premium_path") if isinstance(trade.get("premium_path"), list) else []
    und = trade.get("underlying_path") if isinstance(trade.get("underlying_path"), list) else []
    if prem and _line_count(local_jsonl_path(session_date, tid, kind="premium", base_dir=base_dir)) < len(
        [r for r in prem if isinstance(r, dict)]
    ):
        return False
    if und and _line_count(local_jsonl_path(session_date, tid, kind="underlying", base_dir=base_dir)) < len(
        [r for r in und if isinstance(r, dict)]
    ):
        return False
    return True


def attach_jsonl_paths(
    session_date: str,
    trade: Dict[str, Any],
    *,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fill empty path lists from JSONL (daily JSON is metrics-only)."""
    tid = _trade_id_from_trade(trade)
    if not tid:
        return trade
    prem = trade.get("premium_path") if isinstance(trade.get("premium_path"), list) else []
    und = trade.get("underlying_path") if isinstance(trade.get("underlying_path"), list) else []
    if not prem:
        trade["premium_path"] = load_jsonl(
            local_jsonl_path(session_date, tid, kind="premium", base_dir=base_dir)
        )
    if not und:
        trade["underlying_path"] = load_jsonl(
            local_jsonl_path(session_date, tid, kind="underlying", base_dir=base_dir)
        )
    n_p = len(trade.get("premium_path") or [])
    n_u = len(trade.get("underlying_path") or [])
    if n_p or n_u:
        log.info(
            "PATH_V2_RESTORE | trade_id=%s | session=%s | premium_rows=%d | underlying_rows=%d",
            tid,
            str(session_date).strip()[:10],
            n_p,
            n_u,
        )
    return trade


def strip_paths_for_daily_json(trade: Dict[str, Any], session_date: str) -> Dict[str, Any]:
    """Keep metrics in daily JSON; point at JSONL for the 2s series."""
    out = dict(trade)
    tid = _trade_id_from_trade(out)
    prem = out.get("premium_path") if isinstance(out.get("premium_path"), list) else []
    und = out.get("underlying_path") if isinstance(out.get("underlying_path"), list) else []
    out["premium_path_samples"] = len(prem)
    out["underlying_path_samples"] = len(und)
    out["path_cadence_sec"] = 2
    if tid:
        out["path_jsonl_premium"] = str(
            local_jsonl_path(session_date, tid, kind="premium").name
        )
        out["path_jsonl_underlying"] = str(
            local_jsonl_path(session_date, tid, kind="underlying").name
        )
    out["premium_path"] = []
    out["underlying_path"] = []
    return out


def list_local_jsonl_files(session_date: str, *, base_dir: Optional[Path] = None) -> List[Path]:
    root = paths_dir(session_date, base_dir=base_dir)
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*.jsonl") if p.is_file())


def upload_session_jsonl_to_gcs(
    gcs: Any,
    session_date: str,
    *,
    base_dir: Optional[Path] = None,
    namespace: Optional[str] = None,
) -> int:
    """Upload local 2s JSONL files. Small append-only objects; not the daily JSON."""
    if not gcs or not getattr(gcs, "enabled", False):
        return 0
    uploaded = 0
    for path in list_local_jsonl_files(session_date, base_dir=base_dir):
        dest = f"{gcs_paths_prefix(session_date, namespace=namespace)}{path.name}"
        try:
            if gcs.upload_file(str(path), dest):
                uploaded += 1
        except Exception as exc:
            log.warning("PO_PATH_JSONL | action=gcs_upload_failed | dest=%s | error=%s", dest, exc)
    return uploaded


def download_session_jsonl_from_gcs(
    gcs: Any,
    session_date: str,
    *,
    base_dir: Optional[Path] = None,
    namespace: Optional[str] = None,
) -> int:
    """Pull JSONL for this session onto local disk before hydrate/retrieve attach."""
    if not gcs or not getattr(gcs, "enabled", False):
        return 0
    dest_dir = paths_dir(session_date, base_dir=base_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    namespaces = [namespace] if namespace else ["research_app", "final_app"]
    downloaded = 0
    seen: set[str] = set()
    for ns in namespaces:
        prefix = gcs_paths_prefix(session_date, namespace=ns)
        try:
            paths = gcs.list_files(prefix=prefix) or []
        except Exception as exc:
            log.debug("PO_PATH_JSONL | action=gcs_list_failed | prefix=%s | error=%s", prefix, exc)
            continue
        for gcs_path in paths:
            name = str(gcs_path).rsplit("/", 1)[-1]
            if not name.endswith(".jsonl") or name in seen:
                continue
            seen.add(name)
            local = dest_dir / name
            try:
                if gcs.download_file(str(gcs_path), str(local)):
                    invalidate_line_count(local)
                    downloaded += 1
            except Exception as exc:
                log.debug(
                    "PO_PATH_JSONL | action=gcs_download_failed | path=%s | error=%s",
                    gcs_path,
                    exc,
                )
    return downloaded
