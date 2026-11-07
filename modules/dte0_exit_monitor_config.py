"""Canonical 0DTE exit-monitor configuration (ORB + Trendline, Demo + Live).

One resolver for market-data freshness, cadence, valuation side, and latch policy.

Open-position monitor cadence for ORB, Trendline, and Breakout:
``0DTE_POSITION_MONITOR_INTERVAL_SEC`` (default 2s). At
``0DTE_MONITOR_WIDE_OPEN_COUNT`` (default 20) the tick is
``0DTE_MONITOR_WIDE_INTERVAL_SEC`` (default 3s). The flight recorder uses that
same effective interval. Discovery is a separate 10s floor.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

log = logging.getLogger(__name__)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def resolve_0dte_position_monitor_interval_sec() -> float:
    """Single source of truth for open-position and profit-optimizer shadow cadence."""
    return max(0.5, _env_float("0DTE_POSITION_MONITOR_INTERVAL_SEC", 2.0))


def monitor_wide_open_count() -> int:
    """Switch from 2s to the wide interval at this many open positions."""
    return max(1, int(_env_float("0DTE_MONITOR_WIDE_OPEN_COUNT", 20.0)))


def monitor_wide_interval_sec() -> float:
    return max(
        resolve_0dte_position_monitor_interval_sec(),
        _env_float("0DTE_MONITOR_WIDE_INTERVAL_SEC", 3.0),
    )


def effective_position_monitor_interval_sec(open_count: int = 0) -> float:
    """2s while the book is small, 3s at 20+ open so 50-wide stays near the 180 cap."""
    base = resolve_0dte_position_monitor_interval_sec()
    try:
        n = int(open_count or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= monitor_wide_open_count():
        return float(monitor_wide_interval_sec())
    return float(base)


def _meta_dicts_for_position(pos: object) -> list:
    """Yield position metadata + normalized_options dicts (and top-level as a dict view)."""
    out = []
    top = {}
    for key in (
        "is_0dte",
        "calendar_dte",
        "dte",
        "expiry_selection_mode",
        "expiry_mode",
        "selected_expiry_ymd",
        "expiry_ymd",
        "fallback_days",
    ):
        if hasattr(pos, key):
            try:
                top[key] = getattr(pos, key)
            except Exception:
                pass
    if top:
        out.append(top)
    md = getattr(pos, "metadata", None)
    if isinstance(md, dict):
        out.append(md)
        norm = md.get("normalized_options")
        if isinstance(norm, dict):
            out.append(norm)
    return out


def position_needs_loose_exit_quote_age(pos: object) -> bool:
    """True for Selected-ED / weekly / nearest-to-0DTE (non-session-day) contracts.

    Closest-contract fills that are not same-day 0DTE must not be held to the
    tight 0DTE broker-clock gate — exits must still complete when triggered.
    """
    try:
        from modules.calendar_expiry_metadata import normalize_expiry_selection_mode
    except Exception:
        normalize_expiry_selection_mode = lambda m: str(m or "").strip()  # noqa: E731

    for md in _meta_dicts_for_position(pos):
        if md.get("is_0dte") is False:
            return True
        mode = str(
            normalize_expiry_selection_mode(
                md.get("expiry_selection_mode") or md.get("expiry_mode") or ""
            )
        ).strip().lower()
        if mode in (
            "selected_ed_override",
            "selected_ed",
            "nearest_to_0dte",
            "weekly",
            "explicit",
        ):
            # Same-day Selected-ED is still 0DTE; only loosen when DTE > 0 or is_0dte False.
            if md.get("is_0dte") is False:
                return True
            try:
                cd = md.get("calendar_dte")
                if cd is None:
                    cd = md.get("dte")
                if cd is not None and int(cd) > 0:
                    return True
            except (TypeError, ValueError):
                pass
            try:
                fb = md.get("fallback_days")
                if fb is not None and int(fb) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        try:
            cd = md.get("calendar_dte")
            if cd is None:
                cd = md.get("dte")
            if cd is not None and int(cd) > 0:
                return True
        except (TypeError, ValueError):
            pass
        try:
            fb = md.get("fallback_days")
            if fb is not None and int(fb) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def exit_quote_soft_stale_ok(*, positions: Optional[list] = None) -> bool:
    """When True, broker-clock age must not hard-reject a usable bid (exit completion)."""
    for pos in positions or []:
        if position_needs_loose_exit_quote_age(pos):
            return True
    return False


def effective_exit_quote_max_age_sec(
    *,
    base: Optional[float] = None,
    positions: Optional[list] = None,
) -> float:
    """Exit-quote age gate.

    Same-day (0DTE) stays tight (default 8s). Selected-ED / weekly contracts need a
    looser broker-clock budget so ``quote_age_exceeded`` does not permanently block
    scratch/hold exits on otherwise exit-grade marks.

    Soft-stale acceptance (see ``exit_quote_soft_stale_ok`` / ``apply_max_age``) is the
    hard guarantee that age alone cannot block exits; this budget prefers fresher marks.
    """
    cfg_base = (
        float(base)
        if base is not None
        else max(0.5, _env_float("0DTE_EXIT_QUOTE_MAX_AGE_SEC", 8.0))
    )
    needs_loose = any(position_needs_loose_exit_quote_age(p) for p in (positions or []))
    if needs_loose:
        return max(cfg_base, max(0.5, _env_float("NON_0DTE_EXIT_QUOTE_MAX_AGE_SEC", 30.0)))
    return cfg_base


@dataclass(frozen=True)
class ExitMonitorConfig:
    require_live_option_data: bool = True
    demo_synthetic_chain: bool = False
    allow_synthetic_exit_quotes: bool = False
    unified_monitor_enabled: bool = True
    position_monitor_interval_sec: float = 2.0
    exit_quote_mode: str = "exact_contract_batch"
    exit_quote_timeout_sec: float = 1.5
    exit_quote_max_age_sec: float = 8.0
    long_exit_price_field: str = "bid"
    mfe_hwm_price_field: str = "bid"
    giveback_price_field: str = "bid"
    exit_latch_enabled: bool = True
    blocking_exit_telemetry: bool = False
    # Same as position_monitor_interval_sec — kept for dataclass callers; no separate env.
    armed_position_monitor_interval_sec: float = 2.0
    ownership_grace_sec: float = 45.0
    ownership_sync_target_sec: float = 2.0
    ownership_block_warning_sec: float = 3.0
    ownership_block_critical_sec: float = 5.0
    ownership_urgent_sync_timeout_sec: float = 2.0
    quote_batch_concurrency: int = 2
    open_exact_quote_batch_size: int = 25
    force_chain_refresh_bridge: bool = True
    strict_exit_grade_enabled: bool = True
    eod_reconciliation_enabled: bool = True
    profit_retention_telemetry_enabled: bool = True


def resolve_exit_monitor_config() -> ExitMonitorConfig:
    """Resolve canonical 0DTE exit-monitor keys (no path-specific cadence aliases)."""
    require_live = _env_bool("REQUIRE_LIVE_OPTION_DATA", True)
    synth_chain = _env_bool("0DTE_DEMO_SYNTHETIC_CHAIN", False)
    allow_synth_exit = _env_bool("0DTE_ALLOW_SYNTHETIC_EXIT_QUOTES", False)
    interval = resolve_0dte_position_monitor_interval_sec()

    mode = str(os.getenv("0DTE_EXIT_QUOTE_MODE", "exact_contract_batch") or "exact_contract_batch").strip()
    return ExitMonitorConfig(
        require_live_option_data=require_live,
        demo_synthetic_chain=synth_chain,
        allow_synthetic_exit_quotes=allow_synth_exit,
        unified_monitor_enabled=_env_bool("0DTE_UNIFIED_POSITION_MONITOR_ENABLED", True),
        position_monitor_interval_sec=interval,
        exit_quote_mode=mode.lower(),
        exit_quote_timeout_sec=max(0.2, _env_float("0DTE_EXIT_QUOTE_TIMEOUT_SEC", 1.5)),
        exit_quote_max_age_sec=max(0.5, _env_float("0DTE_EXIT_QUOTE_MAX_AGE_SEC", 8.0)),
        long_exit_price_field=str(os.getenv("0DTE_LONG_EXIT_PRICE_FIELD", "bid") or "bid").strip().lower(),
        mfe_hwm_price_field=str(os.getenv("0DTE_MFE_HWM_PRICE_FIELD", "bid") or "bid").strip().lower(),
        giveback_price_field=str(os.getenv("0DTE_GIVEBACK_PRICE_FIELD", "bid") or "bid").strip().lower(),
        exit_latch_enabled=_env_bool("0DTE_EXIT_LATCH_ENABLED", True),
        blocking_exit_telemetry=_env_bool("0DTE_BLOCKING_EXIT_TELEMETRY", False),
        armed_position_monitor_interval_sec=interval,
        ownership_grace_sec=max(0.0, _env_float("0DTE_OWNERSHIP_GRACE_SEC", 45.0)),
        ownership_sync_target_sec=max(0.1, _env_float("0DTE_OWNERSHIP_SYNC_TARGET_SEC", 2.0)),
        ownership_block_warning_sec=max(0.1, _env_float("0DTE_OWNERSHIP_BLOCK_WARNING_SEC", 3.0)),
        ownership_block_critical_sec=max(0.1, _env_float("0DTE_OWNERSHIP_BLOCK_CRITICAL_SEC", 5.0)),
        ownership_urgent_sync_timeout_sec=max(
            0.2, _env_float("0DTE_OWNERSHIP_URGENT_SYNC_TIMEOUT_SEC", 2.0)
        ),
        quote_batch_concurrency=max(1, int(_env_float("0DTE_EXIT_QUOTE_BATCH_CONCURRENCY", 4.0))),
        open_exact_quote_batch_size=min(
            25, max(1, int(_env_float("0DTE_OPEN_EXACT_QUOTE_BATCH_SIZE", 25.0)))
        ),
        force_chain_refresh_bridge=_env_bool("0DTE_FORCE_CHAIN_REFRESH_BRIDGE", True),
        strict_exit_grade_enabled=_env_bool("0DTE_STRICT_EXIT_GRADE_ENABLED", True),
        eod_reconciliation_enabled=_env_bool("0DTE_EOD_RECONCILIATION_ENABLED", True),
        profit_retention_telemetry_enabled=_env_bool("0DTE_PROFIT_RETENTION_TELEMETRY_ENABLED", True),
    )


def synthetic_exit_quotes_allowed() -> bool:
    return bool(resolve_exit_monitor_config().allow_synthetic_exit_quotes)


def exit_grade_source_allowed(source: str) -> bool:
    """Delegate to strict exit-grade policy when enabled."""
    try:
        from modules.exit_grade_policy import classify_exit_grade_source, strict_exit_grade_enabled

        if strict_exit_grade_enabled():
            return bool(
                classify_exit_grade_source(
                    premium_source=source,
                    raw_bid=0.01,
                    contract_matched=True,
                    skip_cache_bridge=True,
                ).exit_grade
            )
    except Exception:
        pass
    src = str(source or "").strip().lower()
    if not src:
        return False
    banned = (
        "synthetic",
        "modeled",
        "proxy",
        "delta_estimate",
        "underlying_proxy",
        "entry_synthetic",
        "entry_stored",
        "missing",
        "invalid",
        "stale",
    )
    if any(tok in src for tok in banned):
        return False
    if not synthetic_exit_quotes_allowed() and "cache" in src and "fresh" not in src:
        if "exact_contract" not in src and "exact_quote" not in src:
            return False
    trusted = (
        "exact_quote",
        "bid_executable",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "fresh_chain_bridge",
    )
    try:
        from modules.exit_grade_durability import quote_source_is_exact_contract

        if quote_source_is_exact_contract(src):
            return True
    except Exception:
        if src.startswith("exact_quote") or src.startswith("exact_contract"):
            return True
    return src in trusted


def liquidatable_metrics(
    *,
    entry_fill_price: float,
    current_bid: Optional[float],
    prior_hwm_bid: Optional[float],
    arm_pct: float = 10.0,
    giveback_pct: float = 7.0,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], bool, bool]:
    """Bid-only liquidatable series for long options.

    Returns:
      liquidatable_pnl_pct, liquidatable_hwm_bid, liquidatable_mfe_pct,
      giveback_from_hwm_pct, mfe_giveback_armed, mfe_giveback_triggered

    Valid bid of 0.00 is accepted (worthless long).
    """
    entry = float(entry_fill_price or 0.0)
    if current_bid is None:
        return None, prior_hwm_bid, None, None, False, False
    try:
        bid = float(current_bid)
    except (TypeError, ValueError):
        return None, prior_hwm_bid, None, None, False, False
    if entry <= 0:
        return None, prior_hwm_bid, None, None, False, False

    pnl_pct = (bid / entry - 1.0) * 100.0
    hwm = float(prior_hwm_bid) if prior_hwm_bid is not None else bid
    try:
        hwm = float(hwm)
    except (TypeError, ValueError):
        hwm = bid
    if bid > hwm:
        hwm = bid
    mfe_pct = (hwm / entry - 1.0) * 100.0
    giveback = (bid / hwm - 1.0) * 100.0 if hwm > 0 else None
    armed = mfe_pct >= float(arm_pct) - 1e-9
    triggered = bool(
        armed and giveback is not None and giveback <= -float(giveback_pct) + 1e-9
    )
    return pnl_pct, hwm, mfe_pct, giveback, armed, triggered
