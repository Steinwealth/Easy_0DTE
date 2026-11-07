#!/usr/bin/env python3
"""
ORB1 Breakout execution filters — post-rank eligibility (NOT Portfolio Protection).

Runs after Hard Gate + priority rank in listen_to_orb_signals():
  • PS floor (0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION)
  • Re-rank survivors
  • Cap backfill (max_positions)

Portfolio Protection (3a/3b) is observe-only in RESEARCH + FINAL; these filters
always enforce when enabled regardless of PORTFOLIO_PROTECTION_BLOCK_EXECUTION.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    s = str(raw).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _skip_zero_priority_score_enabled() -> bool:
    if os.getenv("0DTE_SKIP_ZERO_PRIORITY_SCORE") is not None:
        return _env_bool("0DTE_SKIP_ZERO_PRIORITY_SCORE", True)
    return True


def _min_priority_score_floor() -> float:
    if os.getenv("0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION") is not None:
        return _env_float("0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION", 0.30)
    return 0.10


def get_signal_priority_score(signal: dict) -> float:
    for key in ("priority_score", "so_priority_score"):
        try:
            val = signal.get(key)
            if val is not None:
                return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def get_dte0_signal_priority_score(signal: Any) -> float:
    try:
        ps = float(getattr(signal, "priority_score", 0.0) or 0.0)
        if ps > 0.0:
            return ps
    except (TypeError, ValueError):
        pass
    orb = getattr(signal, "orb_signal", None)
    if isinstance(orb, dict):
        ps_dict = get_signal_priority_score(orb)
        if ps_dict > 0.0:
            return ps_dict
    if isinstance(signal, dict):
        return get_signal_priority_score(signal)
    return 0.0


@dataclass
class DTE0PriorityScoreExecutionFilterResult:
    kept: List[Any] = field(default_factory=list)
    dropped: List[Any] = field(default_factory=list)
    min_priority_score: float = 0.10
    enabled: bool = True

    @property
    def dropped_labels(self) -> List[str]:
        out: List[str] = []
        for s in self.dropped:
            sym = str(getattr(s, "symbol", "") or "").strip()
            side = str(getattr(s, "direction", "") or "").upper()
            if not sym:
                continue
            label = f"{sym}_{'PUT' if side == 'SHORT' else 'CALL'}"
            out.append(label)
        return out


def is_dte0_priority_score_eligible_for_execution(
    signal: Any,
    min_priority_score: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> bool:
    if enabled is None:
        enabled = _skip_zero_priority_score_enabled()
    if not enabled:
        return True
    floor = _min_priority_score_floor() if min_priority_score is None else float(min_priority_score)
    return get_dte0_signal_priority_score(signal) >= floor


def filter_dte0_signals_below_min_priority_score(
    signals: List[Any],
    min_priority_score: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> DTE0PriorityScoreExecutionFilterResult:
    """Drop ORB1 Breakout rows with priority_score below floor (post-rank execution filter)."""
    if enabled is None:
        enabled = _skip_zero_priority_score_enabled()
    floor = _min_priority_score_floor() if min_priority_score is None else float(min_priority_score)
    if not enabled or not signals:
        return DTE0PriorityScoreExecutionFilterResult(
            kept=list(signals or []),
            dropped=[],
            min_priority_score=floor,
            enabled=enabled,
        )

    kept: List[Any] = []
    dropped: List[Any] = []
    for sig in signals:
        if is_dte0_priority_score_eligible_for_execution(sig, min_priority_score=floor, enabled=True):
            kept.append(sig)
        else:
            dropped.append(sig)

    return DTE0PriorityScoreExecutionFilterResult(
        kept=kept,
        dropped=dropped,
        min_priority_score=floor,
        enabled=True,
    )


def rerank_dte0_signals_by_priority(signals: List[Any]) -> List[Any]:
    for rank, sig in enumerate(signals or [], 1):
        try:
            sig.priority_rank = rank
        except AttributeError:
            if isinstance(sig, dict):
                sig["priority_rank"] = rank
    return list(signals or [])


def log_dte0_priority_score_execution_filter(result: DTE0PriorityScoreExecutionFilterResult) -> None:
    if not result.enabled:
        log.info("0DTE_PS_FLOOR | disabled | 0DTE_SKIP_ZERO_PRIORITY_SCORE=false")
        return
    if not result.dropped:
        log.info("0DTE_PS_FLOOR | pass | floor=%.4f | pool=%d", result.min_priority_score, len(result.kept))
        return
    log.warning(
        "0DTE_PS_FLOOR | dropped=%d | kept=%d | floor=%.4f | symbols=%s",
        len(result.dropped),
        len(result.kept),
        result.min_priority_score,
        ",".join(result.dropped_labels),
    )
    for sig in result.dropped:
        sym = str(getattr(sig, "symbol", "?"))
        side = str(getattr(sig, "direction", "") or "").upper()
        ps = get_dte0_signal_priority_score(sig)
        log.info(
            "0DTE_PIPELINE stage=ps_floor | symbol=%s | side=%s | outcome=dropped | "
            "ps=%.4f | floor=%.4f",
            sym,
            side,
            ps,
            result.min_priority_score,
        )
