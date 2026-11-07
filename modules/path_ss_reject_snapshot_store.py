"""Persist path-SS reject-time feature snapshots for later synthetic win generation.

Goal: when a candidate is rejected by ``path_ss`` gates, capture an **entry-comparable**
PO feature payload (same densified schema as execution entry snapshots) plus a complete
``qg_admit`` key set so MissedProfits remine can use reject snaps side-by-side with
admitted PO entry snaps.

**Broker / cache policy (hard):**
  - Reject capture is **cache-only**. It densifies from the reject-time ``tech_row`` /
    ``qg_admit`` / metadata already paid for on the admit hot path (session 1m bars,
    discovery/path equity quotes, L1 ring, Layer C chain fields).
  - ``stamp_qg_admit_fields`` fills gaps from **cached** bars / quotes / L1 only —
    it does **not** fetch chains and does **not** call ``get_market_data_for_strategy``.
  - ``extract_path_features`` / densify are pure in-memory maps (no I/O).
  - Never call PO entry/exit hydrate (``_hydrate_po_snapshot_row_with_market_data``)
    from this module — that path may broker-fetch on cache miss and belongs only to
    executed fill/exit snapshots.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


ROOT_APP = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT_APP / "tmp" / "path_ss_reject_snapshots"
DEFAULT_GCS_PREFIX = "profit_optimizer/path_ss_reject_snapshots"


def _env_override_bool(name: str) -> Optional[bool]:
    """Return True/False if env var is set, otherwise None (no override)."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    norm = str(raw).strip().lower()
    if norm in {"0", "false", "no", "n", "off"}:
        return False
    if norm in {"1", "true", "yes", "y", "on"}:
        return True
    # Unknown value: default to False to avoid accidentally capturing.
    return False


def _capture_enabled(system: Any) -> bool:
    """
    Capture is enabled by:
    - explicit env var ``EASY_CAPTURE_PATH_SS_REJECT_SNAPSHOTS=1``, or
    - non-live mode (so local research / demo runs can record without extra setup).
    """
    override = _env_override_bool("EASY_CAPTURE_PATH_SS_REJECT_SNAPSHOTS")
    if override is not None:
        return override
    mode = str(getattr(system, "mode", "") or "").strip().lower()
    if not mode:
        # If we can't tell, be conservative and do not capture by default.
        return False
    return mode not in {"live", "production", "prod"}


def _safe(s: Any, *, max_len: int = 160) -> str:
    s = str(s or "")
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s[:max_len]


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # json default=str keeps us robust against datetime-like values.
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _maybe_upload_to_gcs(local_path: Path, gcs_object_name: str) -> None:
    """
    Best-effort upload of reject snapshots to GCS for post-day review.

    Runs in a daemon thread so discovery / admit reject paths never wait on
    network I/O (Path SS rejects can be numerous; keep the trading loop free).

    Controlled by:
      - ``EASY_CAPTURE_PATH_SS_REJECT_SNAPSHOTS_GCS``
        - if set: "0/false" disables, "1/true" enables
        - if unset: defaults ON when running under Cloud Run (K_SERVICE set)
    """
    raw = os.environ.get("EASY_CAPTURE_PATH_SS_REJECT_SNAPSHOTS_GCS")
    if raw is None:
        enabled = bool(os.environ.get("K_SERVICE"))
    else:
        enabled = bool(_env_override_bool("EASY_CAPTURE_PATH_SS_REJECT_SNAPSHOTS_GCS"))
    if not enabled:
        return

    def _upload() -> None:
        try:
            from modules.gcs_persistence import get_gcs_persistence
            from modules.runtime_mode import gcs_namespace

            ns = str(gcs_namespace() or "research_app").strip().strip("/") or "research_app"
            client = get_gcs_persistence()
            if not getattr(client, "enabled", False):
                return
            client.upload_file(str(local_path), f"{ns}/{gcs_object_name}")
        except Exception:
            # Do not disrupt trading/capture flow.
            return

    try:
        import threading

        threading.Thread(
            target=_upload,
            name="path-ss-reject-gcs",
            daemon=True,
        ).start()
    except Exception:
        _upload()


