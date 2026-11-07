"""Broker Position Synchronizer — sole Live mutator of the 0DTE position registry.

Live ownership rule (MODE=live):
  No module except BrokerPositionSynchronizer may create, update (lifecycle),
  quarantine, close, or remove a registry position.

Live entry:
  fill confirmed → ConfirmedOpenFact → register_confirmed_fill() → PositionRecord

Live close:
  close fill → ConfirmedCloseFact → reconcile_confirmed_close()
  Registry removal only when broker truth confirms flat (qty 0 / missing).

Demo: synchronize() is a no-op; Demo open/close may write executor.positions.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from modules.position_registry_contract import (
    SYNC_FAIL_STREAK_CRITICAL,
    PENDING_BROKER_VISIBILITY_GRACE_SEC,
    ConfirmedCloseFact,
    ConfirmedOpenFact,
    EntrySource,
    ManagementPolicy,
    MismatchKind,
    PremiumApplyResult,
    REPAIR_POLICY,
    ReconciliationStatus,
    STRATEGY_RECOVERED_UNKNOWN,
    is_sync_unavailable_reason,
    parse_occ_symbol,
    recovered_unknown_meta,
)

log = logging.getLogger(__name__)


# Backward-compatible alias for older call sites / tests.
@dataclass
class BrokerFill:
    trade_id: str
    occ_contract_symbol: str = ""
    underlying: str = ""
    quantity: int = 0
    average_fill: float = 0.0
    broker_order_id: str = ""
    side: str = ""  # open | close
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairAction:
    kind: str
    position_id: str
    detail: str = ""
    contract: str = ""


@dataclass
class SynchronizationResult:
    skipped: bool = False
    reason: str = ""
    broker_count: int = 0
    local_count: int = 0
    repairs: List[RepairAction] = field(default_factory=list)
    mismatches_remaining: List[str] = field(default_factory=list)
    pass_ok: bool = True
    sync_unavailable: bool = False
    registry_stale: bool = False
    removed_position_ids: List[str] = field(default_factory=list)

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "pass": self.pass_ok,
            "skipped": self.skipped,
            "reason": self.reason,
            "broker_count": self.broker_count,
            "local_count": self.local_count,
            "sync_unavailable": self.sync_unavailable,
            "registry_stale": self.registry_stale,
            "repairs": [f"{r.kind}:{r.position_id}" for r in self.repairs],
            "mismatches_remaining": list(self.mismatches_remaining),
            "removed": list(self.removed_position_ids),
        }


class BrokerPositionSynchronizer:
    """Sole Live writer API for options_executor.positions (canonical book)."""

    def __init__(self, system: Any = None) -> None:
        self._system = system

    def bind_system(self, system: Any) -> None:
        self._system = system

    @contextmanager
    def _live_write_token(self, executor: Any) -> Iterator[None]:
        prev = bool(getattr(executor, "_bps_write_token", False))
        try:
            executor._bps_write_token = True
            yield
        finally:
            executor._bps_write_token = prev

    def put_position(self, executor: Any, position: Any) -> None:
        """Create or replace a registry position (Live sole-writer)."""
        pid = str(getattr(position, "position_id", "") or "")
        if not pid:
            raise ValueError("put_position requires position_id")
        with self._live_write_token(executor):
            executor.positions[pid] = position
            if hasattr(executor, "_assert_live_registry_write_allowed"):
                # Asserted after token set for optional executors that gate writes.
                pass

    def remove_position(self, executor: Any, position_id: str) -> Any:
        """Remove a registry position (Live sole-writer). Returns prior object if any."""
        pid = str(position_id or "")
        positions = getattr(executor, "positions", None)
        if not isinstance(positions, dict) or pid not in positions:
            return None
        with self._live_write_token(executor):
            prior = positions.pop(pid, None)
        if self._system is not None:
            try:
                from modules.open_position_recovery import rebuild_system_position_registry

                rebuild_system_position_registry(self._system)
            except Exception:
                pass
        return prior

    def _resolve_registry_position(
        self, executor: Any, position_id: str
    ) -> tuple[Optional[Any], str]:
        """Resolve the authoritative open position for premium patches.

        Demo → MockOptionsExecutor.active_positions (not executor.positions).
        Live → executor.positions (synchronizer-owned membership).
        """
        pid = str(position_id or "").strip()
        if not pid or executor is None:
            return None, ""
        demo = bool(getattr(executor, "demo_mode", True))
        if demo:
            mock = getattr(executor, "mock_executor", None)
            active = getattr(mock, "active_positions", None) if mock is not None else None
            if isinstance(active, dict) and pid in active:
                return active[pid], "mock_active_positions"
            return None, "mock_active_positions"
        positions = getattr(executor, "positions", None)
        if isinstance(positions, dict) and pid in positions:
            return positions[pid], "executor_positions"
        return None, "executor_positions"

    def apply_premium_state_update(
        self,
        executor: Any,
        snapshot: Any,
        patch: Any,
    ) -> PremiumApplyResult:
        """Apply Premium Engine snapshot + patch to the authoritative registry row.

        Never reports applied=True unless the authoritative position object was mutated.
        Demo writes MockOptionsExecutor.active_positions; Live writes executor.positions.
        """
        pid = str(
            getattr(patch, "position_id", None)
            or getattr(snapshot, "position_id", None)
            or ""
        )
        if executor is None or snapshot is None or patch is None or not pid:
            return PremiumApplyResult(
                applied=False,
                position_id=pid,
                registry="",
                reason="missing_args",
            )

        pos, registry_name = self._resolve_registry_position(executor, pid)
        if pos is None:
            result = PremiumApplyResult(
                applied=False,
                position_id=pid,
                registry=registry_name or "unknown",
                reason="position_not_found",
            )
            log.error(
                "PREMIUM_STATE_APPLY_FAILED | position_id=%s | registry=%s | reason=%s",
                pid,
                result.registry,
                result.reason,
            )
            return result

        def _apply() -> None:
            cv = getattr(patch, "current_position_value", None)
            if cv is None:
                cv = getattr(snapshot, "current_value", None)
            if cv is not None:
                try:
                    pos.current_value = float(cv)
                except Exception:
                    pass
            dollars = getattr(patch, "unrealized_pnl_dollars", None)
            if dollars is None:
                dollars = getattr(snapshot, "unrealized_pnl_dollars", None)
            if dollars is not None:
                try:
                    pos.unrealized_pnl = float(dollars)
                except Exception:
                    pass

            md = getattr(pos, "metadata", None)
            if not isinstance(md, dict):
                md = {}
                pos.metadata = md
            norm = md.get("normalized_options")
            if isinstance(norm, dict) and cv is not None:
                norm["current_value"] = float(cv)

            for attr, key in (
                ("premium_hwm", "premium_hwm"),
                ("premium_lwm", "premium_lwm"),
                ("mfe_pct", "mfe_pct"),
                ("mae_pct", "mae_pct"),
                ("last_reliable_premium", "last_reliable_premium"),
                ("premium_health", "premium_health"),
            ):
                val = getattr(patch, attr, None)
                if val is not None:
                    md[key] = val
            ts = getattr(patch, "last_quote_timestamp", None)
            if isinstance(ts, datetime):
                md["last_quote_timestamp"] = ts.isoformat()
            elif ts is not None:
                md["last_quote_timestamp"] = ts
            deg = getattr(patch, "degraded_since", None)
            if deg is None and not getattr(snapshot, "degraded", False):
                md.pop("degraded_since", None)
            elif isinstance(deg, datetime):
                md["degraded_since"] = deg.isoformat()
            elif deg is not None:
                md["degraded_since"] = deg

            try:
                md["premium_snapshot"] = snapshot.to_dict() if hasattr(snapshot, "to_dict") else dict(snapshot)
            except Exception:
                pass

            osnap = md.get("option_stealth")
            if isinstance(osnap, dict):
                if getattr(patch, "premium_hwm", None) is not None:
                    osnap["premium_hwm"] = patch.premium_hwm
                if getattr(patch, "premium_lwm", None) is not None:
                    osnap["premium_lwm"] = patch.premium_lwm
                if getattr(snapshot, "executable_exit_price", None) is not None:
                    osnap["last_effective_premium"] = snapshot.executable_exit_price
                if getattr(patch, "last_reliable_premium", None) is not None:
                    osnap["last_reliable_premium"] = patch.last_reliable_premium
                # Keep stealth MFE/opportunity aligned with Premium Engine patch.
                mfe_v = getattr(patch, "mfe_pct", None)
                if mfe_v is None:
                    mfe_v = getattr(snapshot, "mfe_pct", None)
                if mfe_v is not None:
                    try:
                        mfe_f = float(mfe_v)
                        prev_max = float(osnap.get("max_pnl_pct") or 0.0)
                        osnap["max_pnl_pct"] = max(prev_max, mfe_f)
                        try:
                            from modules.po_mfe20_snapshot import mark_po_20p_due

                            mark_po_20p_due(osnap, prev_max, osnap.get("max_pnl_pct"))
                        except Exception:
                            pass
                        if osnap["max_pnl_pct"] >= 0.10:
                            osnap["had_opportunity"] = True
                        engine = osnap.get("engine")
                        if isinstance(engine, dict):
                            st_obj = engine.get("state")
                            if st_obj is not None and hasattr(st_obj, "max_pnl_pct"):
                                st_obj.max_pnl_pct = max(
                                    float(getattr(st_obj, "max_pnl_pct", 0.0) or 0.0),
                                    mfe_f,
                                )
                                if float(st_obj.max_pnl_pct) >= 0.10 and hasattr(st_obj, "had_opportunity"):
                                    st_obj.had_opportunity = True
                    except Exception:
                        pass

            for attr in ("premium_hwm", "premium_lwm", "mfe_pct", "mae_pct"):
                val = getattr(patch, attr, None)
                if val is not None and hasattr(pos, attr):
                    try:
                        setattr(pos, attr, val)
                    except Exception:
                        pass
            if hasattr(pos, "premium_snapshot"):
                try:
                    pos.premium_snapshot = snapshot
                except Exception:
                    pass

        try:
            # Live: hold write token so any gated mutators on the object pass.
            # Demo: token is harmless (gate bypasses when demo_mode).
            with self._live_write_token(executor):
                _apply()
        except Exception as err:
            log.error(
                "PREMIUM_STATE_APPLY_FAILED | position_id=%s | registry=%s | reason=apply_exception | error=%s",
                pid,
                registry_name,
                err,
            )
            return PremiumApplyResult(
                applied=False,
                position_id=pid,
                registry=registry_name,
                reason=f"apply_exception:{err}",
            )

        ok_logged = getattr(self, "_premium_apply_ok_logged", None)
        if not isinstance(ok_logged, set):
            ok_logged = set()
            self._premium_apply_ok_logged = ok_logged
        if pid not in ok_logged:
            ok_logged.add(pid)
            log.info(
                "PREMIUM_STATE_APPLY_OK | position_id=%s | registry=%s | first_apply=true",
                pid,
                registry_name,
            )
        else:
            log.debug(
                "PREMIUM_STATE_APPLY_OK | position_id=%s | registry=%s",
                pid,
                registry_name,
            )
        if registry_name == "mock_active_positions" and not getattr(
            self, "_demo_premium_provider_logged", False
        ):
            self._demo_premium_provider_logged = True
            log.warning(
                "DEMO_PREMIUM_PROVIDER | registry=mock_active_positions | "
                "premium_engine=true | path=mock_ledger"
            )
        return PremiumApplyResult(
            applied=True,
            position_id=pid,
            registry=registry_name,
            reason=None,
        )

    def apply_quote_observation(
        self,
        executor: Any,
        position_id: str,
        *,
        awaiting_market_data: Optional[bool] = None,
        quote_rejection_reason: Optional[str] = None,
        exact_quote: Optional[Dict[str, Any]] = None,
        quote_source: Optional[str] = None,
        tick_id: str = "",
    ) -> bool:
        """Apply monitor quote observation through the authoritative registry row."""
        pid = str(position_id or "").strip()
        if executor is None or not pid:
            return False
        pos, registry_name = self._resolve_registry_position(executor, pid)
        if pos is None:
            log.debug(
                "QUOTE_OBSERVATION_APPLY_SKIP | position_id=%s | reason=position_not_found",
                pid,
            )
            return False

        def _apply() -> None:
            md = getattr(pos, "metadata", None)
            if not isinstance(md, dict):
                md = {}
                pos.metadata = md
            if awaiting_market_data is not None:
                md["awaiting_market_data"] = bool(awaiting_market_data)
            if quote_rejection_reason is not None:
                md["quote_rejection_reason"] = str(quote_rejection_reason)
            if exact_quote is not None:
                md["exact_quote"] = exact_quote
                md["option_quote"] = exact_quote
            if quote_source is not None:
                md["last_quote_source"] = str(quote_source)
            if tick_id:
                md["last_quote_tick_id"] = str(tick_id)

        try:
            with self._live_write_token(executor):
                _apply()
            return True
        except Exception as err:
            log.warning(
                "QUOTE_OBSERVATION_APPLY_FAILED | position_id=%s | registry=%s | error=%s",
                pid,
                registry_name,
                err,
            )
            return False

    def apply_exit_latch(self, executor: Any, position_id: str, latch: Any) -> bool:
        """Persist exit latch state onto the authoritative position row."""
        pid = str(position_id or "").strip()
        if executor is None or not pid or latch is None:
            return False
        pos, _registry = self._resolve_registry_position(executor, pid)
        if pos is None:
            return False
        try:
            from modules.exit_latch import persist_exit_latch

            with self._live_write_token(executor):
                persist_exit_latch(pos, latch)
            return True
        except Exception as err:
            log.warning("EXIT_LATCH_APPLY_FAILED | position_id=%s | error=%s", pid, err)
            return False

    def apply_ownership_gate(
        self,
        executor: Any,
        position_id: str,
        *,
        gated: bool,
        last_confirmed_quantity: int = 0,
        last_successful_sync_age_sec: Optional[float] = None,
    ) -> bool:
        pid = str(position_id or "").strip()
        if executor is None or not pid:
            return False
        pos, _ = self._resolve_registry_position(executor, pid)
        if pos is None:
            return False
        try:
            from modules.exit_latch import (
                clear_ownership_gate,
                mark_dispatch_ownership_gated,
            )

            with self._live_write_token(executor):
                if gated:
                    mark_dispatch_ownership_gated(
                        pos,
                        last_confirmed_quantity=last_confirmed_quantity,
                        last_successful_sync_age_sec=last_successful_sync_age_sec,
                    )
                else:
                    clear_ownership_gate(pos)
            return True
        except Exception as err:
            log.warning("OWNERSHIP_GATE_APPLY_FAILED | position_id=%s | error=%s", pid, err)
            return False

    def record_close_dispatch(self, executor: Any, position_id: str) -> bool:
        pid = str(position_id or "").strip()
        if executor is None or not pid:
            return False
        pos, _ = self._resolve_registry_position(executor, pid)
        if pos is None:
            return False
        try:
            from modules.exit_latch import mark_dispatch_started

            with self._live_write_token(executor):
                mark_dispatch_started(pos)
            return True
        except Exception as err:
            log.warning("CLOSE_DISPATCH_RECORD_FAILED | position_id=%s | error=%s", pid, err)
            return False

    def record_broker_submit_attempt(
        self,
        executor: Any,
        position_id: str,
        *,
        broker_order_id: str = "",
    ) -> bool:
        pid = str(position_id or "").strip()
        if executor is None or not pid:
            return False
        pos, _ = self._resolve_registry_position(executor, pid)
        if pos is None:
            return False
        try:
            from modules.exit_latch import mark_close_order_submitted

            with self._live_write_token(executor):
                mark_close_order_submitted(pos, broker_order_id=broker_order_id)
            return True
        except Exception as err:
            log.warning("BROKER_SUBMIT_RECORD_FAILED | position_id=%s | error=%s", pid, err)
            return False

    async def synchronize(self) -> SynchronizationResult:
        """Fetch, normalize, compare and repair broker-backed positions."""
        system = self._system
        executor = _get_options_executor(system)
        if executor is None:
            return SynchronizationResult(skipped=True, reason="no_executor")
        if getattr(executor, "demo_mode", True):
            return SynchronizationResult(skipped=True, reason="demo_mode")

        adapter = getattr(executor, "broker_adapter", None)
        if adapter is None or not getattr(adapter, "is_available", lambda: False)():
            result = SynchronizationResult(
                skipped=False,
                reason="broker_adapter_unavailable",
                pass_ok=False,
                sync_unavailable=True,
                registry_stale=True,
            )
            log.warning("BROKER_POSITION_SYNC | skipped=false | reason=broker_adapter_unavailable")
            _record_sync_failure(system, result)
            return result

        from modules.broker_live_audits import (
            _broker_position_keys,
            _local_position_registry,
            _log_audit,
            _position_contract_key,
        )

        try:
            broker_rows = await adapter.get_positions()
        except Exception as err:
            log.warning("BROKER_POSITION_SYNC | get_positions_failed | error=%s", err)
            result = SynchronizationResult(
                pass_ok=False,
                reason=f"get_positions_failed:{err}",
                sync_unavailable=True,
                registry_stale=True,
            )
            _record_sync_failure(system, result)
            return result

        broker_keys = _broker_position_keys(broker_rows or [])
        local_registry = _local_position_registry(executor)
        local_by_contract = _index_local_by_contract(local_registry, _position_contract_key)

        repairs: List[RepairAction] = []
        remaining: List[str] = []
        removed: List[str] = []

        # --- qty / avg repairs + broker-flat / local-open ---
        for contract_key, local in list(local_by_contract.items()):
            pid = str(local["position_id"])
            pos = local_registry.get(pid)
            broker_row = broker_keys.get(contract_key) or broker_keys.get(
                str(local.get("contract") or "").upper()
            )
            if broker_row is None:
                action = self._repair_local_only(executor, system, pos, pid)
                if action:
                    repairs.append(action)
                continue

            bqty = abs(float(broker_row.get("quantity") or 0.0))
            lqty = abs(float(local.get("quantity") or 0.0))
            if bqty <= 0:
                action = self._repair_broker_flat_local_open(executor, system, pos, pid)
                if action:
                    repairs.append(action)
                    removed.append(pid)
                continue

            if abs(bqty - lqty) > 1e-9 and pos is not None:
                try:
                    with self._live_write_token(executor):
                        pos.quantity = int(bqty)
                        md = getattr(pos, "metadata", None)
                        if not isinstance(md, dict):
                            md = {}
                            pos.metadata = md
                        md["broker_quantity"] = int(bqty)
                        md["reconciliation_status"] = ReconciliationStatus.REPAIRED.value
                        md["last_sync_repair"] = MismatchKind.QTY_MISMATCH.value
                        md.pop("pending_broker_visibility", None)
                    repairs.append(
                        RepairAction(
                            kind=MismatchKind.QTY_MISMATCH.value,
                            position_id=pid,
                            detail=f"local={lqty}->broker={bqty}",
                            contract=contract_key,
                        )
                    )
                    log.warning(
                        "BROKER_POSITION_SYNC | repair=qty_mismatch | trade_id=%s | local=%.0f | broker=%.0f | policy=%s",
                        pid,
                        lqty,
                        bqty,
                        REPAIR_POLICY[MismatchKind.QTY_MISMATCH],
                    )
                except Exception as qty_err:
                    remaining.append(f"qty_mismatch:{pid}:{qty_err}")

            if pos is not None:
                raw = broker_row.get("raw") if isinstance(broker_row.get("raw"), dict) else {}
                broker_avg = _broker_avg_fill(raw)
                if broker_avg > 0:
                    local_avg = float(getattr(pos, "entry_price", 0.0) or 0.0)
                    if local_avg > 0 and abs(local_avg - broker_avg) / max(local_avg, 1e-9) > 0.02:
                        with self._live_write_token(executor):
                            pos.entry_price = float(broker_avg)
                            md = getattr(pos, "metadata", None)
                            if not isinstance(md, dict):
                                md = {}
                                pos.metadata = md
                            md["last_sync_repair"] = MismatchKind.AVG_FILL_MISMATCH.value
                        repairs.append(
                            RepairAction(
                                kind=MismatchKind.AVG_FILL_MISMATCH.value,
                                position_id=pid,
                                detail=f"local_avg={local_avg}->broker={broker_avg}",
                                contract=contract_key,
                            )
                        )
                # Clear visibility pending once broker confirms the line.
                md = getattr(pos, "metadata", None)
                if isinstance(md, dict) and md.get("pending_broker_visibility"):
                    md.pop("pending_broker_visibility", None)
                    md["reconciliation_status"] = ReconciliationStatus.IN_SYNC.value

        # --- broker-only imports ---
        seen_local = {str(v.get("contract") or "").upper() for v in local_by_contract.values()}
        for bkey, brow in broker_keys.items():
            bqty = abs(float(brow.get("quantity") or 0.0))
            if bqty <= 0:
                continue
            sym = str(brow.get("symbol") or bkey)
            if not (" " in sym or re.search(r"[CP]\d{8}$", sym.replace(" ", ""))):
                continue
            occ = sym.upper()
            if occ in seen_local or sym.upper() in seen_local:
                continue
            if any(occ == c or sym.upper() == c for c in seen_local):
                continue
            action = self._repair_broker_only(executor, system, brow, occ, int(bqty))
            if action:
                repairs.append(action)
                seen_local.add(occ)

        # Residual detect-only audit (preserves desk tokens)
        try:
            from modules.broker_live_audits import audit_live_orphan_positions

            residual = await audit_live_orphan_positions(
                broker_adapter=adapter,
                options_executor=executor,
                lifecycle=getattr(system, "_orb_0dte_lifecycle", None) if system else None,
            )
            if not residual.get("pass", True) and not residual.get("skipped"):
                for k in ("broker_only", "local_only"):
                    for item in residual.get(k) or []:
                        remaining.append(f"{k}:{item}")
                for qm in residual.get("qty_mismatch") or []:
                    remaining.append(f"qty_mismatch:{qm}")
        except Exception as audit_err:
            log.warning("BROKER_POSITION_SYNC | residual_audit_failed | error=%s", audit_err)

        result = SynchronizationResult(
            skipped=False,
            broker_count=len(broker_rows or []),
            local_count=len(getattr(executor, "positions", {}) or {}),
            repairs=repairs,
            mismatches_remaining=remaining,
            pass_ok=len(remaining) == 0,
            removed_position_ids=removed,
        )
        _record_sync_success(system, result)
        _log_audit("BROKER_POSITION_SYNC", result.to_audit_dict())
        if repairs:
            log.warning(
                "BROKER_POSITION_SYNC | repairs=%d | remaining=%d | pass=%s",
                len(repairs),
                len(remaining),
                str(result.pass_ok).lower(),
            )
        return result

    async def register_confirmed_fill(self, open_fact: Any) -> Any:
        """Create registry position from ConfirmedOpenFact, then sync broker truth.

        Executor must not write executor.positions in Live — call this instead.
        """
        fact = _coerce_open_fact(open_fact)
        executor = _get_options_executor(self._system)
        if executor is None:
            log.error("BROKER_POSITION_SYNC | register_confirmed_fill | no_executor")
            return None

        log.info(
            "BROKER_POSITION_SYNC | stage=register_confirmed_fill | trade_id=%s | occ=%s | qty=%d | order=%s",
            fact.trade_id,
            fact.contract_symbol,
            fact.filled_quantity,
            fact.broker_order_id,
        )

        from easy0DTE.modules.options_types import OptionsPosition

        entry_time = fact.confirmed_at if isinstance(fact.confirmed_at, datetime) else datetime.now()
        if isinstance(entry_time, datetime) and entry_time.tzinfo is not None:
            entry_time = entry_time.replace(tzinfo=None)
        meta: Dict[str, Any] = dict(fact.metadata_seed or {})
        meta.update(
            {
                "broker_order_id": fact.broker_order_id,
                "broker_execution_id": fact.broker_execution_id,
                "broker_fill_confirmed": True,
                "fill_path": "live_broker_confirmed",
                "entry_source": EntrySource.LIVE_BROKER_FILL.value,
                "occ_symbol": fact.contract_symbol,
                "management_policy": ManagementPolicy.STRATEGY_OWNED.value,
                "reconciliation_status": ReconciliationStatus.UNKNOWN.value,
                "pending_broker_visibility": True,
                "registered_via": "BrokerPositionSynchronizer.register_confirmed_fill",
                "signal_id": fact.signal_id,
                "submitted_at": (
                    fact.submitted_at.isoformat()
                    if isinstance(fact.submitted_at, datetime)
                    else str(fact.submitted_at)
                ),
                "confirmed_at": (
                    fact.confirmed_at.isoformat()
                    if isinstance(fact.confirmed_at, datetime)
                    else str(fact.confirmed_at)
                ),
            }
        )
        # Preserve chain-proven quote wire from seed — but never reintroduce a
        # C/P-mismatched lotto osiKey (Call wire on a Put fill, etc.).
        quote_req = str(
            meta.get("broker_quote_request_symbol")
            or meta.get("osi_key")
            or meta.get("osi_symbol")
            or ""
        ).strip()
        if not quote_req:
            lc = fact.lotto_contract
            quote_req = str(getattr(lc, "osi_key", "") or "").strip() if lc is not None else ""
        side_hint = ""
        try:
            lc = fact.lotto_contract
            side_hint = str(
                getattr(lc, "option_type", None)
                or getattr(lc, "option_side", None)
                or (lc.get("option_type") if isinstance(lc, dict) else None)
                or (lc.get("option_side") if isinstance(lc, dict) else None)
                or ""
            ).lower()
        except Exception:
            side_hint = ""
        if side_hint.startswith("p"):
            side_hint = "put"
        elif side_hint.startswith("c"):
            side_hint = "call"
        if not side_hint:
            from modules.active_option_contract_registry import _side_from_trade_id

            side_hint = _side_from_trade_id(str(fact.trade_id or ""))
        if quote_req and side_hint in ("call", "put"):
            try:
                from easy0DTE.modules.options_execution_normalize import (
                    resolve_0dte_quote_wire_for_side,
                )

                quote_req = resolve_0dte_quote_wire_for_side(
                    trade_id=str(fact.trade_id or ""),
                    option_side=side_hint,
                    osi_key=quote_req,
                )
            except Exception:
                pass
        if quote_req:
            # Keep OSI on osi_key for product payloads; colon form for Market Quote.
            quote_wire = quote_req
            try:
                from modules.option_contract_identity import (
                    identity_from_any_option_symbol,
                    identity_to_etrade_quote_symbol,
                )

                ident = identity_from_any_option_symbol(quote_req)
                if ident is not None and ":" not in quote_req:
                    quote_wire = identity_to_etrade_quote_symbol(ident)
            except Exception:
                quote_wire = quote_req
            meta["broker_quote_request_symbol"] = quote_wire
            meta["osi_key"] = quote_req
            meta["osi_symbol"] = quote_req
        else:
            meta.pop("broker_quote_request_symbol", None)
            meta.pop("osi_key", None)
            meta.pop("osi_symbol", None)
            meta["exit_grade_enrollment_eligible"] = False
            meta["exit_grade_enrollment_blocker"] = "missing_frozen_quote_wire"
        strategy = str(fact.strategy or "").strip() or "ORB_0DTE"
        position = OptionsPosition(
            position_id=str(fact.trade_id),
            symbol=str(fact.underlying or "").strip().upper(),
            position_type="lotto",
            lotto_contract=fact.lotto_contract,
            entry_price=float(fact.fill_price),
            entry_time=entry_time,
            quantity=int(fact.filled_quantity),
            current_value=float(fact.fill_price),
            unrealized_pnl=0.0,
            strategy_type=strategy,
            metadata=meta,
        )
        self.put_position(executor, position)
        if self._system is not None:
            try:
                from modules.open_position_recovery import rebuild_system_position_registry

                rebuild_system_position_registry(self._system)
            except Exception:
                pass

        # Fill ownership is already local after put_position. A sync flake must not
        # discard the registry row — next monitor pre-sync reconciles broker truth.
        try:
            await self.synchronize()
        except Exception as sync_err:
            log.warning(
                "BROKER_POSITION_SYNC | stage=register_confirmed_fill_sync_soft_fail | "
                "trade_id=%s | error=%s | registry_kept=true",
                fact.trade_id,
                sync_err,
            )
        return (getattr(executor, "positions", {}) or {}).get(str(fact.trade_id)) or position

    async def reconcile_confirmed_close(self, close_fact: Any) -> SynchronizationResult:
        """Mark pending close from fill fact; remove only after broker confirms flat."""
        fact = _coerce_close_fact(close_fact)
        executor = _get_options_executor(self._system)
        if executor is None:
            return SynchronizationResult(skipped=True, reason="no_executor", pass_ok=False)

        log.info(
            "BROKER_POSITION_SYNC | stage=reconcile_confirmed_close | trade_id=%s | occ=%s | order=%s",
            fact.position_id,
            fact.contract_symbol,
            fact.broker_order_id,
        )

        pos = (getattr(executor, "positions", {}) or {}).get(str(fact.position_id))
        if pos is not None:
            with self._live_write_token(executor):
                md = getattr(pos, "metadata", None)
                if not isinstance(md, dict):
                    md = {}
                    pos.metadata = md
                md["pending_close"] = {
                    "broker_order_id": fact.broker_order_id,
                    "broker_execution_id": fact.broker_execution_id,
                    "filled_quantity": int(fact.filled_quantity),
                    "fill_price": float(fact.fill_price),
                    "exit_reason": fact.exit_reason,
                    "confirmed_at": (
                        fact.confirmed_at.isoformat()
                        if isinstance(fact.confirmed_at, datetime)
                        else str(fact.confirmed_at)
                    ),
                    "realized_pnl_dollars": fact.realized_pnl_dollars,
                    "realized_pnl_pct": fact.realized_pnl_pct,
                }
                md["close_fill_confirmed"] = True
                md["close_lifecycle"] = {
                    "awaiting_broker_flat": True,
                    "close_reason": fact.exit_reason,
                }
                # Fill alone does not authorize deletion — remain open until sync.
                if str(getattr(pos, "status", "") or "") != "quarantined":
                    pos.status = "open"
                if fact.realized_pnl_dollars is not None:
                    pos.realized_pnl = float(fact.realized_pnl_dollars)
                pos.current_value = float(fact.fill_price)

        result = await self.synchronize()

        still_open = str(fact.position_id) in (getattr(executor, "positions", {}) or {})
        if still_open:
            log.error(
                "BROKER_POSITION_SYNC | close_fill_broker_not_flat | trade_id=%s | "
                "local remains open until broker reports qty=0 | policy=broker_flat_required",
                fact.position_id,
            )
            result.pass_ok = False
            result.mismatches_remaining.append(f"close_pending_broker_flat:{fact.position_id}")
        else:
            result.removed_position_ids.append(str(fact.position_id))
            log.info(
                "BROKER_POSITION_SYNC | close_finalized_broker_flat | trade_id=%s",
                fact.position_id,
            )
        return result

    async def register_local_position_from_executor(self, position: Any) -> Any:
        """Transitional sole-writer put for legacy Live spread builders, then sync."""
        executor = _get_options_executor(self._system)
        if executor is None or position is None:
            return position
        self.put_position(executor, position)
        if self._system is not None:
            try:
                from modules.open_position_recovery import rebuild_system_position_registry

                rebuild_system_position_registry(self._system)
            except Exception:
                pass
        await self.synchronize()
        return (getattr(executor, "positions", {}) or {}).get(
            str(getattr(position, "position_id", ""))
        ) or position

    async def recover_on_startup(self) -> SynchronizationResult:
        """Rebuild / repair registry from broker truth, then re-register stealth."""
        result = await self.synchronize()
        try:
            from modules.open_position_recovery import resync_open_0dte_positions_on_startup

            if self._system is not None:
                summary = await resync_open_0dte_positions_on_startup(self._system)
                log.info(
                    "BROKER_POSITION_SYNC | stage=recover_on_startup | stealth_summary=%s",
                    summary,
                )
        except Exception as err:
            log.warning("BROKER_POSITION_SYNC | recover_stealth_failed | error=%s", err)
        return result

    # --- repair helpers ---

    def _repair_local_only(
        self, executor: Any, system: Any, pos: Any, pid: str
    ) -> Optional[RepairAction]:
        if pos is None:
            return None
        md = getattr(pos, "metadata", None)
        if not isinstance(md, dict):
            md = {}
            try:
                with self._live_write_token(executor):
                    pos.metadata = md
            except Exception:
                return None

        # Close fill awaiting broker flat: missing broker line == flat → remove.
        if md.get("pending_close") or md.get("close_fill_confirmed"):
            return self._repair_broker_flat_local_open(executor, system, pos, pid)

        # Grace window after confirmed open — broker visibility lag must not quarantine.
        if md.get("pending_broker_visibility"):
            registered = _parse_iso_ts(
                md.get("confirmed_at") or md.get("submitted_at")
            )
            age = (time.time() - registered) if registered else None
            if age is not None and age < PENDING_BROKER_VISIBILITY_GRACE_SEC:
                md["reconciliation_status"] = ReconciliationStatus.UNKNOWN.value
                md["registry_visibility_pending"] = True
                log.warning(
                    "BROKER_POSITION_SYNC | local_only_within_grace | trade_id=%s | age_sec=%.1f | action=hold",
                    pid,
                    age,
                )
                return RepairAction(
                    kind="pending_broker_visibility",
                    position_id=pid,
                    detail=f"grace_hold age={age:.1f}s",
                )

        with self._live_write_token(executor):
            md["management_policy"] = ManagementPolicy.QUARANTINED.value
            md["reconciliation_status"] = ReconciliationStatus.QUARANTINED.value
            md["last_sync_repair"] = MismatchKind.LOCAL_ONLY.value
            md["quarantine_reason"] = "local_only_not_on_broker"
            try:
                pos.status = "quarantined"
            except Exception:
                pass
        log.error(
            "BROKER_POSITION_SYNC | repair=local_only | trade_id=%s | policy=%s | action=quarantine",
            pid,
            REPAIR_POLICY[MismatchKind.LOCAL_ONLY],
        )
        return RepairAction(kind=MismatchKind.LOCAL_ONLY.value, position_id=pid, detail="quarantined")

    def _repair_broker_flat_local_open(
        self, executor: Any, system: Any, pos: Any, pid: str
    ) -> Optional[RepairAction]:
        if pos is None:
            return None
        md = getattr(pos, "metadata", None)
        if not isinstance(md, dict):
            md = {}
            try:
                with self._live_write_token(executor):
                    pos.metadata = md
            except Exception:
                pass
        with self._live_write_token(executor):
            md["reconciliation_status"] = ReconciliationStatus.EXTERNALLY_CLOSED.value
            md["last_sync_repair"] = MismatchKind.BROKER_FLAT_LOCAL_OPEN.value
            md["close_lifecycle"] = {
                "close_reason": (
                    (md.get("pending_close") or {}).get("exit_reason")
                    if isinstance(md.get("pending_close"), dict)
                    else "externally_closed_or_manual"
                ),
                "broker_flat_confirmed": True,
            }
            try:
                pos.status = "closed"
                pos.quantity = 0
            except Exception:
                pass
        self.remove_position(executor, pid)
        if system is not None:
            try:
                from modules.open_position_recovery import rebuild_system_position_registry

                rebuild_system_position_registry(system)
            except Exception:
                pass
        log.error(
            "BROKER_POSITION_SYNC | repair=broker_flat_local_open | trade_id=%s | policy=%s",
            pid,
            REPAIR_POLICY[MismatchKind.BROKER_FLAT_LOCAL_OPEN],
        )
        return RepairAction(
            kind=MismatchKind.BROKER_FLAT_LOCAL_OPEN.value,
            position_id=pid,
            detail="removed_local_externally_closed",
        )

    def _repair_broker_only(
        self,
        executor: Any,
        system: Any,
        brow: Dict[str, Any],
        occ: str,
        qty: int,
    ) -> Optional[RepairAction]:
        from modules.open_position_recovery import build_rehydrated_live_position

        position = build_rehydrated_live_position(
            system=system,
            executor=executor,
            occ_symbol=occ,
            broker_qty=qty,
            broker_row=brow,
        )
        if position is None:
            return None
        self.put_position(executor, position)
        if system is not None:
            try:
                from modules.open_position_recovery import rebuild_system_position_registry

                rebuild_system_position_registry(system)
            except Exception:
                pass
        log.error(
            "BROKER_POSITION_SYNC | repair=broker_only | trade_id=%s | occ=%s | qty=%d | "
            "strategy=%s | management_policy=%s | policy=%s",
            getattr(position, "position_id", ""),
            occ,
            qty,
            getattr(position, "strategy_type", STRATEGY_RECOVERED_UNKNOWN),
            (getattr(position, "metadata", None) or {}).get(
                "management_policy", ManagementPolicy.EMERGENCY_SAFE.value
            ),
            REPAIR_POLICY[MismatchKind.BROKER_ONLY],
        )
        return RepairAction(
            kind=MismatchKind.BROKER_ONLY.value,
            position_id=str(getattr(position, "position_id", "")),
            detail=f"imported occ={occ}",
            contract=occ,
        )


def _get_options_executor(system: Any) -> Any:
    if system is None:
        return None
    dte0 = getattr(system, "dte0_manager", None)
    if dte0 and getattr(dte0, "options_executor", None):
        return dte0.options_executor
    return None


def _index_local_by_contract(local_registry: Dict[str, Any], key_fn: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for pid, pos in (local_registry or {}).items():
        sym, contract = key_fn(pos)
        if not contract:
            continue
        out[str(contract).upper()] = {
            "position_id": pid,
            "symbol": sym,
            "contract": contract,
            "quantity": int(getattr(pos, "quantity", 0) or 0),
        }
    return out


def _broker_avg_fill(raw: Dict[str, Any]) -> float:
    for field_name in ("costPerShare", "averagePrice", "pricePaid", "Price"):
        try:
            v = float(raw.get(field_name) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    return 0.0


def _parse_iso_ts(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _coerce_open_fact(open_fact: Any) -> ConfirmedOpenFact:
    if isinstance(open_fact, ConfirmedOpenFact):
        return open_fact
    if isinstance(open_fact, BrokerFill):
        parsed = parse_occ_symbol(open_fact.occ_contract_symbol)
        now = datetime.now(timezone.utc)
        return ConfirmedOpenFact(
            broker_order_id=str(open_fact.broker_order_id or ""),
            broker_execution_id=None,
            contract_symbol=str(open_fact.occ_contract_symbol or ""),
            underlying=str(open_fact.underlying or parsed.get("underlying") or ""),
            expiration=str(parsed.get("expiration") or ""),
            strike=float(parsed.get("strike") or 0.0),
            call_put=str(parsed.get("call_put") or ""),
            filled_quantity=int(open_fact.quantity or 0),
            fill_price=float(open_fact.average_fill or 0.0),
            strategy=str((open_fact.raw or {}).get("strategy") or "ORB_0DTE"),
            signal_id=None,
            trade_id=str(open_fact.trade_id),
            submitted_at=now,
            confirmed_at=now,
            metadata_seed=dict(open_fact.raw or {}),
        )
    raise TypeError(f"Expected ConfirmedOpenFact or BrokerFill, got {type(open_fact)!r}")


def _coerce_close_fact(close_fact: Any) -> ConfirmedCloseFact:
    if isinstance(close_fact, ConfirmedCloseFact):
        return close_fact
    if isinstance(close_fact, BrokerFill):
        now = datetime.now(timezone.utc)
        return ConfirmedCloseFact(
            position_id=str(close_fact.trade_id),
            broker_order_id=str(close_fact.broker_order_id or ""),
            broker_execution_id=None,
            contract_symbol=str(close_fact.occ_contract_symbol or ""),
            filled_quantity=int(close_fact.quantity or 0),
            fill_price=float(close_fact.average_fill or 0.0),
            exit_reason=str((close_fact.raw or {}).get("exit_reason") or "broker_close"),
            confirmed_at=now,
        )
    raise TypeError(f"Expected ConfirmedCloseFact or BrokerFill, got {type(close_fact)!r}")


def _record_sync_failure(system: Any, result: SynchronizationResult) -> None:
    if system is None:
        return
    streak = int(getattr(system, "_broker_sync_fail_streak", 0) or 0) + 1
    system._broker_sync_fail_streak = streak
    system._broker_registry_stale = True
    system._broker_last_sync_result = result.to_audit_dict()
    if streak >= SYNC_FAIL_STREAK_CRITICAL:
        log.critical(
            "BROKER_POSITION_SYNC | critical_repeated_failures | streak=%d | reason=%s | "
            "action=degraded_lifecycle_skip_normal_exits",
            streak,
            result.reason,
        )
        try:
            system._broker_sync_degraded_lifecycle = True
        except Exception:
            pass


def _record_sync_success(system: Any, result: SynchronizationResult) -> None:
    if system is None:
        return
    system._broker_sync_fail_streak = 0
    system._broker_registry_stale = False
    system._broker_sync_degraded_lifecycle = False
    system._broker_last_sync_result = result.to_audit_dict()
    system._broker_last_sync_ok_at = time.time()


def should_skip_normal_exits_for_stale_registry(system: Any) -> bool:
    """True when ownership sync is unavailable — do not run profit-taking exits."""
    if system is None:
        return False
    if bool(getattr(system, "_broker_registry_stale", False)):
        return True
    last = getattr(system, "_broker_last_sync_result", None)
    if isinstance(last, dict) and is_sync_unavailable_reason(str(last.get("reason") or "")):
        return True
    return False


def get_or_create_synchronizer(system: Any) -> BrokerPositionSynchronizer:
    if system is None:
        return BrokerPositionSynchronizer(None)
    sync = getattr(system, "_broker_position_synchronizer", None)
    if isinstance(sync, BrokerPositionSynchronizer):
        sync.bind_system(system)
        return sync
    sync = BrokerPositionSynchronizer(system)
    try:
        system._broker_position_synchronizer = sync
    except Exception:
        pass
    return sync
