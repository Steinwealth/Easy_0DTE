#!/usr/bin/env python3
"""App labeling for profit_optimizer (parity with priority_optimizer source_app / gcs_namespace)."""

from __future__ import annotations

from typing import Any, Dict, Optional


def resolve_app_identity() -> Dict[str, str]:
    """Return {source_app, app, gcs_namespace} for the running sibling app."""
    try:
        from modules.runtime_mode import gcs_app_label, gcs_namespace

        app = str(gcs_app_label() or "RESEARCH").strip().upper() or "RESEARCH"
        ns = str(gcs_namespace() or "research_app").strip() or "research_app"
    except Exception:
        app, ns = "RESEARCH", "research_app"
    if app not in ("RESEARCH", "FINAL"):
        app = "FINAL" if "final" in ns else "RESEARCH"
    return {"source_app": app, "app": app, "gcs_namespace": ns}


def stamp_mapping(target: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge app identity into a dict (creates one when target is None)."""
    out = dict(target or {})
    identity = resolve_app_identity()
    for key, value in identity.items():
        if not out.get(key):
            out[key] = value
    return out


def stamp_entry_snapshot(entry: Any) -> None:
    """Stamp TradeEntrySnapshot.extra + top-level attrs when present."""
    identity = resolve_app_identity()
    extra = dict(getattr(entry, "extra", None) or {})
    for key, value in identity.items():
        if not extra.get(key):
            extra[key] = value
    entry.extra = extra
    for key, value in identity.items():
        if hasattr(entry, key) and not getattr(entry, key, None):
            try:
                setattr(entry, key, value)
            except Exception:
                pass


def stamp_trade_dict(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure trade + entry carry source_app / app / gcs_namespace."""
    identity = resolve_app_identity()
    out = dict(trade)
    for key, value in identity.items():
        if not out.get(key):
            out[key] = value
    entry = dict(out.get("entry") or {}) if isinstance(out.get("entry"), dict) else {}
    for key, value in identity.items():
        if not entry.get(key):
            entry[key] = value
    extra = dict(entry.get("extra") or {}) if isinstance(entry.get("extra"), dict) else {}
    for key, value in identity.items():
        if not extra.get(key):
            extra[key] = value
    entry["extra"] = extra
    out["entry"] = entry
    return out


def stamp_path_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    """Label live/shadow premium_path samples with the owning app."""
    return stamp_mapping(sample)


def trade_app_label(trade: Dict[str, Any]) -> str:
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    raw = (
        trade.get("source_app")
        or trade.get("app")
        or entry.get("source_app")
        or entry.get("app")
        or extra.get("source_app")
        or extra.get("app")
        or ""
    )
    label = str(raw).strip().upper()
    return label if label in ("RESEARCH", "FINAL") else ""
