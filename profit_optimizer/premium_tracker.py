#!/usr/bin/env python3
"""Premium and underlying path tracking with MFE/MAE/theta analytics (read-only)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from profit_optimizer.audit import parse_timestamp_utc, seconds_between_timestamps
from profit_optimizer.time_utils import normalize_timestamp_iso
from profit_optimizer.peak_quote import build_peak_quote_metadata, is_trusted_peak_quote
from profit_optimizer.profit_optimizer_models import (
    PremiumMetrics,
    PremiumPathSample,
    UnderlyingMetrics,
    UnderlyingPathSample,
)

log = logging.getLogger(__name__)


def _parse_ts(ts: str) -> Optional[datetime]:
    return parse_timestamp_utc(ts)


def _seconds_between(t0: str, t1: str) -> Optional[float]:
    return seconds_between_timestamps(t0, t1)


def _pf(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def max_path_samples() -> int:
    """Runaway-recorder ceiling only. 2s cadence is never stride-capped.

    Default covers PROFIT_OPTIMIZER_RECORDING_WINDOW_SECONDS at 2s (~5h → 9k)
    with headroom. Compact JSONL is the durable store; this bound is RAM guard.
    """
    try:
        from modules.config_loader import get_config_value

        n = int(float(get_config_value("PROFIT_OPTIMIZER_MAX_PATH_SAMPLES", "20000") or 20000))
    except Exception:
        n = 20000
    return max(9000, min(50000, n))


def cap_sample_path(path: List[Dict[str, Any]], *, max_n: Optional[int] = None) -> List[Dict[str, Any]]:
    """Identity helper. Do not stride 2s research paths (that made capture review useless)."""
    rows = list(path or [])
    if max_n is None:
        return rows
    cap = max(0, int(max_n))
    if cap <= 0 or len(rows) <= cap:
        return rows
    # Last-N only if a caller explicitly asked for a bound (never used for persist).
    return rows[-cap:]


def _safe_div(num: float, den: float) -> Optional[float]:
    if den == 0:
        return None
    return num / den


@dataclass
class PremiumTracker:
    """Per-trade path recorder; computes metrics from observed samples only."""

    strategy_path: str
    direction: str
    entry_premium: float
    entry_underlying: float
    entry_time: str
    is_long_premium: bool = True
    premium_path: List[Dict[str, Any]] = field(default_factory=list)
    underlying_path: List[Dict[str, Any]] = field(default_factory=list)
    peak_quote_metadata: Dict[str, Any] = field(default_factory=dict)
    _last_sample_mono: float = 0.0
    _entry_mono: float = 0.0
    _peak_premium: float = 0.0
    _peak_ts: str = ""
    _hwm_premium: float = 0.0
    _lwm_premium: float = 0.0
    _hwm_underlying: float = 0.0
    _lwm_underlying: float = 0.0
    _mfe_ts: str = ""
    _mae_ts: str = ""
    _last_premium: float = 0.0

    def __post_init__(self) -> None:
        self.entry_time = normalize_timestamp_iso(self.entry_time)
        ep = float(self.entry_premium or 0.0)
        eu = float(self.entry_underlying or 0.0)
        self._peak_premium = ep
        self._hwm_premium = ep
        self._lwm_premium = ep
        self._hwm_underlying = eu
        self._lwm_underlying = eu
        self._last_premium = ep
        self._peak_ts = self.entry_time
        self._mfe_ts = self.entry_time
        self._mae_ts = self.entry_time

    def set_entry_mono(self, mono: float) -> None:
        self._entry_mono = float(mono)

    def rehydrate_from_saved(
        self,
        premium_path: List[Dict[str, Any]],
        underlying_path: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Restore tracker peaks from persisted paths after cold start."""
        self.premium_path = list(premium_path or [])
        self.underlying_path = list(underlying_path or [])
        ep = float(self.entry_premium or 0.0)
        self._peak_premium = ep
        self._hwm_premium = ep
        self._lwm_premium = ep
        self._peak_ts = self.entry_time
        self._mfe_ts = self.entry_time
        self._mae_ts = self.entry_time
        for row in self.premium_path:
            if str(row.get("sample_type") or "") == "gap":
                continue
            sample = PremiumPathSample(
                timestamp=str(row.get("timestamp") or self.entry_time),
                underlying_price=_pf(row.get("underlying_price")),
                bid=_pf(row.get("bid")),
                ask=_pf(row.get("ask")),
                mid=_pf(row.get("mid")),
                mark=_pf(row.get("mark")),
                spread_width=_pf(row.get("spread_width")),
                spread_pct=_pf(row.get("spread_pct")),
                premium_source=str(row.get("premium_source") or row.get("quote_source") or ""),
                mark_quality=str(row.get("mark_quality") or ""),
                mark_is_exit_grade=bool(row.get("mark_is_exit_grade")),
            )
            prem = self._effective_premium(sample)
            if prem is None or prem <= 0:
                continue
            self._last_premium = prem
            if prem > self._hwm_premium:
                self._hwm_premium = prem
                self._peak_premium = prem
                self._peak_ts = sample.timestamp
                self._mfe_ts = sample.timestamp
            if prem < self._lwm_premium:
                self._lwm_premium = prem
                self._mae_ts = sample.timestamp
            u = sample.underlying_price
            if u is not None and u > 0:
                if u > self._hwm_underlying:
                    self._hwm_underlying = float(u)
                if u < self._lwm_underlying or self._lwm_underlying <= 0:
                    self._lwm_underlying = float(u)
        log.info(
            "PATH_V2_RESTORE | action=tracker_rehydrate | samples=%d | und_samples=%d | "
            "hwm_premium=%.4f | lwm_premium=%.4f",
            len(self.premium_path),
            len(self.underlying_path),
            float(self._hwm_premium or 0.0),
            float(self._lwm_premium or 0.0),
        )

    def should_sample(self, policy: Any, now_mono: float) -> bool:
        return bool(policy.should_sample(self._last_sample_mono, now_mono, self._entry_mono))

    def record_premium_sample(
        self, sample: PremiumPathSample, *, now_mono: float, freeze_latches: bool = False
    ) -> Tuple[bool, bool, Optional[Dict[str, Any]]]:
        """Returns (mfe_updated, mae_updated, peak_metadata_if_mfe)."""
        self._last_sample_mono = now_mono
        from profit_optimizer.path_jsonl import compact_premium_row

        row = compact_premium_row(sample.to_dict())
        if len(self.premium_path) >= max_path_samples():
            if not getattr(self, "_path_ceiling_logged", False):
                self._path_ceiling_logged = True
                log.warning(
                    "PO_PATH | action=ram_ceiling | samples=%d | cap=%d | cadence=2s | "
                    "append_stopped=true",
                    len(self.premium_path),
                    max_path_samples(),
                )
            return False, False, None
        self.premium_path.append(row)

        prem = self._effective_premium(sample)
        if freeze_latches:
            # Deferred OCC observation: copy prior HWM/MAE; do not advance latches.
            return False, False, None
        if prem is None or prem <= 0:
            return False, False, None
        self._last_premium = prem
        mfe_up = mae_up = False
        peak_meta: Optional[Dict[str, Any]] = None

        if prem > self._hwm_premium:
            jump_ok = True
            last_ref = float(self._last_premium or self.entry_premium or 0.0)
            if last_ref > 0 and prem / last_ref > 1.001:
                try:
                    from profit_optimizer.shadow_continuity import live_hwm_jump_ratio_threshold

                    ratio = prem / last_ref
                    if ratio > live_hwm_jump_ratio_threshold():
                        jump_ok = is_trusted_peak_quote(
                            premium_source=str(sample.premium_source or ""),
                            quote_quality=str(sample.quote_quality or ""),
                            mark_quality=str(sample.mark_quality or ""),
                            price_source_telemetry=str(sample.price_source_telemetry or ""),
                            mark_is_exit_grade=bool(sample.mark_is_exit_grade),
                        )
                except Exception:
                    jump_ok = True
            if jump_ok:
                self._hwm_premium = prem
                self._peak_premium = prem
                self._peak_ts = sample.timestamp
                self._mfe_ts = sample.timestamp
                mfe_up = True
                peak_meta = build_peak_quote_metadata(
                    peak_time=sample.timestamp,
                    peak_premium=prem,
                    peak_underlying_price=sample.underlying_price,
                    peak_bid=sample.bid,
                    peak_ask=sample.ask,
                    peak_mid=sample.mid,
                    peak_spread_pct=sample.spread_pct,
                    peak_quote_age_seconds=sample.quote_age_seconds,
                    peak_quote_quality=sample.quote_quality,
                    peak_premium_source=sample.premium_source,
                    peak_mark_quality=sample.mark_quality,
                    peak_is_exit_grade=bool(sample.mark_is_exit_grade),
                    price_source_telemetry=sample.price_source_telemetry,
                )
                self.peak_quote_metadata = peak_meta
        if prem < self._lwm_premium:
            self._lwm_premium = prem
            self._mae_ts = sample.timestamp
            mae_up = True

        u = sample.underlying_price
        if u is not None and u > 0:
            if u > self._hwm_underlying:
                self._hwm_underlying = float(u)
            if u < self._lwm_underlying or self._lwm_underlying <= 0:
                self._lwm_underlying = float(u)

        return mfe_up, mae_up, peak_meta

    def record_underlying_sample(
        self,
        sample: UnderlyingPathSample,
        *,
        now_mono: float,
        policy: Any,
        force: bool = False,
        path_phase: str = "live",
    ) -> Tuple[bool, bool]:
        if not force and not policy.should_sample(self._last_sample_mono, now_mono, self._entry_mono):
            return False, False
        if not force:
            self._last_sample_mono = now_mono
        from profit_optimizer.path_jsonl import compact_underlying_row

        if len(self.underlying_path) >= max_path_samples():
            return False, False
        row = sample.to_dict()
        row["path_phase"] = path_phase
        self.underlying_path.append(compact_underlying_row(row))
        px = float(sample.underlying_price)
        mfe_up = mae_up = False
        if px > self._hwm_underlying:
            self._hwm_underlying = px
            self._mfe_ts = sample.timestamp
            mfe_up = True
        if px < self._lwm_underlying or self._lwm_underlying <= 0:
            self._lwm_underlying = px
            self._mae_ts = sample.timestamp
            mae_up = True
        return mfe_up, mae_up

    @staticmethod
    def _effective_premium(sample: PremiumPathSample) -> Optional[float]:
        # Prefer mark (caller should set mark = STC bid / executable), then bid, then mid/ask.
        for v in (sample.mark, sample.bid, sample.mid, sample.ask):
            if v is not None and float(v) > 0:
                return float(v)
        return None

    def _premium_at_offset_seconds(self, offset_sec: float) -> Optional[float]:
        peak_dt = _parse_ts(self._peak_ts)
        if not peak_dt:
            return None
        target = peak_dt.timestamp() + offset_sec
        best: Optional[Tuple[float, Dict[str, Any]]] = None
        for row in self.premium_path:
            ts = _parse_ts(str(row.get("timestamp") or ""))
            if not ts:
                continue
            delta = abs(ts.timestamp() - target)
            if best is None or delta < best[0]:
                best = (delta, row)
        if not best:
            return None
        row = best[1]
        for k in ("mark", "mid", "bid", "ask"):
            v = row.get(k)
            if v is not None and float(v) > 0:
                return float(v)
        return None

    def _last_path_premium(self) -> Optional[float]:
        if not self.premium_path:
            return self._last_premium
        row = self.premium_path[-1]
        for k in ("mark", "mid", "bid", "ask"):
            v = row.get(k)
            if v is not None and float(v) > 0:
                return float(v)
        return self._last_premium

    def build_premium_metrics(
        self,
        exit_premium: Optional[float] = None,
        *,
        session_scope: bool = False,
    ) -> PremiumMetrics:
        entry = float(self.entry_premium or 0.0)
        hwm = float(self._hwm_premium)
        lwm = float(self._lwm_premium)
        peak = float(self._peak_premium)
        mfe = max(0.0, hwm - entry) if entry > 0 else None
        mae = max(0.0, entry - lwm) if entry > 0 else None
        mfe_pct = _safe_div(mfe, entry) if mfe is not None and entry > 0 else None
        mae_pct = _safe_div(mae, entry) if mae is not None and entry > 0 else None
        drawdown = max(0.0, hwm - lwm)
        if session_scope:
            exit_p = exit_premium
            eod_p = self._last_path_premium()
        else:
            exit_p = exit_premium if exit_premium is not None else self._last_path_premium()
            eod_p = exit_p if exit_p is not None else self._last_path_premium()

        capture_pct = _safe_div(float(exit_p), peak) if exit_p is not None and peak > 0 else None
        net_opp = peak - entry
        capture_net = None
        if exit_p is not None and net_opp > 0 and exit_p >= entry:
            capture_net = _safe_div(float(exit_p) - entry, net_opp)
        missed_prem = _safe_div(peak - float(exit_p), peak) if exit_p is not None and peak > 0 else None
        missed_opp = (1.0 - capture_net) if capture_net is not None else None

        p5 = self._premium_at_offset_seconds(300.0)
        p15 = self._premium_at_offset_seconds(900.0)
        p30 = self._premium_at_offset_seconds(1800.0)

        def _theta_pct(after: Optional[float]) -> Optional[float]:
            if peak > 0 and after is not None:
                return _safe_div(peak - float(after), peak)
            return None

        theta_decay = (peak - float(p5)) if peak > 0 and p5 is not None else None
        theta_pct = _safe_div(theta_decay, peak) if theta_decay is not None else None

        return PremiumMetrics(
            premium_mfe=mfe,
            premium_mae=mae,
            premium_hwm=hwm,
            premium_lwm=lwm,
            time_to_mfe_seconds=_seconds_between(self.entry_time, self._mfe_ts),
            time_to_mae_seconds=_seconds_between(self.entry_time, self._mae_ts),
            peak_premium=peak,
            peak_underlying=self._hwm_underlying if self._hwm_underlying > 0 else None,
            largest_premium_drawdown=drawdown if drawdown > 0 else None,
            premium_mfe_pct=mfe_pct,
            premium_mae_pct=mae_pct,
            max_favorable_premium_pct=mfe_pct,
            max_adverse_premium_pct=mae_pct,
            premium_at_peak=peak,
            premium_5m_after_peak=p5,
            premium_15m_after_peak=p15,
            premium_30m_after_peak=p30,
            premium_at_eod=eod_p,
            theta_decay_after_peak=theta_decay,
            theta_decay_pct=theta_pct,
            theta_decay_5m_after_peak_pct=_theta_pct(p5),
            theta_decay_15m_after_peak_pct=_theta_pct(p15),
            theta_decay_30m_after_peak_pct=_theta_pct(p30),
            capture_pct=capture_pct,
            capture_of_net_opportunity_pct=capture_net,
            missed_premium_pct=missed_prem,
            missed_opportunity_pct=missed_opp,
        )

    def build_underlying_metrics(self, exit_price: Optional[float] = None) -> UnderlyingMetrics:
        entry = float(self.entry_underlying or 0.0)
        if entry <= 0:
            return UnderlyingMetrics()
        is_long = str(self.direction or "LONG").upper() in ("LONG", "BUY", "CALL")
        hwm = float(self._hwm_underlying)
        lwm = float(self._lwm_underlying)
        if is_long:
            mfe = max(0.0, hwm - entry)
            mae = max(0.0, entry - lwm)
            peak = hwm
            pullback = max(0.0, hwm - (exit_price if exit_price is not None else lwm))
        else:
            mfe = max(0.0, entry - lwm)
            mae = max(0.0, hwm - entry)
            peak = lwm if lwm > 0 else entry
            pullback = max(0.0, (exit_price if exit_price is not None else hwm) - lwm)
        return UnderlyingMetrics(
            underlying_mfe=mfe,
            underlying_mae=mae,
            time_to_mfe_seconds=_seconds_between(self.entry_time, self._mfe_ts),
            time_to_mae_seconds=_seconds_between(self.entry_time, self._mae_ts),
            peak_price=peak,
            largest_pullback=pullback if pullback > 0 else None,
        )
