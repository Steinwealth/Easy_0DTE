#!/usr/bin/env python3
"""
Broker adapter interface for ORB1 Breakout and Trendline 0DTE.

Canonical broker selection: BROKER=etrade | interactive_brokers | tastytrade

Live mode must use a broker adapter for all order submission and reconciliation.
Demo mode never submits broker orders (``MODE=demo``); the adapter may still supply market data.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Raised / message prefix when Live sync must mark sync_unavailable (never treat as broker-flat).
BROKER_POSITIONS_UNAVAILABLE = "broker_positions_unavailable"


class BrokerKind(str, Enum):
    ETRADE = "etrade"
    INTERACTIVE_BROKERS = "interactive_brokers"
    TASTYTRADE = "tastytrade"
    CHARLES_SCHWAB = "schwab"

    @classmethod
    def from_env(cls, raw: Optional[str] = None) -> "BrokerKind":
        token = (raw or "").strip().lower()
        aliases = {
            "etrade": cls.ETRADE,
            "e*trade": cls.ETRADE,
            "ib": cls.INTERACTIVE_BROKERS,
            "ibkr": cls.INTERACTIVE_BROKERS,
            "interactive_brokers": cls.INTERACTIVE_BROKERS,
            "tastytrade": cls.TASTYTRADE,
            "tasty": cls.TASTYTRADE,
            "schwab": cls.CHARLES_SCHWAB,
            "charles_schwab": cls.CHARLES_SCHWAB,
            "charlesschwab": cls.CHARLES_SCHWAB,
        }
        if token in aliases:
            return aliases[token]
        return cls.ETRADE


@dataclass
class BrokerAccountBalance:
    cash_available: float
    account_value: float
    buying_power: float
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OptionOrderRequest:
    symbol: str
    expiry: str  # YYYYMMDD or YYYY-MM-DD
    option_type: str  # call | put
    strike: float
    quantity: int
    side: str  # BUY | SELL
    order_type: str = "LIMIT"
    limit_price: Optional[float] = None
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    strategy: str = "ORB_0DTE"
    trade_id: str = ""
    execution_context: Optional[Dict[str, Any]] = None


@dataclass
class BrokerOrderResult:
    success: bool
    order_id: Optional[str] = None
    fill_confirmed: bool = False
    filled_quantity: float = 0.0
    average_fill_price: Optional[float] = None
    status: str = "UNKNOWN"
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerCloseRequest:
    position_id: str
    symbol: str
    quantity: int
    exit_price: Optional[float] = None
    order_type: str = "MARKET"
    strategy: str = "ORB_0DTE"
    position: Any = None  # OptionsPosition when available


class BrokerAdapter(ABC):
    """Broker-agnostic options trading surface."""

    kind: BrokerKind

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    async def get_account_balance(self) -> Optional[BrokerAccountBalance]:
        ...

    @abstractmethod
    async def get_option_chain(
        self,
        symbol: str,
        expiration: Optional[str] = None,
        *,
        strike_count: int = 20,
        underlying_price: Optional[float] = None,
    ) -> Dict[str, List[Any]]:
        ...

    @abstractmethod
    async def submit_option_order(self, order: OptionOrderRequest) -> BrokerOrderResult:
        ...

    @abstractmethod
    async def submit_close_order(self, request: BrokerCloseRequest) -> BrokerOrderResult:
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def reconcile_order(
        self,
        order_id: str,
        *,
        requested_quantity: int,
        side: str,
        strategy: str,
        symbol: str,
        trade_id: str,
        quote_mid: Optional[float] = None,
        timeout_sec: Optional[float] = None,
    ) -> BrokerOrderResult:
        ...

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Return open positions in synchronizer row shape.

        Contract (multi-broker):
          - Success with no opens → ``[]`` (true flat; sync may repair broker-flat).
          - Fetch / auth / unimplemented failure → raise ``RuntimeError`` whose
            message starts with ``broker_positions_unavailable`` so
            ``BrokerPositionSynchronizer`` sets ``sync_unavailable`` (never silent []).
          - Each row: ``{symbol, quantity, avg_price?, raw}`` where ``symbol`` is
            spaced OCC when the broker can identify an option contract.
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...


def normalize_broker_position_row(row: Any) -> Optional[Dict[str, Any]]:
    """Normalize a broker-native position into synchronizer row shape.

    Accepts dicts, ETradePosition-like objects, or already-normalized rows.
    Returns None when the row has no usable symbol.
    """
    if row is None:
        return None

    if isinstance(row, dict):
        raw = dict(row.get("raw") or {}) if isinstance(row.get("raw"), dict) else dict(row)
        symbol = str(
            row.get("symbol")
            or row.get("Symbol")
            or row.get("occ_symbol")
            or row.get("osiKey")
            or ""
        ).strip()
        qty_raw = row.get("quantity", row.get("qty", row.get("Quantity")))
        avg_raw = (
            row.get("avg_price")
            or row.get("averagePrice")
            or row.get("costPerShare")
            or row.get("pricePaid")
        )
        product = row.get("Product") if isinstance(row.get("Product"), dict) else raw.get("Product")
    else:
        raw = {
            "position_id": getattr(row, "position_id", None),
            "symbol": getattr(row, "symbol", None),
            "symbol_description": getattr(row, "symbol_description", None),
            "quantity": getattr(row, "quantity", None),
            "position_type": getattr(row, "position_type", None),
            "market_value": getattr(row, "market_value", None),
            "total_cost": getattr(row, "total_cost", None),
            "Product": getattr(row, "product", None) or getattr(row, "Product", None),
            "osiKey": getattr(row, "osi_key", None) or getattr(row, "osiKey", None),
        }
        symbol = str(getattr(row, "symbol", "") or "").strip()
        qty_raw = getattr(row, "quantity", None)
        avg_raw = getattr(row, "avg_price", None)
        product = raw.get("Product") if isinstance(raw.get("Product"), dict) else None
        # Cost-per-share from total_cost / qty when available.
        try:
            qty_tmp = abs(float(qty_raw or 0.0))
            total_cost = float(getattr(row, "total_cost", 0.0) or 0.0)
            if avg_raw is None and qty_tmp > 0 and total_cost:
                avg_raw = abs(total_cost) / qty_tmp
        except (TypeError, ValueError):
            pass

    # Prefer structured Product / osiKey → spaced OCC for Live sync matching.
    if product or raw.get("osiKey") or raw.get("osi_key"):
        try:
            from modules.option_contract_identity import (
                identity_from_product,
                occ_from_identity,
            )

            osi = str(raw.get("osiKey") or raw.get("osi_key") or "").strip()
            ident = identity_from_product(product or {}, osi_key=osi)
            if ident is not None:
                symbol = occ_from_identity(ident)
        except Exception:
            pass

    if not symbol:
        # Last resort: human description is not OCC-matchable but keeps the row visible.
        symbol = str(
            raw.get("symbol_description")
            or getattr(row, "symbol_description", "")
            or ""
        ).strip()

    if not symbol:
        return None

    try:
        quantity = float(qty_raw or 0.0)
    except (TypeError, ValueError):
        quantity = 0.0

    try:
        avg_price = float(avg_raw or 0.0)
    except (TypeError, ValueError):
        avg_price = 0.0

    out: Dict[str, Any] = {
        "symbol": symbol,
        "quantity": quantity,
        "raw": raw if isinstance(raw, dict) else {"value": raw},
    }
    if avg_price > 0:
        out["avg_price"] = avg_price
        out["averagePrice"] = avg_price
        out["costPerShare"] = avg_price
    return out


def normalize_broker_position_rows(rows: Any) -> List[Dict[str, Any]]:
    """Normalize a broker portfolio list into synchronizer rows (drops unusable)."""
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        norm = normalize_broker_position_row(row)
        if norm is not None:
            out.append(norm)
    return out


def extract_order_id(place_response: Any) -> Optional[str]:
    """Best-effort order id from E*TRADE place/preview responses."""
    if not place_response:
        return None
    if isinstance(place_response, dict):
        for key in ("orderId", "order_id", "OrderId", "id"):
            if place_response.get(key):
                return str(place_response[key])
        for wrapper in (
            "PlaceOrderResponse",
            "placeOrderResponse",
            "OrderResponse",
            "orderResponse",
            "OrderIdsResponse",
            "Order",
        ):
            block = place_response.get(wrapper)
            if isinstance(block, dict):
                oid = extract_order_id(block)
                if oid:
                    return oid
                for key in ("orderId", "orderIds", "OrderId"):
                    val = block.get(key)
                    if val:
                        if isinstance(val, list) and val:
                            return str(val[0])
                        return str(val)
        for key in ("orderId", "orderIds"):
            vals = _walk(place_response, key)
            if vals:
                return str(vals[0])
    return None


def _trendline_broker_strategy(strategy: str) -> bool:
    s = str(strategy or "").strip().lower()
    return s in ("trendline_0dte", "easy_trendline", "easytrendline_0dte")


def _transient_broker_place_failure(raw_response: Any) -> bool:
    """True when E*TRADE preview/place failed transiently and a retry may succeed."""
    if raw_response is None:
        return True
    if isinstance(raw_response, dict):
        err = str(raw_response.get("error") or raw_response.get("message") or "").lower()
        if any(tok in err for tok in ("500", "502", "503", "504", "429", "timeout", "unavailable")):
            return True
        if "previeworderresponse" not in raw_response and raw_response.get("error"):
            return True
    return False


def _walk(obj: Any, key: str) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(_walk(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk(item, key))
    return found


class StubBrokerAdapter(BrokerAdapter):
    """Fail-closed placeholder for unimplemented brokers."""

    def __init__(self, kind: BrokerKind):
        self.kind = kind

    def is_available(self) -> bool:
        return False

    async def get_account_balance(self) -> Optional[BrokerAccountBalance]:
        return None

    async def get_option_chain(self, symbol: str, expiration: Optional[str] = None, **kwargs) -> Dict[str, List[Any]]:
        return {"calls": [], "puts": []}

    async def _fail(self, op: str) -> BrokerOrderResult:
        err = f"broker_not_implemented:{self.kind.value}:{op}"
        log.error("BROKER_ADAPTER | %s", err)
        return BrokerOrderResult(success=False, error=err, status="NOT_IMPLEMENTED")

    async def submit_option_order(self, order: OptionOrderRequest) -> BrokerOrderResult:
        return await self._fail("submit_option_order")

    async def submit_close_order(self, request: BrokerCloseRequest) -> BrokerOrderResult:
        return await self._fail("submit_close_order")

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        return {"error": f"broker_not_implemented:{self.kind.value}"}

    async def reconcile_order(self, order_id: str, **kwargs) -> BrokerOrderResult:
        return await self._fail("reconcile_order")

    async def get_positions(self) -> List[Dict[str, Any]]:
        # Fail closed: never silent-empty (would look like broker flat to Live sync).
        raise RuntimeError(
            f"{BROKER_POSITIONS_UNAVAILABLE}:broker_not_implemented:{self.kind.value}:get_positions"
        )

    async def cancel_order(self, order_id: str) -> bool:
        return False


class ETradeBrokerAdapter(BrokerAdapter):
    """E*TRADE implementation wrapping ETradeOptionsAPI + PrimeETradeTrading."""

    kind = BrokerKind.ETRADE

    def __init__(
        self,
        etrade_options_api: Any,
        *,
        live_mode: bool,
        options_chain_manager: Any = None,
    ):
        self._api = etrade_options_api
        # True when broker preview+place is allowed (broker_submits_enabled), not MODE=live.
        self._live_mode = bool(live_mode)
        # Canonical 0DTE expiry policy owner (same-day → nearest). Optional but preferred.
        self._options_chain_manager = options_chain_manager

    def set_options_chain_manager(self, manager: Any) -> None:
        """Attach OptionsChainManager so get_option_chain uses same-day→nearest policy."""
        self._options_chain_manager = manager

    @property
    def etrade_trading(self) -> Any:
        return getattr(self._api, "etrade", None)

    def is_available(self) -> bool:
        return bool(self._api and getattr(self._api, "is_available", lambda: False)())

    async def get_account_balance(self) -> Optional[BrokerAccountBalance]:
        if not self.is_available():
            return None
        et = self.etrade_trading
        if et is None:
            return None
        try:
            bal_obj = et.get_account_balance()
            if not bal_obj:
                summary = et.get_account_summary()
                block = (summary or {}).get("balance") or {}
                cash = float(block.get("cash_available_for_investment") or 0.0)
                bp = float(block.get("cash_buying_power") or cash)
                av = float(block.get("account_value") or cash)
                return BrokerAccountBalance(cash_available=cash, account_value=av, buying_power=bp, raw=block)
            cash = float(getattr(bal_obj, "cash_available_for_investment", 0.0) or 0.0)
            av = float(getattr(bal_obj, "account_value", 0.0) or cash)
            bp = float(getattr(bal_obj, "cash_buying_power", 0.0) or cash)
            return BrokerAccountBalance(
                cash_available=cash,
                account_value=av,
                buying_power=bp,
                raw={
                    "source": "get_account_balance",
                    "optionLevel": getattr(bal_obj, "option_level", None),
                },
            )
        except Exception as exc:
            log.error("BROKER_ADAPTER | get_account_balance failed: %s", exc)
            return None

    async def get_option_chain(
        self,
        symbol: str,
        expiration: Optional[str] = None,
        *,
        strike_count: int = 20,
        underlying_price: Optional[float] = None,
    ) -> Dict[str, List[Any]]:
        if not self.is_available():
            return {"calls": [], "puts": []}
        from easy0DTE.modules.options_chain_manager import fetch_0dte_intent_chain

        chain = await fetch_0dte_intent_chain(
            self._options_chain_manager,
            symbol,
            expiry=expiration,
            underlying_price=underlying_price,
            use_cache=True,
        )
        if isinstance(chain, dict) and (chain.get("calls") or chain.get("puts") or chain):
            return chain
        log.warning(
            "BROKER_ADAPTER | get_option_chain | symbol=%s | reason=no_chain_via_intent_door",
            symbol,
        )
        return {"calls": [], "puts": []}

    async def submit_option_order(self, order: OptionOrderRequest) -> BrokerOrderResult:
        if not self._live_mode:
            return BrokerOrderResult(
                success=False,
                error="demo_mode_broker_submit_blocked",
                status="BLOCKED",
            )
        if not self.is_available():
            return BrokerOrderResult(success=False, error="broker_unavailable", status="UNAVAILABLE")

        expiry = order.expiry.replace("-", "")
        try:
            from modules.execution_routing import smart_execution_enabled
        except ImportError:
            smart_execution_enabled = lambda: False  # type: ignore

        use_smart = order.side.upper() == "BUY" and (
            smart_execution_enabled() or _trendline_broker_strategy(order.strategy)
        )
        raw_response: Any = None
        oid: Optional[str] = None
        max_place_attempts = 3
        for place_attempt in range(max_place_attempts):
            if use_smart:
                raw_response = await self._api.place_single_option_buy_open_smart(
                    symbol=order.symbol,
                    expiry=expiry,
                    option_type=order.option_type,
                    strike=order.strike,
                    quantity=order.quantity,
                    bid=order.bid,
                    ask=order.ask,
                    mid=order.mid,
                    strategy=order.strategy,
                    trade_id=order.trade_id,
                    execution_context=order.execution_context,
                )
            else:
                raw_response = await self._api.place_single_option_order(
                    symbol=order.symbol,
                    expiry=expiry,
                    option_type=order.option_type,
                    strike=order.strike,
                    side=order.side,
                    quantity=order.quantity,
                )

            oid = extract_order_id(raw_response)
            if not oid and raw_response:
                oid = extract_order_id(getattr(raw_response, "__dict__", raw_response))
            if oid:
                break
            if place_attempt + 1 < max_place_attempts and _transient_broker_place_failure(raw_response):
                log.warning(
                    "BROKER_ADAPTER | transient_place_retry | symbol=%s | strategy=%s | attempt=%d",
                    order.symbol,
                    order.strategy,
                    place_attempt + 1,
                )
                await asyncio.sleep(0.6 * (place_attempt + 1))
                continue
            break

        if not raw_response or not oid:
            log.error(
                "BROKER_ADAPTER | order_submit_failed | symbol=%s | strategy=%s | "
                "place_attempts=%d | raw_response=%s",
                order.symbol,
                order.strategy,
                max_place_attempts,
                str(raw_response)[:500] if raw_response is not None else "none",
            )
            return BrokerOrderResult(
                success=False,
                error="order_submit_failed",
                status="REJECTED",
                raw={"response": raw_response, "place_attempts": max_place_attempts},
            )

        try:
            from modules.config_loader import get_config_value

            entry_timeout = float(get_config_value("EXEC_ENTRY_FILL_RECONCILE_TIMEOUT_SEC", 4.0))
        except Exception:
            entry_timeout = 4.0
        reconciled = await self.reconcile_order(
            oid,
            requested_quantity=order.quantity,
            side=order.side,
            strategy=order.strategy,
            symbol=order.symbol,
            trade_id=order.trade_id,
            quote_mid=order.mid,
            timeout_sec=entry_timeout,
        )
        reconciled.raw = {"place_response": raw_response, **reconciled.raw}
        return reconciled

    async def submit_close_order(self, request: BrokerCloseRequest) -> BrokerOrderResult:
        if not self._live_mode:
            return BrokerOrderResult(
                success=False,
                error="demo_mode_broker_close_blocked",
                status="BLOCKED",
            )
        if not self.is_available():
            return BrokerOrderResult(success=False, error="broker_unavailable", status="UNAVAILABLE")
        if request.position is None:
            return BrokerOrderResult(success=False, error="close_missing_position", status="REJECTED")

        raw = await self._api.close_position(
            position=request.position,
            exit_price=request.exit_price,
            order_type=request.order_type,
        )
        oid = extract_order_id(raw)
        if not raw:
            return BrokerOrderResult(success=False, error="close_submit_failed", status="REJECTED")
        if not oid:
            return BrokerOrderResult(
                success=False,
                error="close_order_id_missing",
                status="REJECTED",
                raw={"close_response": raw},
            )

        qty = int(request.quantity or 1)
        try:
            from modules.config_loader import get_config_value

            exit_timeout = float(get_config_value("EXEC_EXIT_FILL_RECONCILE_TIMEOUT_SEC", 10.0))
        except Exception:
            exit_timeout = 10.0
        reconciled = await self.reconcile_order(
            oid,
            requested_quantity=qty,
            side="SELL",
            strategy=request.strategy,
            symbol=request.symbol,
            trade_id=request.position_id,
            quote_mid=float(request.exit_price) if request.exit_price else None,
            timeout_sec=exit_timeout,
        )
        reconciled.raw = {"close_response": raw, **reconciled.raw}
        if not reconciled.fill_confirmed:
            return BrokerOrderResult(
                success=False,
                order_id=oid,
                fill_confirmed=False,
                error=reconciled.error or "close_fill_not_confirmed",
                status=reconciled.status,
                raw=reconciled.raw,
            )
        return BrokerOrderResult(
            success=True,
            order_id=oid,
            fill_confirmed=True,
            filled_quantity=reconciled.filled_quantity,
            average_fill_price=reconciled.average_fill_price,
            status=reconciled.status,
            raw=reconciled.raw,
        )

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        et = self.etrade_trading
        if et is None:
            return {"error": "broker_unavailable"}
        try:
            import asyncio

            return await asyncio.to_thread(et.get_order_status, order_id)
        except Exception as exc:
            return {"error": str(exc)}

    async def reconcile_order(
        self,
        order_id: str,
        *,
        requested_quantity: int,
        side: str,
        strategy: str,
        symbol: str,
        trade_id: str,
        quote_mid: Optional[float] = None,
        timeout_sec: Optional[float] = None,
    ) -> BrokerOrderResult:
        et = self.etrade_trading
        if et is None:
            return BrokerOrderResult(success=False, error="broker_unavailable", order_id=order_id)

        try:
            from modules.execution_fill_reconcile import reconcile_order_fill
        except ImportError:
            return BrokerOrderResult(
                success=False,
                error="fill_reconcile_unavailable",
                order_id=order_id,
            )

        import asyncio
        from datetime import datetime, timezone

        if timeout_sec is None:
            try:
                from modules.config_loader import get_config_value

                side_u = str(side or "").upper()
                if side_u == "SELL":
                    timeout_sec = float(get_config_value("EXEC_EXIT_FILL_RECONCILE_TIMEOUT_SEC", 10.0))
                else:
                    timeout_sec = float(get_config_value("EXEC_ENTRY_FILL_RECONCILE_TIMEOUT_SEC", 4.0))
            except Exception:
                timeout_sec = 10.0 if str(side or "").upper() == "SELL" else 4.0

        result = await reconcile_order_fill(
            et,
            order_id,
            requested_quantity=requested_quantity,
            side=side,
            strategy=strategy,
            symbol=symbol,
            trade_id=trade_id,
            signal_ts_iso=datetime.now(timezone.utc).isoformat(),
            quote_mid=quote_mid,
            timeout_sec=float(timeout_sec),
        )
        if not result.actual_fill_confirmed:
            return BrokerOrderResult(
                success=False,
                order_id=order_id,
                fill_confirmed=False,
                filled_quantity=result.filled_quantity,
                average_fill_price=result.average_fill_price,
                status=result.status,
                error="fill_not_confirmed",
                raw=result.raw,
            )
        return BrokerOrderResult(
            success=True,
            order_id=order_id,
            fill_confirmed=True,
            filled_quantity=result.filled_quantity,
            average_fill_price=result.average_fill_price,
            status=result.status,
            raw=result.raw,
        )

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch portfolio and normalize to synchronizer rows.

        Uses ``get_positions`` when present, else ``get_portfolio``. Empty list
        means a successful flat book; fetch failures raise
        ``broker_positions_unavailable`` (sync_unavailable, not broker-flat).
        """
        et = self.etrade_trading
        if et is None:
            raise RuntimeError(f"{BROKER_POSITIONS_UNAVAILABLE}:no_etrade_trading")

        fetch = None
        if callable(getattr(et, "get_positions", None)):
            fetch = et.get_positions
        elif callable(getattr(et, "get_portfolio", None)):
            fetch = et.get_portfolio
        else:
            raise RuntimeError(
                f"{BROKER_POSITIONS_UNAVAILABLE}:missing_get_portfolio_or_get_positions"
            )

        try:
            pos = await asyncio.to_thread(fetch)
        except Exception as err:
            msg = str(err or "")
            if msg.startswith(BROKER_POSITIONS_UNAVAILABLE):
                raise
            raise RuntimeError(f"{BROKER_POSITIONS_UNAVAILABLE}:{err}") from err

        # Prefer rows already shaped by PrimeETradeTrading.get_positions.
        if isinstance(pos, list) and pos and isinstance(pos[0], dict) and "symbol" in pos[0]:
            # Still run through normalizer for OCC Product enrichment.
            return normalize_broker_position_rows(pos)
        return normalize_broker_position_rows(pos or [])

    async def cancel_order(self, order_id: str) -> bool:
        et = self.etrade_trading
        if et is None or not hasattr(et, "cancel_order"):
            return False
        try:
            import asyncio

            return bool(await asyncio.to_thread(et.cancel_order, order_id))
        except Exception:
            return False


def create_broker_adapter(
    *,
    etrade_options_api: Any = None,
    live_mode: bool,
    broker_kind: Optional[BrokerKind] = None,
    options_chain_manager: Any = None,
) -> BrokerAdapter:
    """Factory: returns E*TRADE adapter or fail-closed stub."""
    kind = broker_kind or BrokerKind.from_env(
        __import__("os").getenv("BROKER") or __import__("os").getenv("BROKER_TYPE")
    )
    if kind is BrokerKind.ETRADE:
        if etrade_options_api is None:
            log.warning("BROKER_ADAPTER | etrade_options_api missing — adapter unavailable")
            return StubBrokerAdapter(BrokerKind.ETRADE)
        return ETradeBrokerAdapter(
            etrade_options_api,
            live_mode=live_mode,
            options_chain_manager=options_chain_manager,
        )
    return StubBrokerAdapter(kind)
