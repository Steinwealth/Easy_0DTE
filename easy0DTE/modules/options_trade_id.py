#!/usr/bin/env python3
"""Compact options position / trade IDs for alerts, ledgers, and flight recorders.

Also provides E*TRADE ``clientOrderId`` helpers. Broker constraint (Order API):
≤ 20 alphanumeric characters, unique per account. Underscores and hyphens are
rejected — never send raw app trade ids (legacy ``D-``/``L-`` or ``SYM-strike``)
as ``clientOrderId``.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from typing import Optional, Union

_CLIENT_ORDER_ID_MAX = 20
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
# Compact app ids: GOOGL-347.5p-774 · SPY-585/590c-546
# Legacy mode prefix still parses: D-AAPL-335c-821 · L-SPY-585/590c-546
# Historical dated ids still parse: D-260724-AAPL-335c-821 · 260724-AAPL-335c-821
_TRADE_ID_UNDERLYING = re.compile(
    r"^(?:[DL]-)?(?:\d{6}-)?([A-Z]{1,10})(?:[-_/]|$)",
    re.IGNORECASE,
)
_COMPACT_TRADE_ID = re.compile(
    r"^(?:[DL]-)?(?:\d{6}-)?[A-Z]{1,10}-[\d.]+(?:/[\d.]+)?[cCpP]-\d{1,3}$"
)


def _normalize_expiry_ymd(expiry: Optional[str], *, fallback: str = "") -> str:
    cleaned = str(expiry or "").replace("-", "").strip()
    if len(cleaned) >= 8 and cleaned[:2] in ("19", "20"):
        return cleaned[:8]
    if len(cleaned) == 6 and cleaned.isdigit():
        return f"20{cleaned}"
    return str(fallback or "")


def _expiry_yymmdd(expiry: Optional[str], *, now: datetime) -> str:
    ymd = _normalize_expiry_ymd(expiry)
    if len(ymd) == 8:
        return ymd[2:]
    return now.strftime("%y%m%d")


def compact_expiry_md_for_alert(
    expiry: Optional[str] = None,
    *,
    session_ymd: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Return ``M/D`` when expiry is not the session day; else ``""`` (true 0DTE / unknown)."""
    ts = now or datetime.now()
    sess = _normalize_expiry_ymd(session_ymd)
    if not sess:
        try:
            from modules.calendar_expiry_metadata import us_equity_session_date_iso

            sess = _normalize_expiry_ymd(us_equity_session_date_iso())
        except Exception:
            sess = ts.strftime("%Y%m%d")
    exp = _normalize_expiry_ymd(expiry)
    if not exp or not sess or exp == sess:
        return ""
    try:
        mm, dd = int(exp[4:6]), int(exp[6:8])
    except (TypeError, ValueError):
        return ""
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return ""
    return f"{mm}/{dd}"


def _type_letter(option_type: str) -> str:
    raw = str(option_type or "call").strip().lower()
    if raw.startswith("p"):
        return "p"
    return "c"


def _strike_token(value: Union[int, float]) -> str:
    strike = float(value)
    if abs(strike - round(strike)) < 1e-6:
        return str(int(round(strike)))
    return f"{strike:g}"


def build_options_position_id(
    *,
    live: bool,
    symbol: str,
    option_type: str,
    expiry: Optional[str] = None,
    strike: Optional[float] = None,
    long_strike: Optional[float] = None,
    short_strike: Optional[float] = None,
    now: Optional[datetime] = None,
) -> str:
    """
    Build a short, human-readable trade id.

    ``COIN-165p-047`` · ``TSLA-432c-060`` · spread ``SPY-585/590c-546``
    Demo vs Live is the ledger / app mode, not the id prefix. Trailing 3 digits
    disambiguate same-contract re-entries. ``live`` is accepted for callers.
    Same shape for nearest-weekly / DTE>0 — expiry is **not** embedded (Telegram
    fill line shows ``Exp M/D`` when not session-day).
    """
    ts = now or datetime.now()
    _ = live  # mode lives on the ledger; ids no longer carry D-/L-
    sym = str(symbol or "").strip().upper()
    type_c = _type_letter(option_type)
    seq = int(ts.microsecond) % 1000

    if long_strike is not None and short_strike is not None:
        strike_part = f"{_strike_token(long_strike)}/{_strike_token(short_strike)}{type_c}"
    elif strike is not None:
        strike_part = f"{_strike_token(strike)}{type_c}"
    else:
        raise ValueError("build_options_position_id requires strike or long_strike+short_strike")

    return f"{sym}-{strike_part}-{seq:03d}"


def parse_underlying_from_options_trade_id(trade_id: str) -> str:
    """Extract underlying from compact trade ids (with or without legacy ``D-``/``L-``)."""
    m = _TRADE_ID_UNDERLYING.match(str(trade_id or "").strip())
    return m.group(1).upper() if m else ""


def display_options_trade_id(trade_id: Optional[str]) -> str:
    """Operator-facing id: drop legacy ``D-``/``L-``. Do not rewrite stored ledger keys."""
    s = str(trade_id or "").strip()
    if not s or s == "—":
        return "—"
    if len(s) >= 2 and s[1] == "-" and s[0] in "DLdl":
        return s[2:] or "—"
    return s


def is_compact_options_trade_id(trade_id: str) -> bool:
    """True for compact ``SYM-strikep-seq`` ids, with or without a legacy ``D-``/``L-`` prefix."""
    return bool(_COMPACT_TRADE_ID.match(str(trade_id or "").strip()))


def to_broker_client_order_id(
    *,
    trade_id: Optional[str] = None,
    purpose: str = "O",
    attempt: int = 0,
    now_ts: Optional[float] = None,
) -> str:
    """
    Build an E*TRADE-safe ``clientOrderId`` (≤20 alphanumeric, unique per submit).

    ``purpose`` first letter (default ``O`` open / ``C`` close / ``P`` partial / ``E`` equity).
    ``attempt`` encodes reprice/retry uniqueness (base-36 digit).

    Prefer deriving from the app ``trade_id`` when present so submits stay joinable
    in logs; never send hyphens/underscores/slashes to the broker.
    """
    purpose_c = (str(purpose or "O").strip().upper() or "O")[0]
    if not purpose_c.isalpha():
        purpose_c = "O"
    att = abs(int(attempt)) % 36
    att_c = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[att]
    suffix = f"{purpose_c}{att_c}"

    core = _NON_ALNUM.sub("", str(trade_id or "")).upper()
    if not core:
        ts = float(now_ts) if now_ts is not None else time.time()
        # Compact time token: T + ms since epoch in base36-ish hex (always starts with letter)
        ms = int(ts * 1000.0) % (36**8)
        core = f"T{ms:X}"

    budget = _CLIENT_ORDER_ID_MAX - len(suffix)
    if len(core) > budget:
        digest = hashlib.sha1(core.encode("utf-8")).hexdigest()[:4].upper()
        head = max(0, budget - len(digest))
        core = f"{core[:head]}{digest}"[:budget]

    out = f"{core}{suffix}"[:_CLIENT_ORDER_ID_MAX]
    if out and out[0].isdigit():
        out = f"X{out}"[:_CLIENT_ORDER_ID_MAX]
    return out


def is_broker_client_order_id(value: str) -> bool:
    """True when value meets E*TRADE clientOrderId charset/length constraints."""
    s = str(value or "")
    return bool(s) and len(s) <= _CLIENT_ORDER_ID_MAX and s.isalnum()
