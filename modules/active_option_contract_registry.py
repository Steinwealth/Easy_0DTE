"""Active open-option-contract registry for exact-contract exit monitoring.

Tracks Demo mock + Live broker-confirmed ORB and Trendline legs until
confirmed closed. Deduplicates option symbols for batched quote requests.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

log = logging.getLogger(__name__)


@dataclass
class ActiveOptionContract:
    trade_id: str
    strategy: str  # orb_0dte | trendline_0dte
    underlying: str
    option_symbol: str  # OCC / E*TRADE option symbol
    expiration: str
    strike: float
    option_type: str  # call | put
    quantity: int
    entry_fill_price: float
    mode: str  # demo | live
    pending_close: bool = False
    leg_role: str = "long"
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _positive_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _normalize_expiry(raw: Any) -> str:
    s = str(raw or "").strip().replace("-", "")
    return s


def resolve_frozen_quote_wire_from_position(pos: Any) -> str:
    """Return frozen broker quote wire only — never reconstruct from OCC/session day.

    Eligible sources: broker_quote_request_symbol / osi_key / osi_symbol on meta,
    normalized_options, legs, or lotto_contract. Empty string means Exit-grade
    enrollment is not allowed until identity recovery stamps a wire symbol.

    Wire must match resolved option side (C/P); contaminated Call wire on a Put
    (or vice versa) is rejected here so enrollment cannot follow a bad lotto key.
    """
    meta = getattr(pos, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}

    side = ""
    try:
        _occ, _und, _strike, otype, _exp = resolve_occ_symbol_from_position(pos)
        side = str(otype or "").strip().lower()
    except Exception:
        side = ""
    if side not in ("call", "put"):
        side = _side_from_trade_id(str(getattr(pos, "position_id", "") or ""))

    def _accept(raw: str) -> str:
        val = str(raw or "").strip()
        if not val:
            return ""
        if side not in ("call", "put"):
            return val
        try:
            from easy0DTE.modules.options_execution_normalize import (
                resolve_0dte_quote_wire_for_side,
            )

            return resolve_0dte_quote_wire_for_side(
                trade_id=str(getattr(pos, "position_id", "") or ""),
                option_side=side,
                osi_key=val,
            )
        except Exception:
            return val

    for key in ("broker_quote_request_symbol", "osi_key", "osi_symbol"):
        accepted = _accept(meta.get(key))
        if accepted:
            return accepted
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    for key in ("broker_quote_request_symbol", "osi_key", "osi_symbol"):
        accepted = _accept(norm.get(key))
        if accepted:
            return accepted
    legs = norm.get("legs") if isinstance(norm.get("legs"), list) else []
    if legs and isinstance(legs[0], dict):
        for key in ("broker_quote_request_symbol", "osi_key", "osi_symbol"):
            accepted = _accept(legs[0].get(key))
            if accepted:
                return accepted
    lc = getattr(pos, "lotto_contract", None)
    if isinstance(lc, dict):
        accepted = _accept(
            lc.get("osi_key") or lc.get("osiKey") or lc.get("broker_quote_request_symbol")
        )
        if accepted:
            return accepted
    elif lc is not None:
        accepted = _accept(getattr(lc, "osi_key", "") or getattr(lc, "osiKey", ""))
        if accepted:
            return accepted
    return ""


def has_frozen_quote_wire_identity(pos: Any) -> bool:
    return bool(resolve_frozen_quote_wire_from_position(pos))


def resolve_quote_request_symbol_from_position(pos: Any) -> str:
    """Prefer frozen quote wire; OCC fallback is for display/repair only — not Exit-grade."""
    wire = resolve_frozen_quote_wire_from_position(pos)
    if wire:
        return wire
    occ, *_rest = resolve_occ_symbol_from_position(pos)
    return str(occ or "").strip()


def _lotto_field(lc: Any, *names: str, default: Any = None) -> Any:
    if lc is None:
        return default
    if isinstance(lc, dict):
        for n in names:
            if lc.get(n) is not None and lc.get(n) != "":
                return lc.get(n)
        return default
    for n in names:
        if hasattr(lc, n):
            v = getattr(lc, n, None)
            if v is not None and v != "":
                return v
    return default


def _side_from_trade_id(trade_id: str) -> str:
    tid = str(trade_id or "").strip().lower()
    if not tid:
        return ""
    # D-AMD-472.5p-571 / D-COIN-197.5c-918
    m = re.search(r"(\d+(?:\.\d+)?)([cp])(?:-|_|$)", tid)
    if m:
        return "put" if m.group(2) == "p" else "call"
    return ""


def resolve_occ_symbol_from_position(pos: Any) -> Tuple[str, str, float, str, str]:
    """Return (occ, underlying, strike, option_type, expiry_ymd).

    Prefers metadata occ_symbol / order payload; else rebuilds via format_occ_option_symbol.
    Never defaults put→call when legs/trade_id stamp put.
    """
    underlying = str(getattr(pos, "symbol", "") or "").strip().upper()
    meta = getattr(pos, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}

    occ = str(
        meta.get("occ_symbol")
        or meta.get("option_symbol")
        or meta.get("contract_symbol")
        or ""
    ).strip()

    strike = 0.0
    option_type = ""
    expiry = ""

    lc = getattr(pos, "lotto_contract", None)
    if lc is not None:
        try:
            strike = float(_lotto_field(lc, "strike", default=0.0) or 0.0)
        except (TypeError, ValueError):
            strike = 0.0
        option_type = str(_lotto_field(lc, "option_type", "option_side", "type", default="") or "").lower()
        expiry = _normalize_expiry(_lotto_field(lc, "expiry", "expiration", default=None))
        if not underlying:
            underlying = str(_lotto_field(lc, "symbol", "underlying", default="") or "").strip().upper()

    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    legs = norm.get("legs") if isinstance(norm.get("legs"), list) else []
    if legs and isinstance(legs[0], dict):
        leg0 = legs[0]
        occ = occ or str(leg0.get("occ_symbol") or leg0.get("option_symbol") or "").strip()
        if strike <= 0:
            strike = float(leg0.get("strike") or 0.0)
        # Trendline/ORB legs use option_side; never leave a default "call" when put is stamped.
        leg_side = str(
            leg0.get("option_side") or leg0.get("option_type") or leg0.get("side") or ""
        ).lower()
        if leg_side in ("call", "put"):
            option_type = leg_side
        if not expiry:
            expiry = _normalize_expiry(leg0.get("expiration") or leg0.get("expiry") or norm.get("expiration_ymd"))

    if not expiry:
        expiry = _normalize_expiry(norm.get("expiration_ymd") or meta.get("expiration") or meta.get("expiry"))

    osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
    if strike <= 0:
        strike = float(osnap.get("strike") or meta.get("strike") or 0.0)
    snap_side = str(osnap.get("option_side") or "").lower()
    if snap_side in ("call", "put"):
        option_type = snap_side

    if option_type not in ("call", "put"):
        option_type = _side_from_trade_id(str(getattr(pos, "position_id", "") or ""))
    if option_type not in ("call", "put"):
        # Last resort only when nothing else is known — log and keep empty for rebuild skip.
        option_type = ""

    # If stamped OCC call/put letter disagrees with resolved side, rebuild OCC.
    if occ and underlying and strike > 0 and expiry and option_type in ("call", "put"):
        compact = occ.replace(" ", "").upper()
        want = "C" if option_type == "call" else "P"
        m = re.search(r"(\d{6})([CP])(\d{8})$", compact)
        if m and m.group(2) != want:
            occ = ""

    if not occ and underlying and strike > 0 and expiry and option_type in ("call", "put"):
        try:
            from easy0DTE.modules.etrade_options_api import format_occ_option_symbol

            occ = format_occ_option_symbol(underlying, expiry, option_type, strike)
        except Exception:
            try:
                from modules.broker_live_audits import _build_occ_symbol

                occ = _build_occ_symbol(underlying, expiry, option_type, strike)
            except Exception:
                occ = ""

    return occ, underlying, float(strike or 0.0), str(option_type or ""), expiry


def strategy_from_position(pos: Any) -> str:
    meta = getattr(pos, "metadata", None)
    if isinstance(meta, dict):
        st = str(meta.get("strategy_type") or meta.get("strategy") or "").lower()
        if st in ("recovered_unknown", "unknown", "emergency_safe"):
            return "recovered_unknown"
        if "trendline" in st or st.startswith("tl"):
            return "trendline_0dte"
        if "orb" in st:
            return "orb_0dte"
        osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
        st2 = str(osnap.get("strategy_type") or "").lower()
        if "trendline" in st2:
            return "trendline_0dte"
        if "orb" in st2:
            return "orb_0dte"
        if st2 in ("recovered_unknown", "unknown"):
            return "recovered_unknown"
    pid = str(getattr(pos, "position_id", "") or "")
    if pid.upper().startswith("TL_"):
        return "trendline_0dte"
    # Unknown identity must not be coerced into ORB.
    return "recovered_unknown"


class ActiveOptionContractRegistry:
    """In-memory registry keyed by trade_id; quote dedupe by option_symbol."""

    def __init__(self) -> None:
        self._by_trade: Dict[str, ActiveOptionContract] = {}

    def clear(self) -> None:
        self._by_trade.clear()

    def upsert(self, contract: ActiveOptionContract) -> None:
        tid = str(contract.trade_id or "").strip()
        if not tid or not contract.option_symbol:
            return
        self._by_trade[tid] = contract

    def remove(self, trade_id: str) -> None:
        self._by_trade.pop(str(trade_id or "").strip(), None)

    def mark_pending_close(self, trade_id: str, pending: bool = True) -> None:
        c = self._by_trade.get(str(trade_id or "").strip())
        if c is not None:
            c.pending_close = bool(pending)

    def get(self, trade_id: str) -> Optional[ActiveOptionContract]:
        return self._by_trade.get(str(trade_id or "").strip())

    def all_contracts(self) -> List[ActiveOptionContract]:
        return list(self._by_trade.values())

    def unique_option_symbols(self) -> List[str]:
        """Exact-quote batch symbols: frozen wire only (fail-closed for Exit-grade).

        Positions without broker_quote_request_symbol / osiKey are omitted here so
        we never silently reconstruct session-date OCC into the exact quote path.
        Degraded chain monitoring continues separately for those trades.
        """
        seen: Set[str] = set()
        out: List[str] = []
        for c in self._by_trade.values():
            extras = c.extras if isinstance(c.extras, dict) else {}
            if extras.get("exit_grade_enrollment_eligible") is False:
                continue
            sym = str(
                extras.get("broker_quote_request_symbol")
                or extras.get("osi_key")
                or ""
            ).strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
        return out

    def sync_from_positions(
        self,
        positions: Iterable[Any],
        *,
        mode: str,
        confirmed_closed_ids: Optional[Set[str]] = None,
    ) -> List[ActiveOptionContract]:
        """Rebuild active set from open positions; drop confirmed-closed."""
        closed = confirmed_closed_ids or set()
        keep: Set[str] = set()
        for pos in positions or []:
            tid = str(getattr(pos, "position_id", "") or "").strip()
            if not tid or tid in closed:
                continue
            occ, und, strike, otype, expiry = resolve_occ_symbol_from_position(pos)
            if not occ:
                log.warning(
                    "ACTIVE_OPTION_CONTRACT | missing_occ | trade_id=%s | underlying=%s",
                    tid,
                    und,
                )
                continue
            entry = _positive_float(getattr(pos, "entry_price", None)) or 0.0
            qty = int(getattr(pos, "quantity", 1) or 1)
            meta = getattr(pos, "metadata", None) if isinstance(getattr(pos, "metadata", None), dict) else {}
            pending = bool(meta.get("pending_close")) or bool(
                (meta.get("close_lifecycle") or {}).get("awaiting_broker_flat")
                if isinstance(meta.get("close_lifecycle"), dict)
                else False
            )
            quote_req = resolve_frozen_quote_wire_from_position(pos)
            eligible = bool(quote_req)
            if isinstance(meta, dict):
                meta["exit_grade_enrollment_eligible"] = eligible
                if not eligible:
                    meta["exit_grade_enrollment_blocker"] = "missing_frozen_quote_wire"
                    log.warning(
                        "EXIT_GRADE_ENROLLMENT | trade_id=%s | eligible=false | "
                        "reason=missing_frozen_quote_wire | degraded_monitoring=true",
                        tid,
                    )
                else:
                    meta.pop("exit_grade_enrollment_blocker", None)
            norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
            expiry_extras: Dict[str, Any] = {}
            for _ek in (
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
                val = meta.get(_ek)
                if val is None:
                    val = norm.get(_ek)
                if val is not None:
                    expiry_extras[_ek] = val
            self.upsert(
                ActiveOptionContract(
                    trade_id=tid,
                    strategy=strategy_from_position(pos),
                    underlying=und,
                    option_symbol=occ,
                    expiration=expiry,
                    strike=strike,
                    option_type=otype or "call",
                    quantity=max(1, qty),
                    entry_fill_price=float(entry),
                    mode=str(mode or "demo"),
                    pending_close=pending,
                    extras={
                        "broker_quote_request_symbol": quote_req,
                        "osi_key": str(meta.get("osi_key") or quote_req or "").strip(),
                        "exit_grade_enrollment_eligible": eligible,
                        **expiry_extras,
                    },
                )
            )
            keep.add(tid)
        for stale in list(self._by_trade.keys()):
            if stale not in keep:
                self.remove(stale)
        return self.all_contracts()


_REGISTRY: Optional[ActiveOptionContractRegistry] = None


def get_active_option_contract_registry() -> ActiveOptionContractRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ActiveOptionContractRegistry()
    return _REGISTRY