def _snapshot_strategy_for_source(source: str, tech_row: Optional[Dict[str, Any]] = None) -> str:
    s = str(source or "").upper()
    path = ""
    if isinstance(tech_row, dict):
        path = str(tech_row.get("path") or tech_row.get("snapshot_strategy") or "").lower()
    if "BREAKOUT" in s or "ORB2" in s or "breakout" in path:
        return "easy_breakout_0dte"
    if "TRENDLINE" in s or "TL" in s or "trendline" in path:
        return "easy_trendline_0dte"
    return "easy_orb_0dte"


# Short-name Path SS / PE-LS knife atoms (must be present on every reject snap).
# Keep in lockstep with ``_path_ss_fields`` in quality_gate_stack.py.
PATH_SS_ATOM_KEYS: tuple[str, ...] = (
    "vol",
    "vwap",
    "vwap_abs",
    "vwap_with",
    "seconds",
    "rsi",
    "iv",
    "dir",
    "bb",
    "l1d5",
    "l1raw",
    "sva",
    "break_atr",
    "pve1",
    "pve5",
    "otm",
    "delta",
    "body",
    "wick",
    "close",
)

# Path SS evaluate short names → PO entry / QG_ADMIT schema keys.
# Evaluate-time values are the source of truth for reject remine (same atoms
# the knife saw — must match what an entered trade's path_ss report carried).
PATH_SS_FIELD_TO_SCHEMA: Dict[str, str] = {
    "vol": "volume_ratio",
    "seconds": "seconds_since_break",
    "iv": "implied_volatility",
    "dir": "direction_volume_pct",
    "bb": "bollinger_position",
    "sva": "session_vs_avg_volume",
    "l1d5": "l1_delta_5s_with",
    "l1raw": "underlying_l1_imbalance_delta_5s",
    "vwap_abs": "vwap_distance_pct",  # abs distance; signed also set via vwap_with
    "vwap_with": "vwap_with",
    "rsi": "rsi",
    "break_atr": "break_distance_atr",
    "pve1": "price_volume_efficiency_1m",
    "pve5": "price_volume_efficiency_5m",
    "otm": "otm_pct",
    "delta": "chosen_delta",
    "body": "entry_bar_body_pct",
    "wick": "wick_against",
    "close": "close_with",
}


