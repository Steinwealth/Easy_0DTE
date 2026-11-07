#!/usr/bin/env python3
"""Data models for profit_optimizer analytics (read-only flight recorder)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


STRATEGY_ORB_SO = "orb_so"
STRATEGY_ORB_0DTE = "orb_0dte"
STRATEGY_TRENDLINE_0DTE = "trendline_0dte"
STRATEGY_BREAKOUT_0DTE = "breakout_0dte"

TRENDLINE_ENABLED = False
BREAKOUT_ENABLED = False


def trendline_profit_optimizer_enabled() -> bool:
    """When true, record Trendline 0DTE fills in profit_optimizer (telemetry only)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(
            "ENABLE_TRENDLINE_PROFIT_OPTIMIZER",
            get_config_value("ENABLE_TRENDLINE_STRATEGY", "false"),
        )
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return TRENDLINE_ENABLED


def breakout_profit_optimizer_enabled() -> bool:
    """When true, record Breakout 0DTE fills in profit_optimizer (telemetry only)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(
            "ENABLE_BREAKOUT_PROFIT_OPTIMIZER",
            get_config_value("ENABLE_BREAKOUT_STRATEGY", "false"),
        )
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return BREAKOUT_ENABLED


@dataclass
class TradeEntrySnapshot:
    trade_id: str
    date: str
    symbol: str
    strategy_path: str
    direction: str
    entry_time: str
    underlying_entry_price: Optional[float] = None
    strike: Optional[float] = None
    expiration: str = ""
    entry_bid: Optional[float] = None
    entry_ask: Optional[float] = None
    entry_mid: Optional[float] = None
    entry_premium: Optional[float] = None
    contracts: int = 0
    position_size: Optional[float] = None
    contract_identity: Dict[str, Any] = field(default_factory=dict)
    selector_metadata: Dict[str, Any] = field(default_factory=dict)
    entry_execution_quality: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        # Promote calendar expiry fields onto the entry record for exports / grouping.
        for src in (self.extra, self.selector_metadata):
            if not isinstance(src, dict):
                continue
            for key in (
                "session_date",
                "expiry_ymd",
                "calendar_dte",
                "is_0dte",
                "expiry_selection_mode",
                "requested_expiry_ymd",
                "selected_expiry_ymd",
                "fallback_days",
                "fallback_reason",
                "dte",
            ):
                if key not in out or out.get(key) in (None, ""):
                    if src.get(key) is not None and src.get(key) != "":
                        out[key] = src.get(key)
        # Promote call/put volume, skew, owned-contract size for entry review.
        try:
            from profit_optimizer.path_chain_skew import REVIEW_MICROSTRUCTURE_KEYS

            for src in (self.extra, self.selector_metadata):
                if not isinstance(src, dict):
                    continue
                for key in REVIEW_MICROSTRUCTURE_KEYS:
                    if out.get(key) is None and src.get(key) is not None:
                        out[key] = src.get(key)
        except Exception:
            pass
        return out


@dataclass
class PremiumPathSample:
    timestamp: str
    underlying_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    mark: Optional[float] = None
    spread_width: Optional[float] = None
    spread_pct: Optional[float] = None
    volume: Optional[float] = None  # legacy alias; prefer cumulative_volume
    cumulative_volume: Optional[float] = None
    open_interest: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    iv: Optional[float] = None
    # Session call/put chain volume (periodic refresh via osnap — not every tick fetch)
    call_chain_volume: Optional[float] = None
    put_chain_volume: Optional[float] = None
    call_chain_volume_now: Optional[float] = None
    call_chain_volume_30s_ago: Optional[float] = None
    call_chain_volume_5m_ago: Optional[float] = None
    put_chain_volume_now: Optional[float] = None
    put_chain_volume_30s_ago: Optional[float] = None
    put_chain_volume_5m_ago: Optional[float] = None
    selected_contract_volume_now: Optional[float] = None
    selected_contract_volume_30s_ago: Optional[float] = None
    selected_contract_volume_5m_ago: Optional[float] = None
    selected_call_contract_volume_now: Optional[float] = None
    selected_call_contract_volume_30s_ago: Optional[float] = None
    selected_call_contract_volume_5m_ago: Optional[float] = None
    selected_put_contract_volume_now: Optional[float] = None
    selected_put_contract_volume_30s_ago: Optional[float] = None
    selected_put_contract_volume_5m_ago: Optional[float] = None
    bid_size_now: Optional[float] = None
    bid_size_30s_ago: Optional[float] = None
    bid_size_5m_ago: Optional[float] = None
    ask_size_now: Optional[float] = None
    ask_size_30s_ago: Optional[float] = None
    ask_size_5m_ago: Optional[float] = None
    put_call_volume_ratio: Optional[float] = None
    put_call_oi_ratio: Optional[float] = None
    direction_volume_pct: Optional[float] = None
    call_chain_open_interest: Optional[float] = None
    put_chain_open_interest: Optional[float] = None
    total_chain_volume: Optional[float] = None
    total_chain_open_interest: Optional[float] = None
    representative_volume: Optional[float] = None
    representative_open_interest: Optional[float] = None
    representative_delta: Optional[float] = None
    representative_bid: Optional[float] = None
    representative_ask: Optional[float] = None
    # Session underlying tech (MACD hold/exit review)
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    prior_macd_histogram: Optional[float] = None
    macd_above_zero: Optional[bool] = None
    macd_cross: str = ""
    macd_hold_ok_call: Optional[bool] = None
    macd_exit_signal_call: Optional[bool] = None
    macd_hist_sign: Optional[int] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    rsi: Optional[float] = None
    vwap: Optional[float] = None
    vwap_distance_pct: Optional[float] = None
    # Equity participation vs recent bars (entry/skip + thin-tape review).
    volume_ratio: Optional[float] = None
    orb_volume_ratio: Optional[float] = None
    volume_ratio_missing: Optional[bool] = None
    volume_ratio_source: str = ""
    tech_source: str = ""
    session_bar_count: Optional[int] = None
    vwap_missing: Optional[bool] = None
    rsi_missing: Optional[bool] = None
    session_tech_bar_at: Optional[str] = None
    volume_1m: Optional[float] = None
    macd_missing: Optional[bool] = None
    atr: Optional[float] = None
    momentum_10: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_middle: Optional[float] = None
    bollinger_lower: Optional[float] = None
    bollinger_width: Optional[float] = None
    rs_vs_spy: Optional[float] = None
    hold_exit_dir_pct: Optional[float] = None
    quote_age_seconds: Optional[float] = None
    quote_quality: str = ""
    premium_source: str = ""
    mark_quality: str = ""
    mark_is_exit_grade: bool = False
    price_source_telemetry: str = ""
    # Compact exit-research fields (live + shadow shared schema)
    trade_id: str = ""
    contract_symbol: str = ""
    observation_timestamp: Optional[str] = None
    broker_quote_timestamp: Optional[str] = None
    last: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    option_l1_imbalance: Optional[float] = None
    shadow_trust: str = ""
    contract_match_status: str = ""
    freshness_status: str = ""
    monitor_tick_id: str = ""
    prem_vel_10: Optional[float] = None
    reused_last_exact: bool = False
    path_phase: str = ""
    entry_premium: Optional[float] = None
    hwm_premium: Optional[float] = None
    hwm_timestamp: Optional[str] = None
    mfe_pct: Optional[float] = None
    drawdown_from_hwm_pct: Optional[float] = None
    mfe_giveback_armed: Optional[bool] = None
    giveback_floor_premium: Optional[float] = None
    seconds_since_entry: Optional[float] = None
    seconds_since_hwm: Optional[float] = None
    # Shadow-only (null on live)
    actual_exit_timestamp: Optional[str] = None
    actual_exit_bid: Optional[float] = None
    actual_exit_premium: Optional[float] = None
    actual_exit_reason: str = ""
    seconds_since_actual_exit: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Keep volume and cumulative_volume aligned when only one is set.
        if d.get("cumulative_volume") is None and d.get("volume") is not None:
            d["cumulative_volume"] = d["volume"]
        if d.get("volume") is None and d.get("cumulative_volume") is not None:
            d["volume"] = d["cumulative_volume"]
        return d


@dataclass
class UnderlyingPathSample:
    timestamp: str
    underlying_price: float
    volume: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PremiumMetrics:
    premium_mfe: Optional[float] = None
    premium_mae: Optional[float] = None
    premium_hwm: Optional[float] = None
    premium_lwm: Optional[float] = None
    time_to_mfe_seconds: Optional[float] = None
    time_to_mae_seconds: Optional[float] = None
    peak_premium: Optional[float] = None
    peak_underlying: Optional[float] = None
    largest_premium_drawdown: Optional[float] = None
    premium_mfe_pct: Optional[float] = None
    premium_mae_pct: Optional[float] = None
    max_favorable_premium_pct: Optional[float] = None
    max_adverse_premium_pct: Optional[float] = None
    premium_at_peak: Optional[float] = None
    premium_5m_after_peak: Optional[float] = None
    premium_15m_after_peak: Optional[float] = None
    premium_30m_after_peak: Optional[float] = None
    premium_at_eod: Optional[float] = None
    theta_decay_after_peak: Optional[float] = None
    theta_decay_pct: Optional[float] = None
    theta_decay_5m_after_peak_pct: Optional[float] = None
    theta_decay_15m_after_peak_pct: Optional[float] = None
    theta_decay_30m_after_peak_pct: Optional[float] = None
    capture_pct: Optional[float] = None
    capture_of_net_opportunity_pct: Optional[float] = None
    missed_premium_pct: Optional[float] = None
    missed_opportunity_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["max_favorable_premium_pct"] = self.premium_mfe_pct or self.max_favorable_premium_pct
        d["max_adverse_premium_pct"] = self.premium_mae_pct or self.max_adverse_premium_pct
        return d


@dataclass
class UnderlyingMetrics:
    underlying_mfe: Optional[float] = None
    underlying_mae: Optional[float] = None
    time_to_mfe_seconds: Optional[float] = None
    time_to_mae_seconds: Optional[float] = None
    peak_price: Optional[float] = None
    largest_pullback: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExitObservation:
    exit_time: str
    exit_premium: Optional[float] = None
    exit_underlying: Optional[float] = None
    exit_reason: str = ""
    realized_pnl_pct: Optional[float] = None
    premium_mfe_pct: Optional[float] = None
    premium_mae_pct: Optional[float] = None
    capture_pct: Optional[float] = None
    capture_of_net_opportunity_pct: Optional[float] = None
    missed_premium_pct: Optional[float] = None
    missed_opportunity_pct: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        extra = self.extra if isinstance(self.extra, dict) else {}
        try:
            from profit_optimizer.path_chain_skew import REVIEW_MICROSTRUCTURE_KEYS

            for key in REVIEW_MICROSTRUCTURE_KEYS:
                if d.get(key) is None and extra.get(key) is not None:
                    d[key] = extra.get(key)
        except Exception:
            pass
        return d


@dataclass
class TradeFlightRecord:
    entry: TradeEntrySnapshot
    premium_path: List[Dict[str, Any]] = field(default_factory=list)
    underlying_path: List[Dict[str, Any]] = field(default_factory=list)
    premium_metrics: Dict[str, Any] = field(default_factory=dict)
    premium_metrics_at_exit: Dict[str, Any] = field(default_factory=dict)
    underlying_metrics: Dict[str, Any] = field(default_factory=dict)
    peak_quote_metadata: Dict[str, Any] = field(default_factory=dict)
    exit_observation: Optional[Dict[str, Any]] = None
    status: str = "open"
    closed_at: str = ""
    shadow_tracking: bool = False
    path_audit: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        from profit_optimizer.trade_export import enrich_trade_export_dict

        base = {
            "entry": self.entry.to_dict(),
            "premium_path": list(self.premium_path),
            "underlying_path": list(self.underlying_path),
            "premium_metrics": dict(self.premium_metrics),
            "premium_metrics_at_exit": dict(self.premium_metrics_at_exit),
            "underlying_metrics": dict(self.underlying_metrics),
            "peak_quote_metadata": dict(self.peak_quote_metadata),
            "exit_observation": self.exit_observation,
            "status": self.status,
            "closed_at": self.closed_at,
            "shadow_tracking": bool(self.shadow_tracking),
            "path_audit": dict(self.path_audit),
        }
        return enrich_trade_export_dict(base)


@dataclass
class DailySummary:
    trade_count: int = 0
    avg_premium_mfe: Optional[float] = None
    avg_premium_mae: Optional[float] = None
    avg_capture_pct: Optional[float] = None
    avg_time_to_mfe_seconds: Optional[float] = None
    avg_time_to_mae_seconds: Optional[float] = None
    largest_opportunity: Optional[float] = None
    largest_missed_opportunity: Optional[float] = None
    theta_decay_stats: Dict[str, Any] = field(default_factory=dict)
    by_symbol: Dict[str, Any] = field(default_factory=dict)
    by_strike: Dict[str, Any] = field(default_factory=dict)
    by_direction: Dict[str, Any] = field(default_factory=dict)
    by_strategy: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
