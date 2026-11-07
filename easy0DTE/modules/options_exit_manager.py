#!/usr/bin/env python3
"""
Options Emergency Exit Manager
==============================

Fallback-only emergency exits for 0DTE options when the stealth monitor fails.
Normal profit capture is owned exclusively by TrendlineOptionsStealthEngine.

Emergency exits: fail_safe, health_emergency.
EOD flatten uses canonical stealth id eod_close (not this module).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .options_exit_reason_catalog import OptionsEmergencyExitReason
from .options_types import OptionsPosition

log = logging.getLogger(__name__)


class ExitReason(Enum):
    """Emergency-only exit reasons (legacy enum name retained for imports)."""

    FAIL_SAFE = OptionsEmergencyExitReason.FAIL_SAFE.value
    HEALTH_EMERGENCY = OptionsEmergencyExitReason.HEALTH_EMERGENCY.value


@dataclass
class ExitSignal:
    """Exit signal for options position."""

    position_id: str
    reason: str
    exit_price: float
    exit_time: datetime
    pnl_pct: float
    pnl_dollar: float
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "reason": self.reason,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time.isoformat(),
            "pnl_pct": self.pnl_pct,
            "pnl_dollar": self.pnl_dollar,
            "details": self.details,
        }


class OptionsExitManager:
    """
    Emergency-only exit manager for 0DTE options.

    Stealth engine owns all primary exits. This layer runs only when stealth
    monitor fails (fail_safe) or quote health degrades past threshold.
    """

    def __init__(
        self,
        debit_spread_fail_safe_pct: float = -0.60,
        credit_spread_fail_safe_pct: float = -0.60,
        lotto_fail_safe_pct: float = -0.60,
    ):
        self.debit_spread_fail_safe_pct = debit_spread_fail_safe_pct
        self.credit_spread_fail_safe_pct = credit_spread_fail_safe_pct
        self.lotto_fail_safe_pct = lotto_fail_safe_pct
        log.info(
            "Options Emergency Exit Manager initialized | debit_fail_safe=%.0f%% | credit_fail_safe=%.0f%% | lotto_fail_safe=%.0f%%",
            debit_spread_fail_safe_pct * 100,
            credit_spread_fail_safe_pct * 100,
            lotto_fail_safe_pct * 100,
        )

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _nested_field(obj: Any, key: str, default: Any = None) -> Any:
        """Read a field from a mapping or object attribute (chain quote helpers)."""
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _get_structural_position_type(self, position: OptionsPosition) -> str:
        try:
            meta_raw = getattr(position, "metadata", None)
            meta = meta_raw if isinstance(meta_raw, dict) else {}
            norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
            structural = str(norm.get("position_type", "") or "").strip().lower()
        except Exception:
            structural = ""
        if structural:
            return structural
        legacy = str(getattr(position, "position_type", "") or "").strip().lower()
        if legacy == "lotto":
            return "single_leg"
        return legacy

    def _compute_pnl_pct(self, position: OptionsPosition, current_value: float) -> float:
        entry_price = float(getattr(position, "entry_price", 0.0) or 0.0)
        structural_type = self._get_structural_position_type(position)
        is_credit_spread = structural_type == "credit_spread"
        if entry_price <= 0:
            return 0.0
        if is_credit_spread:
            return (entry_price - float(current_value)) / entry_price
        return (float(current_value) - entry_price) / entry_price

    def _update_position_performance(self, position: OptionsPosition, current_value: float) -> None:
        try:
            if not isinstance(getattr(position, "metadata", None), dict):
                position.metadata = {}
            md = position.metadata
            perf = md.get("exit_performance")
            if not isinstance(perf, dict):
                perf = {}
                md["exit_performance"] = perf
            current_pnl_pct = float(self._compute_pnl_pct(position, current_value))
            prev_max = float(perf.get("max_pnl_pct", current_pnl_pct))
            max_pnl_pct = max(prev_max, current_pnl_pct)
            perf["current_pnl_pct"] = current_pnl_pct
            perf["max_pnl_pct"] = max_pnl_pct
            if max_pnl_pct > prev_max + 1e-9:
                log.info(
                    "OPTION_MAX_PNL_UPDATE | position_id=%s | symbol=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position.position_id,
                    position.symbol,
                    current_pnl_pct,
                    max_pnl_pct,
                )
            had_opportunity = bool(perf.get("had_opportunity", False))
            if (not had_opportunity) and max_pnl_pct >= 0.05:
                perf["had_opportunity"] = True
                log.info(
                    "OPTION_OPPORTUNITY_DETECTED | position_id=%s | symbol=%s | max_pnl_pct=%.4f",
                    position.position_id,
                    position.symbol,
                    max_pnl_pct,
                )
        except Exception as perf_err:
            log.debug(
                "OPTION_PERF_TRACK_SKIP | position_id=%s | reason=%s",
                getattr(position, "position_id", "unknown"),
                perf_err,
            )

    def check_fail_safe(
        self,
        position: OptionsPosition,
        current_value: float,
        market_data: Dict[str, Any],
    ) -> Optional[ExitSignal]:
        entry_price = position.entry_price
        structural_type = self._get_structural_position_type(position)
        is_credit_spread = structural_type == "credit_spread"

        if is_credit_spread:
            pnl_pct = (entry_price - current_value) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (entry_price - current_value) * position.quantity * 100
        else:
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (current_value - entry_price) * position.quantity * 100

        if structural_type == "debit_spread":
            fail_safe_threshold = self.debit_spread_fail_safe_pct
        elif structural_type == "credit_spread":
            fail_safe_threshold = self.credit_spread_fail_safe_pct
        else:
            fail_safe_threshold = self.lotto_fail_safe_pct

        fail_safe_reasons = []
        if pnl_pct <= fail_safe_threshold:
            fail_safe_reasons.append(
                f"Price drop: {pnl_pct * 100:.1f}% <= {fail_safe_threshold * 100:.0f}% threshold"
            )

        bid_ask_spread_pct = market_data.get("bid_ask_spread_pct", 0.0)
        if float(bid_ask_spread_pct or 0.0) > 10.0:
            fail_safe_reasons.append(
                f"Liquidity degraded: Bid/ask spread {float(bid_ask_spread_pct):.1f}% > 10%"
            )

        if fail_safe_reasons:
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.FAIL_SAFE.value,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={
                    "fail_safe_reasons": fail_safe_reasons,
                    "entry_price": entry_price,
                    "current_value": current_value,
                    "bid_ask_spread_pct": bid_ask_spread_pct,
                    "position_type": position.position_type,
                    "structural_position_type": structural_type,
                },
            )
        return None

    def evaluate_exit(
        self,
        position: OptionsPosition,
        current_value: float,
        market_data: Dict[str, Any],
        orb_data: Optional[Any] = None,
    ) -> Optional[ExitSignal]:
        return self.evaluate_emergency_only(
            position=position,
            current_value=current_value,
            market_data=market_data,
            stale_quote=False,
        )

    def evaluate_emergency_only(
        self,
        position: OptionsPosition,
        current_value: float,
        market_data: Dict[str, Any],
        stale_quote: bool = False,
    ) -> Optional[ExitSignal]:
        """Fallback-only emergency exits (fail_safe / health_emergency)."""
        self._update_position_performance(position, current_value)

        if stale_quote:
            entry_price = position.entry_price
            pnl_pct = (current_value - entry_price) / entry_price if entry_price > 0 else 0.0
            pnl_dollar = (current_value - entry_price) * position.quantity * 100
            return ExitSignal(
                position_id=position.position_id,
                reason=ExitReason.HEALTH_EMERGENCY.value,
                exit_price=current_value,
                exit_time=datetime.now(timezone.utc),
                pnl_pct=pnl_pct,
                pnl_dollar=pnl_dollar,
                details={"fallback": "stale_quote_threshold_exceeded"},
            )

        return self.check_fail_safe(position, current_value, market_data)

    def get_exit_summary(self, exit_signals: List[ExitSignal]) -> Dict[str, Any]:
        if not exit_signals:
            return {
                "total_exits": 0,
                "by_reason": {},
                "total_pnl": 0.0,
                "avg_pnl_pct": 0.0,
            }
        by_reason: Dict[str, int] = {}
        total_pnl = 0.0
        for signal in exit_signals:
            reason = signal.reason
            by_reason[reason] = by_reason.get(reason, 0) + 1
            total_pnl += signal.pnl_dollar
        avg_pnl_pct = sum(s.pnl_pct for s in exit_signals) / len(exit_signals) if exit_signals else 0.0
        return {
            "total_exits": len(exit_signals),
            "by_reason": by_reason,
            "total_pnl": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
        }
