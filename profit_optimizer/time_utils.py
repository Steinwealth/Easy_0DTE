#!/usr/bin/env python3
"""UTC-safe timestamp normalization for profit_optimizer (naive DEMO entry times)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Union


def normalize_timestamp_iso(ts: Any) -> str:
    """Return UTC-aware ISO-8601 string; naive inputs are treated as UTC."""
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(ts, datetime):
        dt = ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    raw = str(ts).strip()
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()
