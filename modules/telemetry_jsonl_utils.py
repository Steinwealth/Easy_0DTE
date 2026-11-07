"""JSONL append, tail read, and size-bound trimming (Kalshibot-style bounds for ORB)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(float(str(raw).strip())))
    except ValueError:
        return default


def telemetry_jsonl_max_bytes() -> int:
    """Per-file size cap before in-process tail trim (default 8 MiB on Cloud Run)."""
    default = 8 * 1024 * 1024 if os.getenv("K_SERVICE") else 32 * 1024 * 1024
    return _env_int("TELEMETRY_JSONL_MAX_BYTES", default)


def telemetry_jsonl_tail_lines() -> int:
    return _env_int("TELEMETRY_JSONL_TAIL_LINES", 10_000)


def load_jsonl_tail(path: Path, max_lines: int) -> list[dict[str, Any]]:
    if not path.is_file() or max_lines <= 0:
        return []
    buf: deque[dict[str, Any]] = deque(maxlen=max_lines)
    try:
        with path.open(encoding="utf-8", errors="replace") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    buf.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.warning("JSONL tail read failed (%s): %s", path.name, exc)
        return []
    return list(buf)


def trim_jsonl_to_tail(path: Path, *, max_lines: Optional[int] = None) -> bool:
    if not path.is_file():
        return False
    keep = max_lines if max_lines is not None else telemetry_jsonl_tail_lines()
    if keep <= 0:
        return False
    rows = load_jsonl_tail(path, keep)
    if not rows:
        return False
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            for row in rows:
                out.write(json.dumps(row, default=str) + "\n")
        os.replace(tmp_name, path)
        return True
    except OSError as exc:
        logger.warning("JSONL trim failed (%s): %s", path.name, exc)
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
        return False


def maybe_trim_jsonl_if_oversized(path: Path) -> bool:
    max_bytes = telemetry_jsonl_max_bytes()
    if max_bytes <= 0 or not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False
    logger.warning(
        "Telemetry JSONL oversized: %s %.1f MiB > %d MiB — trimming to last %d lines",
        path.name,
        size / (1024 * 1024),
        max_bytes // (1024 * 1024),
        telemetry_jsonl_tail_lines(),
    )
    return trim_jsonl_to_tail(path)


def safe_jsonl_append(path: Path, record: dict[str, Any]) -> None:
    """Append one JSONL line; trim if oversized; never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            maybe_trim_jsonl_if_oversized(path)
        row = dict(record)
        row.setdefault("ts", datetime.utcnow().isoformat() + "Z")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:
        logger.warning("JSONL write failed (%s): %s", path.name, exc)


def trim_oversized_telemetry_on_startup(base_dirs: Optional[list[Path]] = None) -> None:
    """Trim known JSONL paths before heavy reads (Cloud Run startup)."""
    root = Path(__file__).resolve().parent.parent
    paths = [
        root / "data" / "trendline_optimizer" / "trendline_features.jsonl",
    ]
    if base_dirs:
        for d in base_dirs:
            paths.append(Path(d) / "trendline_features.jsonl")
    for path in paths:
        if path.is_file():
            maybe_trim_jsonl_if_oversized(path)
