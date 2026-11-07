"""
ORB SO terminal reject tracking (Jun 5 P0).

Every collected symbol must end in executed OR a structured SO_EXEC_REJECT before
so_execution_batch_completed may be marked successful.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

log = logging.getLogger(__name__)

_active_tracker: Optional["SoTerminalRejectTracker"] = None


def set_active_so_terminal_tracker(tracker: Optional["SoTerminalRejectTracker"]) -> None:
    global _active_tracker
    _active_tracker = tracker


def get_active_so_terminal_tracker() -> Optional["SoTerminalRejectTracker"]:
    return _active_tracker


def log_so_exec_reject(symbol: str, stage: str, reason: str, detail: str = "") -> None:
    sym = (symbol or "?").strip().upper()
    tail = f" | detail={detail}" if detail else ""
    log.info(
        "SO_EXEC_REJECT | symbol=%s | stage=%s | reason=%s%s",
        sym,
        stage,
        reason,
        tail,
    )


class SoTerminalRejectTracker:
    """Accumulates terminal reject reasons keyed by symbol (last reason wins)."""

    def __init__(self) -> None:
        self._records: Dict[str, Dict[str, Any]] = {}

    def record(
        self,
        symbol: str,
        stage: str,
        reason: str,
        detail: str = "",
        signal: Optional[Dict[str, Any]] = None,
    ) -> None:
        sym = (symbol or "?").strip().upper()
        if not sym or sym == "?":
            return
        log_so_exec_reject(sym, stage, reason, detail)
        base = dict(signal) if isinstance(signal, dict) else {"symbol": sym}
        base["symbol"] = sym
        base["reject_stage"] = stage
        base["reject_reason"] = reason
        if detail:
            base["reject_detail"] = detail
        self._records[sym] = base

    @property
    def symbols(self) -> Set[str]:
        return set(self._records.keys())

    def record_from_pipeline_drop(self, stage: str, symbol: str, reason: str, detail: str = "") -> None:
        self.record(symbol, stage, reason, detail)

    def merge_into_rejected_signals(
        self,
        rejected_signals: List[Dict[str, Any]],
        executed_signals: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Append terminal rejects not already present in rejected or executed lists."""
        executed_syms = {
            (s.get("symbol") or "").strip().upper()
            for s in (executed_signals or [])
            if isinstance(s, dict) and s.get("symbol")
        }
        existing_reject_syms = {
            (s.get("symbol") or "").strip().upper()
            for s in rejected_signals
            if isinstance(s, dict) and s.get("symbol")
        }
        merged = list(rejected_signals)
        for sym, rec in sorted(self._records.items()):
            if sym in executed_syms or sym in existing_reject_syms:
                continue
            merged.append(rec)
        return merged

    def covers_symbols(self, symbols: Iterable[str], executed_symbols: Optional[Iterable[str]] = None) -> bool:
        required = {(s or "").strip().upper() for s in symbols if (s or "").strip()}
        if not required:
            return True
        executed = {(s or "").strip().upper() for s in (executed_symbols or []) if (s or "").strip()}
        covered = executed | self.symbols
        missing = sorted(required - covered)
        return len(missing) == 0

    def missing_symbols(self, symbols: Iterable[str], executed_symbols: Optional[Iterable[str]] = None) -> List[str]:
        required = {(s or "").strip().upper() for s in symbols if (s or "").strip()}
        if not required:
            return []
        executed = {(s or "").strip().upper() for s in (executed_symbols or []) if (s or "").strip()}
        covered = executed | self.symbols
        return sorted(required - covered)

    def log_summary(self, collected_symbols: Optional[Iterable[str]] = None) -> None:
        parts = [
            f"{sym}:{rec.get('reject_reason', '?')}"
            for sym, rec in sorted(self._records.items())
        ]
        missing = []
        if collected_symbols is not None:
            missing = self.missing_symbols(collected_symbols)
        log.info(
            "SO_EXEC_REJECT_SUMMARY | reject_count=%d | rejects=%s | missing_terminal=%s",
            len(self._records),
            " ; ".join(parts) if parts else "-",
            ",".join(missing) if missing else "-",
        )


def batch_completion_allowed(
    *,
    collected_count: int,
    executed_count: int,
    rejected_count: int,
    terminal_complete: bool,
) -> bool:
    """
    so_execution_batch_completed only when:
    - no signals collected, OR
    - at least one fill, OR
    - every collected symbol has a terminal reject (rejected_count covers collection).
    """
    if collected_count <= 0:
        return True
    if executed_count > 0:
        return True
    if rejected_count > 0 and terminal_complete:
        return True
    return False
