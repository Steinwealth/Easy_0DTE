#!/usr/bin/env python3
"""
Multi-broker account routing for unified 0DTE execution.

Loads BROKER_ACCOUNTS_JSON manifest; routes ORB + Trendline paths to configured
broker adapters. Unimplemented brokers use explicit fail-closed stubs.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from modules.broker_adapter import BrokerAdapter, BrokerKind, create_broker_adapter

log = logging.getLogger(__name__)

PATH_ORB_0DTE = "orb_0dte"
PATH_TRENDLINE_0DTE = "trendline_0dte"


@dataclass
class BrokerAccountManifestEntry:
    account_id: str
    broker: str
    paths: Tuple[str, ...] = (PATH_ORB_0DTE, PATH_TRENDLINE_0DTE)
    label: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def _normalize_paths(paths: Any) -> Tuple[str, ...]:
    if not paths:
        return (PATH_ORB_0DTE, PATH_TRENDLINE_0DTE)
    if isinstance(paths, str):
        return tuple(p.strip().lower() for p in paths.split(",") if p.strip())
    return tuple(str(p).strip().lower() for p in paths)


_SUPPORTED_BROKERS = frozenset(
    {
        "etrade",
        "e*trade",
        "ibkr",
        "ib",
        "interactive_brokers",
        "tastytrade",
        "tasty",
        "schwab",
        "charles_schwab",
        "charlesschwab",
    }
)


def parse_broker_account_manifest(raw_json: str) -> List[BrokerAccountManifestEntry]:
    """Strict parse of non-empty BROKER_ACCOUNTS_JSON. Raises ValueError on violation."""
    raw = str(raw_json or "").strip()
    if not raw:
        raise ValueError("empty_manifest")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json:{exc}") from exc

    accounts = data if isinstance(data, list) else data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("accounts_required")

    entries: List[BrokerAccountManifestEntry] = []
    seen_ids: set[str] = set()
    for idx, row in enumerate(accounts):
        if not isinstance(row, dict):
            raise ValueError(f"account_{idx}_not_object")
        aid = str(row.get("id") or row.get("account_id") or "").strip()
        if not aid:
            raise ValueError(f"account_{idx}_missing_id")
        if aid in seen_ids:
            raise ValueError(f"duplicate_account_id:{aid}")
        seen_ids.add(aid)
        broker = str(row.get("broker") or row.get("kind") or "").strip().lower()
        if not broker:
            raise ValueError(f"account_{aid}_missing_broker")
        if broker not in _SUPPORTED_BROKERS:
            raise ValueError(f"unsupported_broker:{broker}")
        entries.append(
            BrokerAccountManifestEntry(
                account_id=aid,
                broker=broker,
                paths=_normalize_paths(row.get("paths")),
                label=str(row.get("label") or ""),
                raw=row,
            )
        )
    return entries


def load_broker_account_manifest(
    raw_json: Optional[str] = None,
) -> List[BrokerAccountManifestEntry]:
    """Parse BROKER_ACCOUNTS_JSON or return default single E*TRADE account."""
    raw = raw_json if raw_json is not None else os.getenv("BROKER_ACCOUNTS_JSON", "").strip()
    if not raw:
        default_id = os.getenv("DEFAULT_0DTE_BROKER_ACCOUNT_ID", "etrade_primary").strip()
        broker = os.getenv("BROKER") or os.getenv("BROKER_TYPE") or "etrade"
        return [
            BrokerAccountManifestEntry(
                account_id=default_id or "etrade_primary",
                broker=str(broker).strip().lower(),
                paths=(PATH_ORB_0DTE, PATH_TRENDLINE_0DTE),
                label="default",
            )
        ]
    return parse_broker_account_manifest(raw)


class BrokerRouter:
    """Route 0DTE paths to broker adapters by account manifest."""

    def __init__(
        self,
        *,
        live_mode: bool,
        etrade_options_api: Any = None,
        adapters: Optional[Dict[str, BrokerAdapter]] = None,
        manifest: Optional[List[BrokerAccountManifestEntry]] = None,
    ) -> None:
        self.live_mode = bool(live_mode)
        self.etrade_options_api = etrade_options_api
        self.manifest = manifest or load_broker_account_manifest()
        self._adapters: Dict[str, BrokerAdapter] = dict(adapters or {})
        self._default_account_id = os.getenv(
            "DEFAULT_0DTE_BROKER_ACCOUNT_ID", ""
        ).strip()
        self._build_adapters()

    def _build_adapters(self) -> None:
        for entry in self.manifest:
            if entry.account_id in self._adapters:
                continue
            kind = BrokerKind.from_env(entry.broker)
            api = self.etrade_options_api if kind is BrokerKind.ETRADE else None
            self._adapters[entry.account_id] = create_broker_adapter(
                etrade_options_api=api,
                live_mode=self.live_mode,
                broker_kind=kind,
            )
        if not self._default_account_id and self.manifest:
            self._default_account_id = self.manifest[0].account_id

    def list_accounts(self) -> List[BrokerAccountManifestEntry]:
        return list(self.manifest)

    def resolve_account_id(
        self,
        path: str,
        explicit_account_id: Optional[str] = None,
    ) -> Optional[str]:
        if explicit_account_id and explicit_account_id in self._adapters:
            return explicit_account_id
        path_l = str(path).lower()
        for entry in self.manifest:
            if path_l in entry.paths or not entry.paths:
                return entry.account_id
        return self._default_account_id or None

    def get_adapter(
        self,
        path: str = PATH_ORB_0DTE,
        *,
        account_id: Optional[str] = None,
    ) -> Optional[BrokerAdapter]:
        aid = self.resolve_account_id(path, account_id)
        if not aid:
            return None
        return self._adapters.get(aid)

    def register_adapter(self, account_id: str, adapter: BrokerAdapter) -> None:
        self._adapters[str(account_id)] = adapter

    def primary_adapter(self) -> Optional[BrokerAdapter]:
        return self.get_adapter(PATH_ORB_0DTE)


_router_singleton: Optional[BrokerRouter] = None


def get_broker_router(
    system: Any = None,
    *,
    live_mode: Optional[bool] = None,
    etrade_options_api: Any = None,
) -> BrokerRouter:
    global _router_singleton
    if system is not None:
        router = getattr(system, "_broker_router", None)
        if router is not None:
            return router
    if _router_singleton is None:
        if live_mode is None:
            try:
                from modules.runtime_mode import is_live_mode

                live_mode = is_live_mode()
            except ImportError:
                live_mode = False
        api = etrade_options_api
        if system is not None:
            api = api or getattr(system, "_etrade_options_api", None)
        _router_singleton = BrokerRouter(live_mode=bool(live_mode), etrade_options_api=api)
    if system is not None:
        system._broker_router = _router_singleton
    return _router_singleton
