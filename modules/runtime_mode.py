#!/usr/bin/env python3
"""
Canonical runtime mode resolver for Easy ORB Strategy.

Single source of truth: ``MODE=demo`` | ``live``

- **Demo Mode:** live broker **market data** + **simulated ledger only** (no broker order submit).
- **Live Mode:** live broker market data + real broker account orders.

Broker selection is separate: ``BROKER=etrade`` (etc.) via ``resolve_broker_kind()``.

``ETRADE_MODE`` is **deprecated** — do not read it in application code. It may still appear in
old deploy env for conflict detection only; startup rejects ``ETRADE_MODE`` ≠ ``MODE``.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_MODE_RESOLVED: Optional["RuntimeMode"] = None
_MODE_AUDIT_DONE = False


class RuntimeMode(str, Enum):
    DEMO = "demo"
    LIVE = "live"

    @property
    def is_demo(self) -> bool:
        return self is RuntimeMode.DEMO

    @property
    def is_live(self) -> bool:
        return self is RuntimeMode.LIVE

    @property
    def label(self) -> str:
        return "DEMO" if self.is_demo else "LIVE"


_LEGACY_MODE_KEYS: Tuple[str, ...] = (
    "MODE",
    "ETRADE_MODE",
    "TRADING_MODE",
    "DEPLOYMENT_MODE",
    "AUTOMATION_MODE",
)

_LEGACY_BOOL_KEYS: Tuple[Tuple[str, bool], ...] = (
    ("DEMO_MODE_ENABLED", True),
    ("LIVE_MODE_ENABLED", False),
)


def _normalize_mode_token(raw: Optional[str]) -> Optional[RuntimeMode]:
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if token in ("demo", "demo_mode", "mock", "simulation", "sim"):
        return RuntimeMode.DEMO
    if token in ("live", "live_mode", "production", "real", "prod_trading"):
        return RuntimeMode.LIVE
    return None


def _legacy_mode_values() -> Dict[str, Optional[RuntimeMode]]:
    out: Dict[str, Optional[RuntimeMode]] = {}
    for key in _LEGACY_MODE_KEYS:
        val = os.getenv(key)
        if val is not None and str(val).strip() != "":
            out[key] = _normalize_mode_token(val)
    demo_en = os.getenv("DEMO_MODE_ENABLED")
    live_en = os.getenv("LIVE_MODE_ENABLED")
    if demo_en is not None and str(demo_en).strip() != "":
        out["DEMO_MODE_ENABLED"] = RuntimeMode.DEMO if str(demo_en).lower() == "true" else RuntimeMode.LIVE
    if live_en is not None and str(live_en).strip() != "":
        out["LIVE_MODE_ENABLED"] = RuntimeMode.LIVE if str(live_en).lower() == "true" else RuntimeMode.DEMO
    return out


def resolve_runtime_mode(*, force_refresh: bool = False) -> RuntimeMode:
    """Resolve canonical MODE (cached after first call unless force_refresh)."""
    global _MODE_RESOLVED
    if _MODE_RESOLVED is not None and not force_refresh:
        return _MODE_RESOLVED

    canonical = _normalize_mode_token(os.getenv("MODE"))
    legacy = _legacy_mode_values()

    if canonical is None:
        for key in ("TRADING_MODE", "DEPLOYMENT_MODE", "AUTOMATION_MODE"):
            if key in legacy and legacy[key] is not None:
                canonical = legacy[key]
                log.warning(
                    "RUNTIME_MODE_FALLBACK | using legacy %s=%s — set MODE=demo|live explicitly",
                    key,
                    legacy[key].value,
                )
                break
        if canonical is None:
            if legacy.get("LIVE_MODE_ENABLED") is RuntimeMode.LIVE:
                canonical = RuntimeMode.LIVE
            elif legacy.get("DEMO_MODE_ENABLED") is RuntimeMode.DEMO:
                canonical = RuntimeMode.DEMO
            else:
                canonical = RuntimeMode.DEMO

    _MODE_RESOLVED = canonical
    return canonical


def assert_mode_config_consistency(*, strict: bool = True) -> None:
    """
    Reject split-brain mode configuration at startup.

    Raises ValueError when explicit legacy keys disagree with MODE or each other.
    """
    canonical = resolve_runtime_mode(force_refresh=True)
    legacy = _legacy_mode_values()
    conflicts: List[str] = []

    mode_raw = os.getenv("MODE")
    if mode_raw is not None and str(mode_raw).strip() != "":
        explicit_mode = _normalize_mode_token(mode_raw)
        if explicit_mode is None:
            conflicts.append(f"MODE={mode_raw!r} is not demo|live")
        elif explicit_mode != canonical:
            conflicts.append(f"MODE={mode_raw!r} resolves inconsistently")

    for key, parsed in legacy.items():
        if key == "MODE" or parsed is None:
            continue
        if parsed != canonical:
            raw = os.getenv(key)
            conflicts.append(f"{key}={raw!r} ({parsed.value}) != MODE={canonical.value}")

    if conflicts:
        msg = (
            f"SPLIT_BRAIN_MODE_CONFIG | canonical={canonical.value} | "
            f"conflicts={conflicts}"
        )
        log.error(msg)
        if strict:
            raise ValueError(msg)
    else:
        log.info(
            "RUNTIME_MODE_RESOLVED | mode=%s | label=%s | legacy_keys_checked=%s",
            canonical.value,
            canonical.label,
            sorted(k for k in legacy if legacy[k] is not None),
        )


def audit_startup_mode(*, strict: bool = True) -> RuntimeMode:
    """Resolve mode, assert consistency, emit one startup audit line."""
    global _MODE_AUDIT_DONE
    mode = resolve_runtime_mode(force_refresh=True)
    assert_mode_config_consistency(strict=strict)
    if not _MODE_AUDIT_DONE:
        _MODE_AUDIT_DONE = True
        log.info(
            "STARTUP_MODE_AUDIT | mode=%s | broker_orders=%s | mock_executor=%s",
            mode.value,
            "enabled" if broker_execution_enabled() else "disabled",
            "disabled" if broker_execution_enabled() else ("enabled" if mode.is_demo else "disabled"),
        )
    return mode


def is_demo_mode() -> bool:
    return resolve_runtime_mode().is_demo


def is_live_mode() -> bool:
    return resolve_runtime_mode().is_live


def broker_execution_enabled() -> bool:
    """
    Broker order submit/close — **LIVE mode only** (``MODE=live``).

    DEMO (``MODE=demo``) never places broker orders; fills and closes use the
    simulated ledger (``MockOptionsExecutor``). Both modes may still read live
    option chains/quotes via E*TRADE market-data APIs.
    """
    return is_live_mode()


def broker_submits_enabled() -> bool:
    """Broker adapter may submit orders (not blocked by demo guard)."""
    return broker_execution_enabled()


def mode_label() -> str:
    return resolve_runtime_mode().label


def sync_legacy_mode_env(mode: Optional[RuntimeMode] = None) -> None:
    """Write legacy keys from canonical MODE (not ETRADE_MODE — retired)."""
    m = mode or resolve_runtime_mode()
    os.environ["MODE"] = m.value
    os.environ["TRADING_MODE"] = m.value
    os.environ["DEPLOYMENT_MODE"] = m.value
    os.environ["AUTOMATION_MODE"] = m.value
    os.environ["DEMO_MODE_ENABLED"] = "true" if m.is_demo else "false"
    os.environ["LIVE_MODE_ENABLED"] = "true" if m.is_live else "false"


def resolve_broker_kind() -> str:
    """Canonical BROKER token (etrade | interactive_brokers | tastytrade)."""
    try:
        from modules.broker_adapter import BrokerKind

        return BrokerKind.from_env(
            os.getenv("BROKER") or os.getenv("BROKER_TYPE")
        ).value
    except Exception:
        return "etrade"


def live_spreads_enabled() -> bool:
    """Live spread orders require explicit LIVE_SPREADS_ENABLED=true."""
    if is_demo_mode():
        return True
    return os.getenv("LIVE_SPREADS_ENABLED", "false").strip().lower() == "true"


_DEMO_CLOUD_SERVICE_NAMES = frozenset({"easy-etrade-strategy"})
_LIVE_CLOUD_SERVICE_NAMES = frozenset({"easy-etrade-strategy-live"})

GCS_RESEARCH_NAMESPACE = "research_app"
GCS_FINAL_NAMESPACE = "final_app"


def cloud_run_service_name() -> str:
    """Cloud Run service id (K_SERVICE) or configured GCP_SERVICE_NAME."""
    return (os.getenv("K_SERVICE") or os.getenv("GCP_SERVICE_NAME") or "").strip()


def is_live_cloud_service() -> bool:
    svc = cloud_run_service_name()
    if svc in _LIVE_CLOUD_SERVICE_NAMES:
        return True
    return bool(svc.endswith("-live") and "etrade" in svc.lower())


def is_demo_cloud_service() -> bool:
    """
    True on the RESEARCH Cloud Run service (``easy-etrade-strategy``).

    Session artifacts stay under ``research_app/`` regardless of MODE=demo|live
    so the RESEARCH desk never collides with the FINAL sibling app.
    """
    if is_live_cloud_service():
        return False
    svc = cloud_run_service_name()
    if svc in _DEMO_CLOUD_SERVICE_NAMES:
        return True
    return bool(svc and not svc.endswith("-live"))


def gcs_namespace() -> str:
    """GCS path prefix — Cloud Run service identity wins over MODE for dual-app isolation."""
    if is_live_cloud_service():
        return GCS_FINAL_NAMESPACE
    if is_demo_cloud_service():
        return GCS_RESEARCH_NAMESPACE
    # Local: match checkout path (parity with gcs_app_label) so FINAL ≠ research_app/
    try:
        from pathlib import Path as _P

        here = str(_P(__file__).resolve())
        if "FINAL APP" in here or "Final App" in here:
            return GCS_FINAL_NAMESPACE
        if "RESEARCH APP" in here or "Research App" in here:
            return GCS_RESEARCH_NAMESPACE
    except Exception:
        pass
    return GCS_RESEARCH_NAMESPACE if is_demo_mode() else GCS_FINAL_NAMESPACE


def gcs_app_label() -> str:
    """Sibling app label for snapshot payloads/filenames: FINAL or RESEARCH."""
    if is_live_cloud_service():
        return "FINAL"
    if is_demo_cloud_service():
        return "RESEARCH"
    svc = cloud_run_service_name().lower()
    if "live" in svc:
        return "FINAL"
    # Local: GCP_SERVICE_NAME / path heuristic
    try:
        from pathlib import Path as _P
        here = str(_P(__file__).resolve())
        if "FINAL APP" in here or "Final App" in here:
            return "FINAL"
        if "RESEARCH APP" in here or "Research App" in here:
            return "RESEARCH"
    except Exception:
        pass
    return "RESEARCH" if is_demo_mode() else "FINAL"



def alert_dedup_service_token() -> str:
    """Stable token for per-service alert dedup keys (RESEARCH vs FINAL Cloud Run)."""
    svc = cloud_run_service_name()
    if svc:
        return svc.replace("-", "_")
    if is_live_cloud_service():
        return "easy_etrade_strategy_live"
    if is_demo_cloud_service():
        return "easy_etrade_strategy"
    return gcs_namespace().replace("/", "_")


def gcs_namespaced_coord_prefix(relative: str) -> str:
    """Prefix cross-instance coordination paths with research_app/ or final_app/."""
    rel = (relative or "").strip("/")
    if not rel:
        rel = gcs_namespace()
    ns = gcs_namespace()
    if rel == ns or rel.startswith(f"{ns}/"):
        return rel
    return f"{ns}/{rel}"


def gcs_mock_options_history_path() -> str:
    return f"{gcs_namespace()}/mock_options_history.json"


def gcs_mock_trading_history_path() -> str:
    return f"{gcs_namespace()}/mock_trading_history.json"


def gcs_execution_snapshots_prefix() -> str:
    return f"{gcs_namespace()}/priority_optimizer/execution_snapshots/"


def gcs_execution_snapshot_object(filename: str) -> str:
    return f"{gcs_execution_snapshots_prefix()}{filename.lstrip('/')}"


def gcs_execution_snapshot_list_prefixes() -> Tuple[str, ...]:
    """GCS list prefixes for retrieve — both sibling app namespaces."""
    return (
        f"{GCS_RESEARCH_NAMESPACE}/priority_optimizer/execution_snapshots/",
        f"{GCS_FINAL_NAMESPACE}/priority_optimizer/execution_snapshots/",
        gcs_execution_snapshots_prefix(),
    )


def gcs_profit_optimizer_daily_prefix() -> str:
    """Per-app profit_optimizer daily JSON prefix (research_app/ | final_app/)."""
    return f"{gcs_namespace()}/profit_optimizer/daily/"


def gcs_profit_optimizer_cohort_prefix() -> str:
    return f"{gcs_namespace()}/profit_optimizer/cohorts/"


def gcs_profit_optimizer_daily_object(session_date: str) -> str:
    return f"{gcs_profit_optimizer_daily_prefix()}{str(session_date).strip()}.json"


def gcs_profit_optimizer_cohort_object(session_date: str) -> str:
    return (
        f"{gcs_profit_optimizer_cohort_prefix()}"
        f"{str(session_date).strip()}_profit_optimizer_cohort.csv"
    )


def gcs_profit_optimizer_paths_prefix() -> str:
    return f"{gcs_namespace()}/profit_optimizer/paths/"


def gcs_profit_optimizer_list_prefixes() -> Tuple[str, ...]:
    """Retrieve both sibling apps + legacy flat DEMO path."""
    return (
        f"{GCS_RESEARCH_NAMESPACE}/profit_optimizer/daily/",
        f"{GCS_FINAL_NAMESPACE}/profit_optimizer/daily/",
        "profit_optimizer/daily/",
        gcs_profit_optimizer_daily_prefix(),
    )


def gcs_profit_optimizer_paths_list_prefixes() -> Tuple[str, ...]:
    return (
        f"{GCS_RESEARCH_NAMESPACE}/profit_optimizer/paths/",
        f"{GCS_FINAL_NAMESPACE}/profit_optimizer/paths/",
        "profit_optimizer/paths/",
        gcs_profit_optimizer_paths_prefix(),
    )


def gcs_trendline_break_snapshots_prefix() -> str:
    return f"{gcs_namespace()}/priority_optimizer/trendline_break_snapshots/"


def gcs_trendline_break_snapshot_object(filename: str) -> str:
    return f"{gcs_trendline_break_snapshots_prefix()}{filename.lstrip('/')}"


def gcs_trendline_break_snapshot_list_prefixes() -> Tuple[str, ...]:
    """Namespaced + legacy flat break-snapshot prefixes for read/retrieve."""
    return (
        gcs_trendline_break_snapshots_prefix(),
        f"{GCS_RESEARCH_NAMESPACE}/priority_optimizer/trendline_break_snapshots/",
        f"{GCS_FINAL_NAMESPACE}/priority_optimizer/trendline_break_snapshots/",
        "priority_optimizer/trendline_break_snapshots/",
    )


def gcs_trendline_outcomes_prefix() -> str:
    return f"{gcs_namespace()}/priority_optimizer/trendline_outcomes/"


def gcs_trendline_outcomes_object(filename: str) -> str:
    return f"{gcs_trendline_outcomes_prefix()}{filename.lstrip('/')}"


def gcs_daily_signals_prefix() -> str:
    return f"{gcs_namespace()}/priority_optimizer/daily_signals/"


def gcs_daily_signals_object(date_str: str) -> str:
    return f"{gcs_daily_signals_prefix()}{str(date_str).strip()}_signals.json"


def gcs_daily_signals_list_prefixes() -> Tuple[str, ...]:
    """Namespaced + legacy flat daily_signals prefixes (write ns; prune/read both)."""
    return (
        gcs_daily_signals_prefix(),
        f"{GCS_RESEARCH_NAMESPACE}/priority_optimizer/daily_signals/",
        f"{GCS_FINAL_NAMESPACE}/priority_optimizer/daily_signals/",
        "priority_optimizer/daily_signals/",
    )


def gcs_daily_markers_path(relative: str) -> str:
    """Per-app daily marker path: {research_app|final_app}/daily_markers/…"""
    return f"{gcs_namespace()}/daily_markers/{relative.lstrip('/')}"


def gcs_alert_marker_path(marker_key: str) -> str:
    """Per-service alert dedup marker (research_app/ vs final_app/ — no cross-app dedup)."""
    return f"{gcs_namespace()}/alert_markers/{marker_key}.txt"


def gcs_signal_collection_marker_path(marker_key: str) -> str:
    return f"{gcs_namespace()}/signal_collection_markers/{marker_key}.txt"


def gcs_eod_marker_path(marker_key: str) -> str:
    return f"{gcs_namespace()}/eod_markers/{marker_key}.txt"


def gcs_read_daily_markers_string(gcs: Any, relative: str) -> Optional[str]:
    """Read a daily_markers blob from the app-scoped GCS prefix."""
    if not gcs or not getattr(gcs, "enabled", False):
        return None
    return gcs.read_string(gcs_daily_markers_path(relative))


def executor_type_label(*, demo_mode: Optional[bool] = None) -> str:
    if broker_execution_enabled():
        return "broker_adapter"
    dm = demo_mode if demo_mode is not None else is_demo_mode()
    return "mock_executor" if dm else "broker_adapter"


def account_source_label(*, demo_mode: Optional[bool] = None) -> str:
    if broker_execution_enabled():
        return f"broker:{resolve_broker_kind()}"
    dm = demo_mode if demo_mode is not None else is_demo_mode()
    return "simulated_ledger" if dm else f"broker:{resolve_broker_kind()}"


def enabled_strategies_label() -> str:
    parts: List[str] = []
    if os.getenv("ENABLE_0DTE_STRATEGY", "false").lower() == "true":
        parts.append("ORB_0DTE")
    if os.getenv("ENABLE_TRENDLINE_STRATEGY", "false").lower() == "true":
        parts.append("Trendline_0DTE")
    if os.getenv("ENABLE_BREAKOUT_STRATEGY", "false").lower() == "true":
        parts.append("Breakout_0DTE")
    if os.getenv("ENABLE_ORB_STRATEGY", "false").lower() == "true":
        parts.append("ORB_SO")
    return ",".join(parts) if parts else "none"


def assert_config_mode_consistency(
    *,
    config_mode: Any,
    options_executor_demo_mode: Optional[bool] = None,
    strict: bool = True,
) -> None:
    """
    Ensure TradingConfig.mode and OptionsTradingExecutor.demo_mode agree with MODE.

    Emits CONFIG_MODE_CONSISTENCY_AUDIT and raises ValueError on disagreement.
    """
    runtime = resolve_runtime_mode(force_refresh=True)
    expected_live = runtime.is_live
    expected_demo = runtime.is_demo

    cm_raw = str(getattr(config_mode, "value", config_mode) or "").lower()
    if cm_raw == "live_mode":
        config_is_live = True
    elif cm_raw == "demo_mode":
        config_is_live = False
    else:
        config_is_live = "live" in cm_raw and "demo" not in cm_raw.replace("live", "")

    config_label = "live_mode" if config_is_live else "demo_mode"
    executor_label = (
        str(options_executor_demo_mode)
        if options_executor_demo_mode is not None
        else "not_checked"
    )

    disagreements: List[str] = []
    if config_is_live != expected_live:
        disagreements.append(f"config.mode={config_label}")
    if options_executor_demo_mode is not None:
        if bool(options_executor_demo_mode) != expected_demo:
            disagreements.append(f"options_executor.demo_mode={options_executor_demo_mode}")

    ok = len(disagreements) == 0
    log.info(
        "CONFIG_MODE_CONSISTENCY_AUDIT | runtime_mode=%s | config.mode=%s | "
        "options_executor.demo_mode=%s | ok=%s",
        runtime.value,
        config_label,
        executor_label,
        str(ok).lower(),
    )

    if disagreements:
        msg = (
            f"CONFIG_MODE_SPLIT_BRAIN | runtime_mode={runtime.value} | "
            f"disagreements={disagreements}"
        )
        log.error(msg)
        if strict:
            raise ValueError(msg)


def audit_production_startup(
    *,
    broker_adapter: Any = None,
    demo_mode: Optional[bool] = None,
) -> None:
    """
    Emit deploy-safe startup audit line for production soak / Live cutover.

    Legacy mode keys cannot override MODE — assert_mode_config_consistency runs first.
    """
    mode = resolve_runtime_mode(force_refresh=True)
    assert_mode_config_consistency(strict=True)
    dm = mode.is_demo if demo_mode is None else bool(demo_mode)
    broker = resolve_broker_kind()
    spreads = live_spreads_enabled()
    adapter_ok = bool(
        broker_adapter and getattr(broker_adapter, "is_available", lambda: False)()
    )
    log.info(
        "PRODUCTION_STARTUP_AUDIT | MODE=%s | BROKER=%s | strategies=%s | "
        "executor=%s | account_source=%s | gcs_namespace=%s | "
        "LIVE_SPREADS_ENABLED=%s | broker_adapter_available=%s | broker_orders=%s",
        mode.value,
        broker,
        enabled_strategies_label(),
        executor_type_label(demo_mode=dm),
        account_source_label(demo_mode=dm),
        gcs_namespace(),
        str(spreads).lower(),
        adapter_ok,
        "enabled" if broker_execution_enabled() else "disabled",
    )
    try:
        from modules.broker_live_audits import audit_broker_account_routing

        et_api = getattr(broker_adapter, "_api", None) if broker_adapter else None
        audit_broker_account_routing(etrade_options_api=et_api)
    except Exception as routing_err:
        log.warning("BROKER_ACCOUNT_ROUTING_AUDIT skipped at startup: %s", routing_err)
