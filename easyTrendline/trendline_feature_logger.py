#!/usr/bin/env python3
"""
Lightweight JSONL feature logger for trendline candidates/trades.

Bounded via modules.telemetry_jsonl_utils (TELEMETRY_JSONL_MAX_BYTES / TAIL_LINES).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)


class TrendlineFeatureLogger:
    """Append-only JSONL logger with size caps (non-fatal on write failure)."""

    def __init__(self, base_dir: str = "data/trendline_optimizer") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / "trendline_features.jsonl"

    def log_event(self, payload: Dict[str, Any]) -> None:
        """Write one normalized event row; failures are non-fatal."""
        row = dict(payload)
        row.setdefault("logged_at_utc", datetime.utcnow().isoformat())
        try:
            from modules.telemetry_jsonl_utils import safe_jsonl_append

            safe_jsonl_append(self.path, row)
        except Exception as exc:
            log.warning("TRENDLINE_PIPELINE | stage=telemetry | action=write_failed | reason=%s", exc)