def densify_qg_admit(snap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Every ``QG_ADMIT_KEYS`` entry present (null when unavailable)."""
    from modules.qg_admit_snapshot import QG_ADMIT_KEYS, _qg_admit_json_value

    src = dict(snap) if isinstance(snap, dict) else {}
    out: Dict[str, Any] = {}
    for key in QG_ADMIT_KEYS:
        out[key] = _qg_admit_json_value(src.get(key))
    return out


def _fnum(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def densify_path_ss_atoms(
    *,
    qg_admit: Optional[Dict[str, Any]] = None,
    features: Optional[Dict[str, Any]] = None,
    tech_row: Optional[Dict[str, Any]] = None,
    path_ss_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Densified short-name knife atoms for PE/LS remine.

    Always emits every ``PATH_SS_ATOM_KEYS`` entry (null when unavailable).
    Prefer evaluate-time ``path_ss_fields`` (what the knife saw), then features /
    qg_admit / tech. Derives ``vwap_abs`` / ``vwap_with`` from distance when needed.
    """
    from modules.qg_admit_snapshot import _qg_admit_json_value

    layers: list[Dict[str, Any]] = []
    for src in (path_ss_fields, tech_row, features, qg_admit):
        if isinstance(src, dict):
            layers.append(src)

    def _get(*keys: str) -> Any:
        for layer in layers:
            for k in keys:
                if layer.get(k) is not None and layer.get(k) != "":
                    return layer.get(k)
        return None

    vol = _fnum(_get("volume_ratio", "vol"))
    vwap = _fnum(_get("vwap_distance_pct", "vwap", "vwap_abs"))
    vwap_with = _fnum(_get("vwap_with"))
    if vwap_with is None and vwap is not None:
        side = str(_get("side", "option_side", "direction") or "").upper()
        # If vwap came from vwap_abs (unsigned), keep CALL-positive convention.
        if _get("vwap_abs") is not None and _get("vwap_distance_pct") is None:
            vwap_with = float(vwap) if not side.startswith("P") else -float(vwap)
        else:
            vwap_with = -float(vwap) if side.startswith("P") else float(vwap)
    vwap_abs = _fnum(_get("vwap_abs"))
    if vwap_abs is None and vwap is not None:
        vwap_abs = abs(float(vwap))
    elif vwap_abs is None and vwap_with is not None:
        vwap_abs = abs(float(vwap_with))

    atoms_raw = {
        "vol": vol,
        "vwap": vwap if _get("vwap_distance_pct", "vwap") is not None else vwap_abs,
        "vwap_abs": vwap_abs,
        "vwap_with": vwap_with,
        "seconds": _fnum(_get("seconds_since_break", "seconds")),
        "rsi": _fnum(_get("rsi")),
        "iv": _fnum(_get("implied_volatility", "iv", "representative_iv")),
        "dir": _fnum(_get("direction_volume_pct", "dir_pct", "dir")),
        "bb": _fnum(_get("bollinger_position", "bb")),
        "l1d5": _fnum(_get("l1_delta_5s_with", "l1d5")),
        "l1raw": _fnum(_get("underlying_l1_imbalance_delta_5s", "l1raw")),
        "sva": _fnum(_get("session_vs_avg_volume", "session_vs_avg", "sva")),
        "break_atr": _fnum(_get("break_distance_atr", "break_atr")),
        "pve1": _fnum(_get("price_volume_efficiency_1m", "pve1")),
        "pve5": _fnum(_get("price_volume_efficiency_5m", "pve5")),
        "otm": _fnum(_get("otm_pct", "otm")),
        "delta": _fnum(_get("chosen_delta", "delta")),
        "body": _fnum(_get("entry_bar_body_pct", "body")),
        "wick": _fnum(_get("wick_against", "wick")),
        "close": _fnum(_get("close_with", "close")),
    }
    # Derive signed L1 delta when evaluate/admit only carried raw imbalance.
    if atoms_raw["l1d5"] is None and atoms_raw["l1raw"] is not None:
        side = str(_get("side", "option_side", "direction") or "").upper()
        raw = float(atoms_raw["l1raw"])
        atoms_raw["l1d5"] = -raw if side.startswith("P") else raw
    return {k: _qg_admit_json_value(atoms_raw.get(k)) for k in PATH_SS_ATOM_KEYS}


def path_ss_fields_from_report(report: Any) -> Optional[Dict[str, Any]]:
    """Extract Path SS evaluate fields from a GateReport / report dict."""
    if report is None:
        return None
    results = None
    if hasattr(report, "results"):
        results = getattr(report, "results", None)
    elif isinstance(report, dict):
        results = report.get("results")
    if not isinstance(results, list):
        return None
    for item in results:
        if isinstance(item, dict):
            if str(item.get("gate_id") or "") == "path_ss":
                fields = item.get("fields")
                return dict(fields) if isinstance(fields, dict) else None
            continue
        if getattr(item, "gate_id", None) == "path_ss":
            fields = getattr(item, "fields", None)
            return dict(fields) if isinstance(fields, dict) else None
    return None


def apply_path_ss_gate_fields(
    row: Dict[str, Any],
    path_ss_fields: Optional[Dict[str, Any]],
    *,
    overwrite: bool = True,
) -> int:
    """
    Map Path SS evaluate short-name fields onto PO schema keys.

    Evaluate-time atoms are what the knife used — prefer them over stale tech /
    cache stamps so reject snaps line up with entered-trade ``quality_gate_report``.
    """
    if not isinstance(row, dict) or not isinstance(path_ss_fields, dict):
        return 0
    n = 0
    for short, schema in PATH_SS_FIELD_TO_SCHEMA.items():
        if short not in path_ss_fields:
            continue
        val = path_ss_fields.get(short)
        if val is None or val == "":
            continue
        if short == "vwap_abs":
            # Prefer signed distance on schema; keep abs only when no vwap_with.
            if path_ss_fields.get("vwap_with") is not None:
                continue
            try:
                abs_v = abs(float(val))
            except (TypeError, ValueError):
                continue
            if overwrite or row.get("vwap_distance_pct") is None:
                row["vwap_distance_pct"] = abs_v
                n += 1
            continue
        if short == "vwap_with":
            if overwrite or row.get("vwap_with") is None:
                row["vwap_with"] = val
                n += 1
            # Reconstruct signed vwap_distance_pct (CALL=+with, PUT=-with).
            if overwrite or row.get("vwap_distance_pct") is None:
                try:
                    wv = float(val)
                    side = str(row.get("side") or row.get("option_side") or "").upper()
                    row["vwap_distance_pct"] = -wv if side.startswith("P") else wv
                    n += 1
                except (TypeError, ValueError):
                    pass
            continue
        if short == "l1d5":
            if overwrite or row.get("l1_delta_5s_with") is None:
                row["l1_delta_5s_with"] = val
                n += 1
            # Mirror onto PO schema L1 raw when evaluate didn't also stamp l1raw.
            if path_ss_fields.get("l1raw") is None and (
                overwrite or row.get("underlying_l1_imbalance_delta_5s") is None
            ):
                try:
                    wv = float(val)
                    side = str(row.get("side") or row.get("option_side") or "").upper()
                    row["underlying_l1_imbalance_delta_5s"] = (
                        -wv if side.startswith("P") else wv
                    )
                    n += 1
                except (TypeError, ValueError):
                    pass
            continue
        if overwrite or row.get(schema) is None:
            row[schema] = val
            n += 1
    # Aliases entry / qg_admit also carry
    if row.get("iv") is None and row.get("implied_volatility") is not None:
        row["iv"] = row["implied_volatility"]
        n += 1
    if row.get("delta") is None and row.get("chosen_delta") is not None:
        row["delta"] = row["chosen_delta"]
        n += 1
    if row.get("session_vs_avg") is None and row.get("session_vs_avg_volume") is not None:
        row["session_vs_avg"] = row["session_vs_avg_volume"]
        n += 1
    return n


def _prune_features_to_entry_schema(
    features: Dict[str, Any],
    *,
    snapshot_strategy: str,
) -> Dict[str, Any]:
    """Keep ``features_entry`` aligned with PO entry snaps (no knife aliases)."""
    if not isinstance(features, dict):
        return {}
    try:
        from modules.po_feature_schema import is_entry_excluded_field, schema_for_strategy

        allowed = {
            k
            for k in schema_for_strategy(snapshot_strategy)
            if not is_entry_excluded_field(k)
        }
    except Exception:
        return features
    return {k: features.get(k) for k in allowed}


def _gate_report_to_dict(report: Any) -> Optional[Dict[str, Any]]:
    if report is None:
        return None
    if isinstance(report, dict):
        return report
    to_dict = getattr(report, "to_dict", None)
    if callable(to_dict):
        try:
            out = to_dict()
            return out if isinstance(out, dict) else None
        except Exception:
            return None
    return None


def _merge_signal_row(
    *,
    symbol: str,
    side: str,
    slot: Optional[str],
    qg_admit: Optional[Dict[str, Any]],
    tech_row: Optional[Dict[str, Any]],
    extra: Optional[Dict[str, Any]],
    path_ss_fields: Optional[Dict[str, Any]] = None,
    gate_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a PO-signal-shaped row from whatever reject-time context we have."""
    row: Dict[str, Any] = {}
    if isinstance(tech_row, dict):
        row.update(tech_row)
    if isinstance(extra, dict):
        # Avoid nesting huge blobs into feature extract; only scalar-ish extras.
        for k, v in extra.items():
            if k in {"classification", "scope", "reason_tag", "reasons", "gate_report"}:
                continue
            if isinstance(v, (dict, list)):
                continue
            row.setdefault(k, v)
    if isinstance(qg_admit, dict):
        for k, v in qg_admit.items():
            if v is not None and row.get(k) is None:
                row[k] = v
        row["qg_admit"] = dict(qg_admit)
    # Path SS evaluate fields overwrite — same atoms an entered trade's report has.
    apply_path_ss_gate_fields(row, path_ss_fields, overwrite=True)
    if isinstance(gate_report, dict):
        row["quality_gate_report"] = gate_report
    row["symbol"] = symbol
    row["side"] = side
    row["option_side"] = side
    row["direction"] = side
    if slot:
        row.setdefault("trendline_slot", slot)
    # Common GATE_STACK aliases → schema keys
    if row.get("volume_ratio") is None and row.get("vol") is not None:
        row["volume_ratio"] = row.get("vol")
    if row.get("vwap_distance_pct") is None and row.get("vwap") is not None:
        try:
            row["vwap_distance_pct"] = float(row.get("vwap"))
        except (TypeError, ValueError):
            pass
    if row.get("direction_volume_pct") is None and row.get("dir_pct") is not None:
        row["direction_volume_pct"] = row.get("dir_pct")
    if row.get("direction_volume_pct") is None and row.get("dir") is not None:
        row["direction_volume_pct"] = row.get("dir")
    return row


def build_entry_comparable_features(
    system: Any,
    *,
    symbol: str,
    side: str,
    source: str,
    slot: Optional[str] = None,
    qg_admit: Optional[Dict[str, Any]] = None,
    tech_row: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
    path_ss_fields: Optional[Dict[str, Any]] = None,
    gate_report: Any = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Dict[str, Any]]:
    """
    Stamp admit fields and emit densified PO entry features + densified qg_admit.

    Returns ``(qg_admit_dense, features_entry_dense, snapshot_strategy, path_ss_atoms)``.

    When ``path_ss_fields`` / ``gate_report`` are provided (live reject), evaluate-time
    knife atoms are merged onto the row **before and after** cache stamp so reject
    snaps match entered-trade ``quality_gate_report.results[path_ss].fields``.
    """
    snapshot_strategy = _snapshot_strategy_for_source(source, tech_row)
    report_dict = _gate_report_to_dict(gate_report)
    if path_ss_fields is None and report_dict is not None:
        path_ss_fields = path_ss_fields_from_report(report_dict)
    row = _merge_signal_row(
        symbol=symbol,
        side=side,
        slot=slot,
        qg_admit=qg_admit,
        tech_row=tech_row,
        extra=extra,
        path_ss_fields=path_ss_fields,
        gate_report=report_dict,
    )
    # Match emit_handoff: freeze decision clock so trajectory uses closed bars.
    row["po_snapshot_stage"] = "path_ss_reject"
    row["stage"] = "path_ss_reject"
    try:
        from modules.po_observe_trajectory import mark_decision_freeze

        mark_decision_freeze(row)
    except Exception:
        pass

    try:
        from modules.qg_admit_snapshot import collect_qg_admit_snapshot
        from modules.session_tech_admit import stamp_admit_tech as stamp_qg_admit_fields

        stamp_qg_admit_fields(system, row, symbol=symbol)
        # Re-apply evaluate-time Path SS atoms after cache stamp (source of truth).
        apply_path_ss_gate_fields(row, path_ss_fields, overwrite=True)
        collected = collect_qg_admit_snapshot(row)
        if collected:
            # Prefer freshly stamped values; keep prior qg_admit as fill-ins.
            merged_qg = dict(qg_admit or {})
            merged_qg.update(collected)
            # Path SS evaluate atoms win on collision with cache (signed vwap, etc.).
            apply_path_ss_gate_fields(merged_qg, path_ss_fields, overwrite=True)
            qg_admit = merged_qg
            for k, v in collected.items():
                if v is not None and row.get(k) is None:
                    row[k] = v
            apply_path_ss_gate_fields(row, path_ss_fields, overwrite=True)
    except Exception:
        pass

    qg_dense = densify_qg_admit(qg_admit if qg_admit is not None else row)

    # Ensure densified qg keys also sit on the signal row for feature extract.
    for k, v in qg_dense.items():
        if v is not None and row.get(k) is None:
            row[k] = v
    apply_path_ss_gate_fields(row, path_ss_fields, overwrite=True)

    features_dense: Dict[str, Any] = {}
    try:
        from modules.execution_feature_snapshot import extract_path_features

        features_dense = extract_path_features(
            row,
            snapshot_strategy=snapshot_strategy,
            include_nulls=True,
            include_exit_fields=False,
        )
    except Exception:
        try:
            from modules.po_snapshot_hydration import densify_feature_map

            features_dense = densify_feature_map(
                row,
                snapshot_strategy=snapshot_strategy,
                include_exit_fields=False,
            )
        except Exception:
            features_dense = {}

    # Identity must always be filled when known.
    if isinstance(features_dense, dict):
        features_dense["symbol"] = symbol
        features_dense["side"] = side
        features_dense["direction"] = side
        if slot and features_dense.get("trendline_slot") is None:
            features_dense["trendline_slot"] = slot
        # Ensure knife schema keys from evaluate-time fields survive densify.
        apply_path_ss_gate_fields(features_dense, path_ss_fields, overwrite=True)
        features_dense = _prune_features_to_entry_schema(
            features_dense,
            snapshot_strategy=snapshot_strategy,
        )
        # Re-stamp identity after prune (always known).
        features_dense["symbol"] = symbol
        features_dense["side"] = side
        features_dense["direction"] = side
        if slot and features_dense.get("trendline_slot") is None:
            features_dense["trendline_slot"] = slot

    path_ss_atoms = densify_path_ss_atoms(
        qg_admit=qg_dense,
        features=features_dense if isinstance(features_dense, dict) else {},
        tech_row=row,
        path_ss_fields=path_ss_fields,
    )

    return qg_dense, features_dense, snapshot_strategy, path_ss_atoms


def capture_path_ss_reject_snapshot(
    system: Any,
    *,
    source: str,
    symbol: str,
    side: str,
    reason_code: str,
    trade_id: Optional[str] = None,
    slot: Optional[str] = None,
    qg_admit: Optional[Dict[str, Any]] = None,
    tech_row: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
    path_ss_fields: Optional[Dict[str, Any]] = None,
    gate_report: Any = None,
) -> Optional[Path]:
    """
    Write a Path SS reject snapshot that is **comparable to a PO entry snapshot**.

    Payload includes:
      - densified ``qg_admit`` (all ``QG_ADMIT_KEYS``)
      - densified ``features_entry`` / ``features_entry_dense`` (unified 0DTE schema)
      - densified ``path_ss_atoms`` (PE/LS short names: vol/vwap_abs/seconds/…)
      - ``quality_gate_report`` when provided (same shape as entered-trade snaps)
      - ``feature_schema_keys`` / ``schema_version`` / ``snapshot_strategy``

    **No broker API calls.** Values come from caller ``tech_row`` / ``qg_admit`` /
    Path SS evaluate fields plus cache-only stamping (bars, quote cache, L1).
    Missing keys stay null rather than triggering a live E*TRADE pull.

    Returns the written path, or None if capture is disabled / cannot build.
    """
    if not _capture_enabled(system):
        return None

    sym = str(symbol or "").upper().strip()
    sd = str(side or "").upper().strip()
    if not sym:
        return None
    if sd.startswith("C"):
        sd = "CALL"
    elif sd.startswith("P"):
        sd = "PUT"

    tech_row = dict(tech_row or {})
    extra = dict(extra or {})
    report_dict = _gate_report_to_dict(gate_report)
    if path_ss_fields is None:
        path_ss_fields = path_ss_fields_from_report(report_dict)
    if path_ss_fields is None and isinstance(extra.get("path_ss_fields"), dict):
        path_ss_fields = dict(extra.get("path_ss_fields") or {})

    qg_dense, features_dense, snapshot_strategy, path_ss_atoms = build_entry_comparable_features(
        system,
        symbol=sym,
        side=sd,
        source=source,
        slot=slot,
        qg_admit=qg_admit,
        tech_row=tech_row,
        extra=extra,
        path_ss_fields=path_ss_fields,
        gate_report=report_dict,
    )

    # Require at least identity + schema densify (even if all feature values null).
    if not isinstance(features_dense, dict) or not features_dense:
        return None

    try:
        from modules.po_feature_schema import (
            SCHEMA_VERSION,
            is_entry_excluded_field,
            schema_for_strategy,
        )

        schema_keys = [
            k for k in schema_for_strategy(snapshot_strategy) if not is_entry_excluded_field(k)
        ]
        schema_version = SCHEMA_VERSION
    except Exception:
        schema_keys = sorted(features_dense.keys())
        schema_version = 2

    ts = _utc_iso_now()
    day = ts[:10]
    rid = _safe(reason_code)
    tid = _safe(trade_id or "n/a", max_len=80)
    slot_s = _safe(slot or "", max_len=40)

    # Coverage telemetry for desk audits (not a schema field).
    qg_filled = sum(1 for v in qg_dense.values() if v is not None)
    feat_filled = sum(1 for v in features_dense.values() if v is not None)
    atoms_filled = sum(1 for v in path_ss_atoms.values() if v is not None)
    knife_keys = (
        "seconds_since_break",
        "implied_volatility",
        "volume_ratio",
        "direction_volume_pct",
        "session_vs_avg_volume",
        "bollinger_position",
        "rsi",
        "break_distance_atr",
        "underlying_l1_imbalance_delta_5s",
        "vwap_distance_pct",
        "price_volume_efficiency_1m",
    )
    knife_filled = sum(1 for k in knife_keys if features_dense.get(k) is not None)

    out = {
        "ts_utc": ts,
        "day": day,
        "source": source,
        "symbol": sym,
        "side": sd,
        "trade_id": trade_id,
        "trendline_slot": slot,
        "reason_code": reason_code,
        "reason_code_short": str(reason_code)[:180],
        "schema_version": schema_version,
        "snapshot_strategy": snapshot_strategy,
        "stage": "path_ss_reject",
        "feature_schema_keys": schema_keys,
        "qg_admit": qg_dense,
        "features_entry": features_dense,
        "features_entry_dense": features_dense,
        "path_ss_atoms": path_ss_atoms,
        "path_ss_fields": dict(path_ss_fields) if isinstance(path_ss_fields, dict) else None,
        "quality_gate_report": report_dict,
        "coverage": {
            "qg_admit_keys": len(qg_dense),
            "qg_admit_filled": qg_filled,
            "feature_schema_keys": len(schema_keys),
            "features_entry_filled": feat_filled,
            "path_ss_atom_keys": len(path_ss_atoms),
            "path_ss_atoms_filled": atoms_filled,
            "knife_schema_keys": len(knife_keys),
            "knife_schema_filled": knife_filled,
            "has_path_ss_fields": bool(path_ss_fields),
            "has_quality_gate_report": bool(report_dict),
            "entry_comparable": bool(
                path_ss_fields and knife_filled >= 6 and atoms_filled >= 6
            ),
        },
        "extra": extra,
    }

    out_dir = DEFAULT_OUT_DIR / day
    fname_parts = [source, sym, sd]
    if slot_s:
        fname_parts.append(slot_s)
    fname_parts.extend([tid, rid])
    fname = "__".join(_safe(p, max_len=80) for p in fname_parts if p) + ".json"
    path = out_dir / fname
    _write_json(path, out)

    # Optional: also push to GCS so day-end collectors can fetch/review later.
    try:
        _maybe_upload_to_gcs(
            path,
            f"{DEFAULT_GCS_PREFIX}/{day}/{fname}",
        )
    except Exception:
        pass
    return path
