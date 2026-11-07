#!/usr/bin/env python3
"""
ORB1 Breakout rejected / executed opportunity outcome audit (June 1 session improvement pass).

Registers candidates at execution time and logs forward underlying performance at
+5m, +15m, and +30m horizons for selector quality measurement.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
PT = ZoneInfo("America/Los_Angeles")

HORIZON_MINUTES = (5, 15, 30)


@dataclass
class Orb0DteOutcomeCandidate:
    symbol: str
    direction: str
    executed: bool
    entry_spot: float
    registered_at: datetime
    reject_reason: str = ""
    reason_code: str = ""
    strategy_type: str = ""
    option_type: str = ""
    strike: Optional[float] = None
    delta: Optional[float] = None
    premium: Optional[float] = None
    structure: str = ""
    position_id: str = ""
    best_alt_spread_pct: Optional[float] = None

    def to_log_fields(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "executed": str(self.executed).lower(),
            "entry_spot": round(float(self.entry_spot), 4),
            "reject_reason": (self.reject_reason or "")[:120],
            "reason_code": self.reason_code or "",
            "strategy_type": self.strategy_type or "",
            "structure": self.structure or "",
            "strike": self.strike,
            "delta": self.delta,
            "premium": self.premium,
            "position_id": self.position_id or "",
        }


class Orb0DteRejectedOutcomeAudit:
    """Session-scoped registry + delayed underlying move audit."""

    def __init__(self) -> None:
        self._candidates: List[Orb0DteOutcomeCandidate] = []
        self._tasks: List[asyncio.Task] = []
        self._session_date = ""

    def begin_session(self, session_date: Optional[str] = None) -> None:
        self._candidates.clear()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        self._tasks.clear()
        self._session_date = session_date or datetime.now(PT).strftime("%Y-%m-%d")

    def register_executed(
        self,
        *,
        symbol: str,
        direction: str,
        entry_spot: float,
        strategy_type: str = "",
        option_type: str = "",
        strike: Optional[float] = None,
        delta: Optional[float] = None,
        premium: Optional[float] = None,
        structure: str = "",
        position_id: str = "",
    ) -> None:
        self._candidates.append(
            Orb0DteOutcomeCandidate(
                symbol=str(symbol).upper(),
                direction=str(direction or "LONG").upper(),
                executed=True,
                entry_spot=float(entry_spot),
                registered_at=datetime.now(PT),
                strategy_type=strategy_type,
                option_type=option_type,
                strike=strike,
                delta=delta,
                premium=premium,
                structure=structure,
                position_id=position_id,
            )
        )

    def register_rejected(
        self,
        *,
        symbol: str,
        direction: str,
        entry_spot: float,
        reason_code: str = "",
        reject_reason: str = "",
        strategy_type: str = "",
        option_type: str = "",
        best_alt: Optional[Dict[str, Any]] = None,
    ) -> None:
        strike = None
        delta = None
        premium = None
        best_spread = None
        if isinstance(best_alt, dict):
            strike = best_alt.get("strike")
            delta = best_alt.get("delta")
            premium = best_alt.get("premium")
            best_spread = best_alt.get("spread_pct")
        self._candidates.append(
            Orb0DteOutcomeCandidate(
                symbol=str(symbol).upper(),
                direction=str(direction or "LONG").upper(),
                executed=False,
                entry_spot=float(entry_spot),
                registered_at=datetime.now(PT),
                reject_reason=reject_reason,
                reason_code=reason_code,
                strategy_type=strategy_type,
                option_type=option_type,
                strike=strike,
                delta=delta,
                premium=premium,
                best_alt_spread_pct=best_spread,
            )
        )

    def schedule_post_session_audits(self, trading_system: Any) -> None:
        """Schedule horizon checks; uses underlying quotes (option marks optional later)."""
        if not self._candidates:
            return
        for minutes in HORIZON_MINUTES:
            task = asyncio.create_task(
                self._run_horizon_audit(trading_system, minutes),
                name=f"orb_0dte_outcome_audit_{minutes}m",
            )
            self._tasks.append(task)

    async def _fetch_spot(self, trading_system: Any, symbol: str) -> Optional[float]:
        dm = getattr(trading_system, "data_manager", None)
        if dm is None:
            return None
        try:
            q = await dm.get_quote(symbol)
            if isinstance(q, dict):
                for key in ("last", "price", "close", "current_price"):
                    v = q.get(key)
                    if v is not None:
                        return float(v)
        except Exception as exc:
            log.debug("ORB_0DTE_OUTCOME_QUOTE_FAIL | symbol=%s | error=%s", symbol, exc)
        return None

    def _directional_pnl_pct(self, direction: str, entry: float, current: float) -> float:
        if entry <= 0:
            return 0.0
        move = (current - entry) / entry
        if str(direction).upper() == "SHORT":
            move = -move
        return float(move) * 100.0

    async def _run_horizon_audit(self, trading_system: Any, horizon_minutes: int) -> None:
        await asyncio.sleep(max(1, int(horizon_minutes)) * 60)
        for cand in list(self._candidates):
            spot_now = await self._fetch_spot(trading_system, cand.symbol)
            if spot_now is None:
                continue
            gain_pct = self._directional_pnl_pct(cand.direction, cand.entry_spot, spot_now)
            log.info(
                "ORB_0DTE_REJECTED_OUTCOME_AUDIT | horizon_min=%d | %s | executed=%s | "
                "entry_spot=%.4f | spot_now=%.4f | underlying_move_pct=%.2f | reason_code=%s | "
                "strategy=%s | strike=%s | premium=%s",
                int(horizon_minutes),
                cand.symbol,
                str(cand.executed).lower(),
                float(cand.entry_spot),
                float(spot_now),
                float(gain_pct),
                cand.reason_code or "n/a",
                cand.strategy_type or "n/a",
                cand.strike if cand.strike is not None else "n/a",
                cand.premium if cand.premium is not None else "n/a",
            )

    async def run_immediate_summary(self, trading_system: Any) -> None:
        """Log snapshot at audit time (max horizon label) for all candidates."""
        for cand in self._candidates:
            spot_now = await self._fetch_spot(trading_system, cand.symbol)
            if spot_now is None:
                log.info(
                    "ORB_0DTE_REJECTED_OUTCOME_AUDIT | horizon=immediate | %s | executed=%s | "
                    "entry_spot=%.4f | spot_unavailable=true",
                    cand.symbol,
                    str(cand.executed).lower(),
                    float(cand.entry_spot),
                )
                continue
            gain_pct = self._directional_pnl_pct(cand.direction, cand.entry_spot, spot_now)
            status = "executed" if cand.executed else "rejected"
            log.info(
                "ORB_0DTE_REJECTED_OUTCOME_AUDIT | horizon=summary | %s %s | underlying_move_pct=%.2f | "
                "entry_spot=%.4f | spot_now=%.4f | reason_code=%s",
                cand.symbol,
                status,
                float(gain_pct),
                float(cand.entry_spot),
                float(spot_now),
                cand.reason_code or "n/a",
            )
