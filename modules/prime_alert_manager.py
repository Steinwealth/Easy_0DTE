#!/usr/bin/env python3
"""
Prime Alert Manager
==================

Comprehensive alert management system for the Easy ORB Strategy.
Handles all Telegram notifications for trade signals, performance updates,
and end-of-day summaries.

Key Features:
- Trade signal alerts (entry/exit notifications)
- Performance monitoring alerts
- End-of-day trade summaries
- System status notifications
- Error and warning alerts
- Configurable alert levels and throttling
- HTML formatting with auto-fallback to plain text
- Aggregated batch exit alerts (Rev 00078)
- Enhanced formatting with bold key metrics (Rev 00231)

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import asyncio
import logging
import json

try:
    from .execution_telemetry import log_option_exit_diagnosis
except ImportError:
    try:
        from modules.execution_telemetry import log_option_exit_diagnosis
    except ImportError:

        def log_option_exit_diagnosis(**_kwargs):  # type: ignore[misc]
            return
import re
import time
import os
import csv
from html import escape
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union, Tuple, Set

try:
    from easy0DTE.modules.options_exit_reason_catalog import normalize_exit_reason
except ImportError:
    def normalize_exit_reason(reason: Optional[str]) -> str:  # type: ignore[misc]
        return str(reason or "unknown").strip() or "unknown"
from dataclasses import dataclass, field
from enum import Enum
import requests
import aiohttp
from collections import defaultdict, deque
import threading

try:
    from .config_loader import get_config_value
except ImportError:
    # Fallback for direct imports
    def get_config_value(key, default=''):
        import os
        return os.getenv(key, default)

log = logging.getLogger(__name__)


def _telegram_html_escape(text: object) -> str:
    """Escape user/system text for Telegram HTML parse_mode."""
    if text is None:
        return ""
    return escape(str(text), quote=False)


def _trade_id_text(text: object) -> str:
    """Operator-facing trade id (legacy ``D-``/``L-`` stripped)."""
    try:
        from easy0DTE.modules.options_trade_id import display_options_trade_id

        return display_options_trade_id(text)
    except Exception:
        s = str(text or "").strip()
        if len(s) >= 2 and s[1] == "-" and s[0] in "DLdl":
            return s[2:] or "—"
        return s or "—"


def _resolve_telegram_mode_label(mode: Optional[object] = None) -> str:
    """
    Return ``DEMO`` or ``LIVE`` for ``| {mode} Mode`` alert headers.

    Always uses canonical runtime ``MODE`` (``runtime_mode.mode_label``). Both the DEMO
    and LIVE Cloud Run apps may run in either demo or live trading mode; the label
    reflects trading mode, not which service deployed the alert. The optional ``mode``
    argument is accepted for call-site compatibility but is not used for the header.
    """
    from .runtime_mode import mode_label

    return mode_label()


def _alert_mode_header_line(mode: object) -> str:
    """
    Indented second header line for trading alerts: ``| DEMO Mode`` / ``| LIVE Mode``.

    Reflects runtime ``MODE`` (simulated ledger vs broker real money), not which Cloud Run
    service sent the alert. Do **not** use on system/OAuth alerts (Good Morning, Holiday,
    OAuth Tokens Expired/Renewed).
    """
    label = _resolve_telegram_mode_label(mode)
    return f"          | {_telegram_html_escape(label)} Mode"


def _telegram_mode_emoji() -> str:
    """Emoji for inline mode hints (🎮 demo / 💰 live) from runtime MODE."""
    from .runtime_mode import is_demo_mode

    return "🎮" if is_demo_mode() else "💰"


def _portfolio_protection_alert_header(mode: object, *, observe_only: bool = False) -> str:
    """Portfolio Protection title block: protection · observe/blocking · mode."""
    label = _resolve_telegram_mode_label(mode)
    mode_esc = _telegram_html_escape(label)
    action = "OBSERVE ONLY" if observe_only else "BLOCKING"
    return f"""🛡️ <b>PORTFOLIO PROTECTION</b>
          | {action}
          | {mode_esc} Mode"""


ALERT_HEADER_BAR = "===================================================================="
RESEARCH_APP_ALERT_BANNER = "🏁 RESEARCH APP 🏁 RESEARCH APP 🏁"
# Legacy alias — tests and call sites may still import the old name.
DEMO_APP_ALERT_BANNER = RESEARCH_APP_ALERT_BANNER


def _research_app_alert_banner_present(message: str) -> bool:
    """True when the RESEARCH-app desk banner is already near the top of the payload."""
    if not message:
        return False
    head = message[:280]
    return RESEARCH_APP_ALERT_BANNER in head or "🏁 DEMO MODE 🏁" in head


def _inject_research_app_alert_banner(message: str) -> str:
    """
    Inject RESEARCH-app desk banner immediately under ``ALERT_HEADER_BAR``.

    Distinct from trading-alert ``| DEMO Mode`` lines (runtime MODE) — this banner
    identifies the RESEARCH Cloud Run desk on every Telegram payload.
    """
    if not message or _research_app_alert_banner_present(message):
        return message

    if message.startswith(ALERT_HEADER_BAR):
        remainder = message[len(ALERT_HEADER_BAR) :]
        if remainder.startswith("\r\n"):
            remainder = remainder[2:]
        elif remainder.startswith("\n"):
            remainder = remainder[1:]
        return f"{ALERT_HEADER_BAR}\n{RESEARCH_APP_ALERT_BANNER}\n\n{remainder.lstrip()}"

    return f"{ALERT_HEADER_BAR}\n{RESEARCH_APP_ALERT_BANNER}\n\n{message.lstrip()}"


def _inject_demo_app_alert_banner(message: str) -> str:
    """Legacy alias for ``_inject_research_app_alert_banner``."""
    return _inject_research_app_alert_banner(message)


def _prepare_research_app_telegram_message(message: str) -> str:
    """Normalize HTML and prepend RESEARCH app desk banner for Telegram send.

    RESEARCH Cloud Run only. FINAL must use ``_prepare_telegram_message_for_send``.
    """
    return _inject_research_app_alert_banner(_telegram_normalize_html_for_send(message))


def _strip_research_desk_banner_lines(message: str) -> str:
    """Remove accidental RESEARCH/DEMO desk banner lines (FINAL hygiene)."""
    if not message:
        return message
    banner_tokens = (
        RESEARCH_APP_ALERT_BANNER,
        "🏁 RESEARCH APP 🏁",
        "🏁 DEMO MODE 🏁",
    )
    out_lines: List[str] = []
    for line in str(message).splitlines():
        stripped = line.strip()
        if any(tok in stripped for tok in banner_tokens) and stripped.startswith("🏁"):
            continue
        out_lines.append(line)
    text = "\n".join(out_lines)
    # Collapse accidental blank runs after strip (keep single blank after header bar).
    if text.startswith(ALERT_HEADER_BAR + "\n\n\n"):
        text = ALERT_HEADER_BAR + "\n\n" + text[len(ALERT_HEADER_BAR) + 3 :].lstrip("\n")
    return text


def _prepare_telegram_message_for_send(message: str) -> str:
    """FINAL app Telegram prepare: HTML normalize only — no desk-banner injection."""
    return _strip_research_desk_banner_lines(_telegram_normalize_html_for_send(message or ""))


_DESK_DASHBOARD_URL = "https://easy-0dte.web.app"
_PRE_HOLIDAY_NAME_TAIL = re.compile(r"\s*\(\d{4}-\d{2}-\d{2},\s*[A-Z_]+\)$")


def _pre_holiday_display_name(holiday_name: str) -> str:
    """Human title: drop 'Trading Session Before' and the (YYYY-MM-DD, REASON) tail."""
    raw = str(holiday_name or "").strip()
    prefix = "Trading Session Before "
    if raw.startswith(prefix):
        raw = raw[len(prefix) :]
    raw = _PRE_HOLIDAY_NAME_TAIL.sub("", raw).strip()
    return raw or str(holiday_name or "").strip()


def _holiday_alert_copy(holiday_name: str, skip_reason: str) -> tuple[str, str, str]:
    """Return emoji, title, and reason copy for holiday-style disabled days."""
    if skip_reason == "MARKET_CLOSED":
        return (
            "🏖️",
            f"Holiday! - {holiday_name}",
            "U.S. stock market is closed today. Trading disabled to preserve capital quality.",
        )
    if skip_reason == "PRE_HOLIDAY":
        display = _pre_holiday_display_name(holiday_name)
        return (
            "🛡️",
            f"Pre-Holiday Risk Session! - Day before {display}",
            "This is the final trading session before a holiday or low-volume market event. "
            "Trading disabled to avoid unreliable pre-holiday price action.",
        )
    if skip_reason == "ELECTION_DAY":
        return (
            "🗳️",
            f"Election Day! - {holiday_name}",
            "NYSE is open. Easy 0DTE trading is disabled on U.S. federal Election Day.",
        )
    return (
        "🎃",
        f"Holiday! - {holiday_name}",
        "Market is open, but volume is typically low on this holiday. "
        "Trading disabled to preserve capital quality.",
    )


def _normalize_strategy_alert_id(strategy_id: object) -> str:
    """Canonical strategy id for shared entry/exit Telegram contracts."""
    raw = str(strategy_id or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in ("orb1", "orb_0dte", "orb1_breakout", "easy_orb_0dte", "orb"):
        return "orb1"
    if raw in ("breakout", "orb2", "orb2_breakout", "breakout_0dte", "easy_breakout_0dte"):
        return "breakout"
    if raw in ("trendline", "tl", "trendline_0dte", "easy_trendline_0dte"):
        return "trendline"
    return "orb1"


def _strategy_execution_alert_title(
    strategy_id: object,
    row: Optional[Dict[str, Any]] = None,
    *,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Shared rolling-entry Telegram title for ORB1 / ORB2 / Trendline."""
    sid = _normalize_strategy_alert_id(strategy_id)
    if sid == "trendline":
        return _trendline_execution_alert_title(row, rows=rows)
    if sid == "breakout":
        return _breakout_execution_alert_title()
    return "🔮 <b>Breakout Order Filled</b>"


def _fill_strike_delta_line(
    strike: float,
    delta: float,
    row: Optional[Dict[str, Any]] = None,
) -> str:
    """Strike • optional Exp M/D (non-0DTE) • Delta — used by ORB1/TL/BO fill alerts."""
    blob = row if isinstance(row, dict) else {}
    lotto = blob.get("lotto_contract") if isinstance(blob.get("lotto_contract"), dict) else {}
    raw = (
        blob.get("expiry_ymd")
        or blob.get("selected_expiry_ymd")
        or blob.get("expiration_ymd")
        or blob.get("expiry")
        or lotto.get("expiry_ymd")
        or lotto.get("expiry")
        or ""
    )
    if not raw:
        raw = _expiry_ymd_from_trade_id(blob.get("trade_id"))
    exp_label = ""
    try:
        from easy0DTE.modules.options_trade_id import compact_expiry_md_for_alert

        exp_label = compact_expiry_md_for_alert(str(raw) if raw else None)
    except Exception:
        exp_label = ""
    if exp_label:
        return f"Strike: {strike:.2f} • Exp {exp_label} • Delta: {delta:.2f}"
    return f"Strike: {strike:.2f} • Delta: {delta:.2f}"


def _expiry_ymd_from_trade_id(trade_id: Any) -> str:
    """Best-effort YYYY-MM-DD from trade ids like ``D-260814-ADBE-275c-292``."""
    tid = str(trade_id or "").strip().upper()
    if not tid:
        return ""
    # Prefer explicit 8-digit YYYYMMDD token when present.
    m8 = re.search(r"(?:^|[^0-9])(20\d{6})(?:[^0-9]|$)", tid)
    if m8:
        dig = m8.group(1)
        return f"{dig[0:4]}-{dig[4:6]}-{dig[6:8]}"
    # Common demo/live form: D-YYMMDD-SYM-…
    m6 = re.search(r"(?:^|-)(\d{6})-", tid)
    if m6:
        dig = m6.group(1)
        yy, mm, dd = int(dig[0:2]), int(dig[2:4]), int(dig[4:6])
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"20{yy:02d}-{mm:02d}-{dd:02d}"
    return ""


def _collect_expiry_fields_for_exit_alert(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    is_0dte: Optional[bool] = None,
    calendar_dte: Any = None,
    trade_id: Any = None,
) -> Dict[str, Any]:
    """Resolve Expiry / DTE / Is_0dte for closed-position Telegram bodies.

    Fill-time stamps often live under ``execution_payload`` / ``signal_snapshot`` /
    ``normalized_options`` rather than metadata top-level — exit alerts must dig.
    """
    try:
        from modules.calendar_expiry_metadata import (
            extract_expiry_fields_from_mapping,
            merge_expiry_trade_fields,
            normalize_expiry_iso,
            us_equity_session_date_iso,
        )
    except Exception:
        extract_expiry_fields_from_mapping = None  # type: ignore[assignment]
        merge_expiry_trade_fields = None  # type: ignore[assignment]
        normalize_expiry_iso = None  # type: ignore[assignment]
        us_equity_session_date_iso = None  # type: ignore[assignment]

    md = metadata if isinstance(metadata, dict) else {}
    norm = md.get("normalized_options") if isinstance(md.get("normalized_options"), dict) else {}
    payload = md.get("execution_payload") if isinstance(md.get("execution_payload"), dict) else {}
    snap = md.get("signal_snapshot") if isinstance(md.get("signal_snapshot"), dict) else {}
    osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
    eng = osnap.get("engine") if isinstance(osnap.get("engine"), dict) else {}
    legs = norm.get("legs") if isinstance(norm.get("legs"), list) else []
    leg0 = legs[0] if legs and isinstance(legs[0], dict) else {}

    merged: Dict[str, Any] = {}
    if extract_expiry_fields_from_mapping is not None:
        for src in (md, norm, payload, snap, osnap, eng, leg0):
            try:
                part = extract_expiry_fields_from_mapping(src)
            except Exception:
                part = {}
            for k, v in (part or {}).items():
                if k not in merged and v is not None and v != "":
                    merged[k] = v
            # Alias keys not always in EXPIRY_TRADE_FIELD_KEYS
            if isinstance(src, dict):
                for ak, dest in (
                    ("expiration_ymd", "expiry_ymd"),
                    ("expiration", "expiry_ymd"),
                    ("expiry", "expiry_ymd"),
                ):
                    if dest not in merged and src.get(ak) not in (None, ""):
                        raw = src.get(ak)
                        if normalize_expiry_iso is not None:
                            try:
                                raw = normalize_expiry_iso(raw) or raw
                            except Exception:
                                pass
                        merged[dest] = raw

    if is_0dte is not None and "is_0dte" not in merged:
        merged["is_0dte"] = bool(is_0dte)
    if calendar_dte is not None and "calendar_dte" not in merged:
        merged["calendar_dte"] = calendar_dte
        if "dte" not in merged:
            merged["dte"] = calendar_dte

    if not merged.get("expiry_ymd") and not merged.get("selected_expiry_ymd"):
        tid_exp = _expiry_ymd_from_trade_id(trade_id)
        if tid_exp:
            merged["expiry_ymd"] = tid_exp
            merged.setdefault("selected_expiry_ymd", tid_exp)

    # Derive calendar_dte / is_0dte when we have an expiry date.
    exp = merged.get("selected_expiry_ymd") or merged.get("expiry_ymd")
    if exp and merge_expiry_trade_fields is not None:
        try:
            session = merged.get("session_date")
            if not session and us_equity_session_date_iso is not None:
                session = us_equity_session_date_iso()
            merge_expiry_trade_fields(
                merged,
                selected_expiry=exp,
                requested_expiry=merged.get("requested_expiry_ymd") or session,
                session_date=session,
                expiry_selection_mode=merged.get("expiry_selection_mode"),
                fallback_reason=merged.get("fallback_reason"),
                fallback_days=merged.get("fallback_days"),
                prefer_existing=True,
            )
        except Exception:
            pass

    if merged.get("calendar_dte") is None and merged.get("dte") is not None:
        merged["calendar_dte"] = merged.get("dte")
    if merged.get("dte") is None and merged.get("calendar_dte") is not None:
        merged["dte"] = merged.get("calendar_dte")
    return merged


def _strategy_exit_alert_title(
    strategy_id: object,
    row: Optional[Dict[str, Any]] = None,
    *,
    is_0dte: Optional[bool] = None,
    calendar_dte: Any = None,
) -> str:
    """Shared position-closed Telegram title for ORB1 / ORB2 / Trendline."""
    sid = _normalize_strategy_alert_id(strategy_id)
    if sid == "trendline":
        return _trendline_exit_alert_title(row, is_0dte=is_0dte, calendar_dte=calendar_dte)
    if sid == "breakout":
        return _breakout_exit_alert_title(is_0dte=is_0dte, calendar_dte=calendar_dte)
    return "🔮 <b>Breakout Order Closed</b>"


def _strategy_exit_dedupe_attr(strategy_id: object) -> str:
    sid = _normalize_strategy_alert_id(strategy_id)
    if sid == "trendline":
        return "_trendline_exit_alert_trade_ids"
    if sid == "breakout":
        return "_breakout_exit_alert_trade_ids"
    return "_orb_0dte_exit_alert_trade_ids"


def _strategy_log_tag(strategy_id: object) -> str:
    sid = _normalize_strategy_alert_id(strategy_id)
    if sid == "trendline":
        return "trendline_0dte"
    if sid == "breakout":
        return "breakout_0dte"
    return "orb_0dte"


def _strategy_setup_display(strategy_id: object, row: Dict[str, Any]) -> str:
    """Human Setup line for shared single-emit entry alerts."""
    sid = _normalize_strategy_alert_id(strategy_id)
    if sid == "breakout":
        return _breakout_setup_display(row)
    if sid == "orb1":
        setup = str(row.get("setup_type") or row.get("entry_type") or "").strip()
        if setup:
            return setup.replace("_", " ")
        side = str(row.get("option_side") or "").strip().lower()
        direction = str(row.get("direction") or "").strip().upper()
        if side == "put" or direction in ("SHORT", "PUT"):
            return "orb1 breakout below orb low"
        if side == "call" or direction in ("LONG", "CALL"):
            return "orb1 breakout above orb high"
        return "orb1 range breakout"
    return str(row.get("setup_type") or "n/a").strip().replace("_", " ") or "n/a"


def _trendline_slot_alert_label(row: Optional[Dict[str, Any]] = None) -> str:
    """Resolve TL0/TL1/TL2/TL3 for Trendline execution/exit Telegram titles."""
    raw: Any = None
    if isinstance(row, dict):
        raw = row.get("trendline_slot")
        if raw is None:
            setup = row.get("setup_payload")
            if isinstance(setup, dict):
                raw = setup.get("trendline_slot")
        if raw is None:
            snap = row.get("signal_snapshot")
            if isinstance(snap, dict):
                raw = snap.get("trendline_slot")
        if raw is None:
            md = row.get("metadata")
            if isinstance(md, dict):
                raw = md.get("trendline_slot")
                if raw is None:
                    nested = md.get("setup_payload")
                    if isinstance(nested, dict):
                        raw = nested.get("trendline_slot")
                if raw is None:
                    snap = md.get("signal_snapshot")
                    if isinstance(snap, dict):
                        raw = snap.get("trendline_slot")
    try:
        from easyTrendline.trendline_models import trendline_slot_short_label

        return trendline_slot_short_label(raw)
    except Exception:
        return "TL1"


def _trendline_execution_alert_title(
    row: Optional[Dict[str, Any]] = None,
    *,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Trendline position-open execution Telegram title (fill or post-executor reject)."""
    label = None
    if isinstance(row, dict):
        label = _trendline_slot_alert_label(row)
    elif rows:
        labels = {
            _trendline_slot_alert_label(r)
            for r in rows
            if isinstance(r, dict)
        }
        if len(labels) == 1:
            label = next(iter(labels))
        elif not labels:
            label = "TL1"
        else:
            # Mixed TL slots in one batch summary — keep unscoped title.
            return "📏 <b>Trendline Order Filled</b>"
    else:
        label = "TL1"
    return f"📏 <b>Trendline {label} Order Filled</b>"


def _trendline_exit_alert_title(
    row: Optional[Dict[str, Any]] = None,
    *,
    is_0dte: Optional[bool] = None,
    calendar_dte: Any = None,
) -> str:
    """Trendline position-close Telegram title (stealth / EOD exit). Includes TL0/TL1/TL2/TL3."""
    label = _trendline_slot_alert_label(row)
    _ = (is_0dte, calendar_dte)  # DTE no longer in title; body carries expiry/DTE rows
    return f"📏 <b>Trendline {label} Order Closed</b>"


def _breakout_execution_alert_title() -> str:
    """ORB2 Breakout position-open execution Telegram title."""
    return "⛓️ <b>Breakout Order Filled</b>"


def _breakout_exit_alert_title(*, is_0dte: Optional[bool] = None, calendar_dte: Any = None) -> str:
    """ORB2 Breakout position-close Telegram title (stealth / EOD exit)."""
    _ = (is_0dte, calendar_dte)  # DTE no longer in title; body carries expiry/DTE rows
    return "⛓️ <b>Breakout Order Closed</b>"


def _position_dict_is_trendline_0dte(position: Dict[str, Any]) -> bool:
    """True when a closed-position dict belongs to Trendline 0DTE (not ORB/Breakout)."""
    if not isinstance(position, dict):
        return False
    # Breakout reuses TL-compatible signal metadata — never treat as trendline-owned.
    if _position_dict_is_breakout_0dte(position):
        return False
    st = str(position.get("strategy_type") or "").strip().lower()
    if "trendline" in st:
        return True
    md = position.get("metadata")
    if isinstance(md, dict):
        norm = md.get("normalized_options")
        if isinstance(norm, dict):
            nst = str(
                norm.get("strategy_type") or norm.get("source_path") or ""
            ).strip().lower()
            if "trendline" in nst and "breakout" not in nst:
                return True
        ep = md.get("execution_payload")
        if isinstance(ep, dict):
            est = str(
                ep.get("strategy_type") or ep.get("source_path") or ""
            ).strip().lower()
            if "trendline" in est and "breakout" not in est:
                return True
    return False


def _position_dict_is_breakout_0dte(position: Dict[str, Any]) -> bool:
    """True when a closed-position dict belongs to Breakout 0DTE."""
    if not isinstance(position, dict):
        return False
    st = str(position.get("strategy_type") or "").strip().lower()
    if "breakout" in st:
        return True
    md = position.get("metadata")
    if isinstance(md, dict):
        norm = md.get("normalized_options")
        if isinstance(norm, dict):
            nst = str(
                norm.get("strategy_type") or norm.get("source_path") or ""
            ).strip().lower()
            if "breakout" in nst:
                return True
        ep = md.get("execution_payload")
        if isinstance(ep, dict):
            est = str(
                ep.get("strategy_type") or ep.get("source_path") or ""
            ).strip().lower()
            if "breakout" in est:
                return True
        snap = md.get("signal_snapshot")
        if isinstance(snap, dict):
            sst = str(snap.get("strategy_type") or snap.get("source_path") or "").strip().lower()
            if "breakout" in sst:
                return True
    return False


def _closed_position_batch_strategy_id(position: Dict[str, Any]) -> str:
    """Canonical path for EOD/batch close ordering: orb1 → breakout → trendline."""
    if _position_dict_is_breakout_0dte(position):
        return "breakout"
    if _position_dict_is_trendline_0dte(position):
        return "trendline"
    return "orb1"


def _closed_position_batch_sort_key(position: Dict[str, Any]) -> tuple:
    sid = _closed_position_batch_strategy_id(position)
    rank = {"orb1": 0, "breakout": 1, "trendline": 2}.get(sid, 9)
    slot = _trendline_slot_alert_label(position) if sid == "trendline" else ""
    sym = str(position.get("symbol") or "").strip().upper()
    tid = str(position.get("position_id") or position.get("trade_id") or "")
    return (rank, slot, sym, tid)


def _batch_close_strategy_group_html(position: Dict[str, Any]) -> str:
    sid = _closed_position_batch_strategy_id(position)
    if sid == "breakout":
        return "⛓️ <b>ORB2 Breakout</b>"
    if sid == "trendline":
        return f"📏 <b>Trendline {_trendline_slot_alert_label(position)}</b>"
    return "🔮 <b>ORB1 Breakout</b>"


def _breakout_setup_display(row: Dict[str, Any]) -> str:
    """Human setup line: 'breakout above orb high' / 'breakout below orb low'."""
    trig = str(row.get("trigger_direction") or "").strip().lower().replace("_", " ")
    if "below" in trig or "orb low" in trig:
        return "breakout below orb low"
    if "above" in trig or "orb high" in trig:
        return "breakout above orb high"
    side = str(row.get("option_side") or "").strip().lower()
    if side == "put":
        return "breakout below orb low"
    if side == "call":
        return "breakout above orb high"
    setup = str(row.get("setup_type") or "orb range breakout").strip().replace("_", " ")
    return setup or "orb range breakout"

_TELEGRAM_ALLOWED_TAG_RE = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler|span|a)\b[^>]*>|<br\s*/?>",
    re.I,
)


def _telegram_escape_stray_lt(text: str) -> str:
    """Escape ``<`` that are not allowed Telegram HTML tags (e.g. ORB cutoff ``price < orb_high``)."""
    if not text or "<" not in text:
        return text
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "<":
            out.append(text[i])
            i += 1
            continue
        rest = text[i:]
        br = re.match(r"(?i)<br\s*/?>", rest)
        if br:
            out.append("<br>")
            i += br.end()
            continue
        tag = re.match(
            r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler|span|a)\b[^>]*>",
            rest,
            re.I,
        )
        if tag:
            out.append(tag.group(0))
            i += len(tag.group(0))
            continue
        out.append("&lt;")
        i += 1
    return "".join(out)


def _telegram_harden_html_chunks(text: str) -> str:
    """Harden dynamic text between allowed Telegram HTML tags."""
    if not text:
        return text
    parts = _TELEGRAM_ALLOWED_TAG_RE.split(text)
    tags = _TELEGRAM_ALLOWED_TAG_RE.findall(text)
    if not tags:
        return _telegram_escape_stray_lt(text)
    rebuilt: List[str] = []
    for idx, part in enumerate(parts):
        if part:
            rebuilt.append(_telegram_escape_stray_lt(part))
        if idx < len(tags):
            tag = tags[idx]
            rebuilt.append("<br>" if re.match(r"(?i)<br", tag) else tag)
    return "".join(rebuilt)


def _telegram_plain_text_from_html(text: str) -> str:
    """Strip HTML for plain-text Telegram fallback (preserve line breaks)."""
    if not text:
        return text
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    return re.sub(r"<[^>]+>", "", text)


def _telegram_normalize_html_for_send(text: str) -> str:
    """Normalize markup so Telegram Bot API HTML parse_mode accepts the payload.

    Self-closing ``<br/>`` / ``<br />`` is a common cause of parse errors; the client then
    retries without parse_mode and users see plain text without bold. Telegram documents ``<br>``.
    Stray ``<`` in rejection reasons (ORB cutoff comparisons) is escaped via
    ``_telegram_harden_html_chunks``.
    """
    if not text:
        return text
    text = re.sub(r"(?i)<br\s*/\s*>", "<br>", text)
    return _telegram_harden_html_chunks(text)


# Telegram sendMessage hard limit (UTF-16 code units); stay under for HTML + plain retries.
TELEGRAM_MAX_MESSAGE_LEN = 4096

# Lower number = higher priority (fills jump ahead of reject floods).
TELEGRAM_PRIORITY_FILL = 0
TELEGRAM_PRIORITY_NORMAL = 1


@dataclass(order=True)
class _TelegramOutboundJob:
    priority: int
    seq: int
    message: str = field(compare=False)
    future: Any = field(compare=False)


TELEGRAM_SAFE_MESSAGE_LEN = 3900


def _telegram_strip_incomplete_trailing_tag(text: str) -> str:
    """Remove a trailing partial HTML tag left by naive character truncation."""
    if not text or "<" not in text:
        return text
    last_lt = text.rfind("<")
    if last_lt >= 0 and text.rfind(">", last_lt) < last_lt:
        return text[:last_lt].rstrip()
    return text


def _telegram_balance_html_tags(text: str) -> str:
    """Append closing tags for any unclosed Telegram-allowed open tags."""
    if not text:
        return text
    stack: List[str] = []
    for tag in _TELEGRAM_ALLOWED_TAG_RE.findall(text):
        if re.match(r"(?i)<br", tag):
            continue
        close_m = re.match(r"(?i)</([a-z][\w-]*)", tag)
        if close_m:
            name = close_m.group(1).lower()
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == name:
                    stack.pop(i)
                    break
            continue
        open_m = re.match(r"(?i)<([a-z][\w-]*)", tag)
        if open_m:
            stack.append(open_m.group(1).lower())
    if not stack:
        return text
    return text + "".join(f"</{name}>" for name in reversed(stack))


def _telegram_fit_message(text: str, max_len: int = TELEGRAM_SAFE_MESSAGE_LEN) -> str:
    """Truncate oversized payloads so Telegram does not return HTTP 400."""
    if not text or len(text) <= max_len:
        return text
    suffix = "\n\n<i>… message truncated for Telegram length limit</i>"
    budget = max(200, max_len - len(suffix))
    body = _telegram_strip_incomplete_trailing_tag(text[:budget].rstrip())
    fitted = _telegram_balance_html_tags(body + suffix)
    while len(fitted) > max_len and len(body) > 200:
        body = _telegram_strip_incomplete_trailing_tag(body[: max(200, len(body) - 80)].rstrip())
        fitted = _telegram_balance_html_tags(body + suffix)
    return fitted


def _fmt_usd(amount: Union[int, float], decimals: int = 2) -> str:
    """Format a dollar amount with thousands separators (e.g. $12,345.67)."""
    return f"${float(amount):,.{decimals}f}"


def _fmt_usd_signed(amount: Union[int, float], decimals: int = 2) -> str:
    """Format a signed dollar amount (e.g. +$12,345.67 or -$12,345.67)."""
    val = float(amount)
    sign = "+" if val >= 0 else "-"
    return f"{sign}${abs(val):,.{decimals}f}"


def _fmt_num(amount: Union[int, float], decimals: int = 2) -> str:
    """Format a plain number with thousands separators (e.g. 12,345.67)."""
    return f"{float(amount):,.{decimals}f}"


def _fmt_pct(pct: Union[int, float], decimals: int = 2) -> str:
    """Signed percent with thousands separators (e.g. +2,281.71)."""
    val = float(pct)
    sign = "+" if val >= 0 else "-"
    return f"{sign}{abs(val):,.{decimals}f}"


def _fmt_pct_suffix(pct: Union[int, float], decimals: int = 2) -> str:
    """Signed percent with thousands separators and % suffix (e.g. +2,055.26%)."""
    return f"{_fmt_pct(pct, decimals)}%"


def _fmt_qty(qty: Union[int, float]) -> str:
    """Integer quantity with thousands separators (e.g. 1,234)."""
    return f"{int(qty):,}"


def _fmt_profit_factor(value: float) -> str:
    """Profit factor for EOD reports — comma-separated, infinity when all wins."""
    if value == float("inf"):
        return "∞"
    return _fmt_num(value)

# User-visible names for the three traded ledgers (ORB ETF, ORB1 Breakout options, Trendline 0DTE options)
ALERT_LABEL_EASY_ORB_ETF = "Easy ORB"
ALERT_LABEL_EASY_ORB_0DTE_OPTIONS = "Easy ORB1 Breakout"
ALERT_LABEL_EASY_TRENDLINE_0DTE = "Easy Trendline 0DTE"
ALERT_LABEL_EASY_BREAKOUT_0DTE = "Easy Breakout 0DTE"


def _orb_0dte_execution_alert_title() -> str:
    return "🔮 <b>Breakout Order Filled</b>"


def _orb_0dte_execution_alert_header_block(mode: object) -> str:
    """Title + indented mode line (bold title — matches DEMO/LIVE Telegram layout)."""
    return f"{_orb_0dte_execution_alert_title()}\n{_alert_mode_header_line(mode)}"


def _orb_0dte_option_side_label(direction_or_side: str) -> str:
    raw = str(direction_or_side or "").strip().upper()
    if raw in ("SHORT", "PUT", "P"):
        return "PUT"
    if raw in ("LONG", "CALL", "C"):
        return "CALL"
    return raw or "—"


def _split_0dte_convex_reason(reason_raw: str) -> Tuple[str, str]:
    """Split ``score=… | detail`` convex / gate rejection text."""
    text = str(reason_raw or "").strip()
    match = re.match(r"^(score=[^|]+)\s*\|\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ("", text)
    score_part = match.group(1).strip()
    detail = match.group(2).strip()
    bracket = detail.find(" [")
    if bracket > 0:
        detail = detail[:bracket].strip()
    for prefix in ("LONG:", "SHORT:", "CALL:", "PUT:"):
        if detail.upper().startswith(prefix):
            detail = detail[len(prefix) :].strip()
            break
    return (score_part, detail)


def _format_0dte_details_detail_line(detail: str) -> str:
    d = detail.strip()
    if d.lower().startswith("volatility score "):
        rest = d[len("Volatility score ") :]
        rest = re.sub(r"\s*percentile\s*", " ", rest, flags=re.IGNORECASE).strip()
        return f"Volatility {rest}"
    return d


def _format_0dte_diag_detail_line(detail: str) -> str:
    return re.sub(r"\s*percentile\s*", " ", detail.strip(), flags=re.IGNORECASE).strip()


def _good_morning_trading_schedule_block() -> str:
    return """📊 Trading Schedule:
    <b>ORB1 Capture:</b>
          6:30 - 6:45 AM PT (9:30 - 9:45 AM ET)
    <b>TL0 Discovery:</b>
          6:45 - 7:10 AM PT (9:45 - 10:10 AM ET)
    <b>ORB1 Breakouts:</b>
          6:50 - 7:10 AM PT (9:50 - 10:10 AM ET)
    <b>ORB2 / TL1 unlock:</b>
          ≥7:30 AM PT (10:30 AM ET)"""


def _good_morning_broker_other_tokens_na() -> str:
    return """          • Interactive Brokers token:
            ❏ n/a
          • TastyTrade token:
            ❏ n/a"""


def _good_morning_status_ready_block() -> str:
    return """💎 Status:
          Trading system ready!
          Demo = Ready
          Live = Data Only"""


class AlertLevel(Enum):
    """Alert level enumeration"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    SUCCESS = "success"

class AlertType(Enum):
    """Alert type enumeration"""
    TRADE_SIGNAL = "trade_signal"
    TRADE_ENTRY = "trade_entry"
    TRADE_EXIT = "trade_exit"
    PERFORMANCE_UPDATE = "performance_update"
    END_OF_DAY_SUMMARY = "end_of_day_summary"
    SYSTEM_STATUS = "system_status"
    ERROR = "error"
    WARNING = "warning"
    MARKET_ALERT = "market_alert"
    # OAuth Token Management Alerts
    OAUTH_RENEWAL = "oauth_renewal"
    OAUTH_SUCCESS = "oauth_success"
    OAUTH_EXPIRED = "oauth_expired"
    OAUTH_ERROR = "oauth_error"
    OAUTH_WARNING = "oauth_warning"
    OAUTH_TOKEN_RENEWED_CONFIRMATION = "oauth_token_renewed_confirmation"
    
    # Trading Pipeline Alerts (Rev 00173)
    # ORB Strategy Alerts (ACTIVE)
    ORB_CAPTURE_COMPLETE = "orb_capture_complete"
    SO_EXECUTION_AGGREGATED = "so_execution_aggregated"
    ORR_EXECUTION_INDIVIDUAL = "orr_execution_individual"
    ORB_NO_SIGNALS = "orb_no_signals"
    
    # Easy 0DTE Alerts (Rev 00206)
    ODTE_ORB_CAPTURE = "0dte_orb_capture"
    OPTIONS_SIGNAL_COLLECTION = "options_signal_collection"
    OPTIONS_EXECUTION = "options_execution"
    OPTIONS_POSITION_EXIT = "options_position_exit"
    OPTIONS_AGGREGATED_EXIT = "options_aggregated_exit"
    OPTIONS_PARTIAL_PROFIT = "options_partial_profit"
    OPTIONS_RUNNER_EXIT = "options_runner_exit"
    OPTIONS_HEALTH_CHECK = "options_health_check"

@dataclass
class Alert:
    """Alert data structure"""
    alert_id: str
    alert_type: AlertType
    level: AlertLevel
    title: str
    message: str
    symbol: Optional[str] = None
    strategy: Optional[str] = None
    confidence: Optional[float] = None
    expected_return: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TradeAlert:
    """Trade-specific alert data structure"""
    symbol: str
    strategy: str
    action: str  # "BUY" or "SELL"
    price: float
    quantity: int
    confidence: float
    expected_return: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class PerformanceSummary:
    """End-of-day performance summary"""
    date: datetime
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    daily_return: float
    active_positions: int
    signals_generated: int
    signals_accepted: int
    acceptance_rate: float
    top_performers: List[Dict[str, Any]] = field(default_factory=list)
    worst_performers: List[Dict[str, Any]] = field(default_factory=list)
    
    # Additional metrics for enhanced reporting
    max_drawdown: float = 0.0
    capital_used_pct: float = 0.0
    consecutive_wins: int = 0
    avg_risk_per_trade: float = 4.2
    total_pnl_dollars: float = 0.0

@dataclass
class OAuthAlert:
    """OAuth-specific alert data structure"""
    environment: str  # "prod" (production OAuth only)
    alert_type: str  # "renewal", "success", "error", "warning"
    message: str
    oauth_url: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

class PrimeAlertManager:
    """Prime Alert Manager for comprehensive notification system"""
    
    def __init__(self):
        self.name = "Prime Alert Manager"
        self.version = "1.0"
        # UNIQUE DEPLOYMENT IDENTIFIER - REVISION 00077 DEBUG
        self.deployment_id = "REV_00077_DEBUG_2025_10_07"
        
        # Load configuration first
        self._load_configuration()
        
        # Telegram configuration - Rev 00248: Load from env vars first, then Secret Manager in production
        import os
        environment = os.getenv('ENVIRONMENT', 'production').lower()
        
        # Try environment variables first (Cloud Run mounts secrets as env vars)
        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        
        # If not found in env vars and in production, try Secret Manager
        if (not self.telegram_bot_token or not self.telegram_chat_id) and environment == 'production':
            log.info("📝 Telegram credentials not in env vars, loading from Secret Manager...")
            self._load_telegram_credentials_from_secret_manager()
        
        # Fallback to config_loader if still not found
        if not self.telegram_bot_token:
            self.telegram_bot_token = get_config_value('TELEGRAM_BOT_TOKEN', '')
        if not self.telegram_chat_id:
            self.telegram_chat_id = get_config_value('TELEGRAM_CHAT_ID', '')
        
        if self.telegram_bot_token and self.telegram_chat_id:
            log.info(f"✅ Telegram credentials loaded (token: {self.telegram_bot_token[:10]}..., chat_id: {self.telegram_chat_id})")
        else:
            log.warning(f"⚠️ Telegram credentials not found (token: {'SET' if self.telegram_bot_token else 'MISSING'}, chat_id: {'SET' if self.telegram_chat_id else 'MISSING'})")
        telegram_config = get_config_value('TELEGRAM_ALERTS_ENABLED', 'true')
        self.telegram_enabled = str(telegram_config).lower() == 'true' if isinstance(telegram_config, (str, bool)) else True
        
        # Alert configuration
        def safe_bool_config(key: str, default: str = 'true') -> bool:
            config_val = get_config_value(key, default)
            return str(config_val).lower() == 'true' if isinstance(config_val, (str, bool)) else True
        
        self.alert_levels_enabled = {
            AlertLevel.INFO: safe_bool_config('ALERT_LEVEL_INFO', 'true'),
            AlertLevel.WARNING: safe_bool_config('ALERT_LEVEL_WARNING', 'true'),
            AlertLevel.ERROR: safe_bool_config('ALERT_LEVEL_ERROR', 'true'),
            AlertLevel.CRITICAL: safe_bool_config('ALERT_LEVEL_CRITICAL', 'true'),
            AlertLevel.SUCCESS: safe_bool_config('ALERT_LEVEL_SUCCESS', 'true')
        }
        
        # Throttling / outbound pacing (never drop fill/reject — serialize + space sends)
        self.max_alerts_per_minute = int(get_config_value('TELEGRAM_MAX_MESSAGES_PER_MINUTE', '30'))
        self.alert_cooldown_seconds = int(get_config_value('ALERT_COOLDOWN_SECONDS', '30'))
        try:
            self.telegram_min_interval_sec = float(
                get_config_value('TELEGRAM_MIN_INTERVAL_SEC', '1.05') or 1.05
            )
        except (TypeError, ValueError):
            self.telegram_min_interval_sec = 1.05
        try:
            self.telegram_send_max_retries = int(
                get_config_value('TELEGRAM_SEND_MAX_RETRIES', '8') or 8
            )
        except (TypeError, ValueError):
            self.telegram_send_max_retries = 8
        self._telegram_send_lock: Optional[asyncio.Lock] = None
        self._telegram_last_send_monotonic: float = 0.0
        self._telegram_outbound_queue: Optional[asyncio.PriorityQueue] = None
        self._telegram_outbound_worker: Optional[asyncio.Task] = None
        self._telegram_outbound_seq: int = 0
        
        # Alert tracking
        self.alert_history = deque(maxlen=1000)
        self.alert_counts = defaultdict(int)
        self.last_alert_time = defaultdict(float)
        self._orb_0dte_exit_alert_trade_ids: Set[str] = set()
        self._trendline_exit_alert_trade_ids: Set[str] = set()
        self._breakout_exit_alert_trade_ids: Set[str] = set()
        
        # Performance tracking
        self.daily_performance = {}
        self.trade_history = []

        # Pipeline alert behavior toggles (Rev 00173)
        # ARCHIVED: Multi-strategy alerts no longer used - ORB strategy only
        per_symbol_cfg = get_config_value('MULTI_STRATEGY_PER_SYMBOL_ALERTS', 'false')
        self.multi_strategy_per_symbol_enabled = str(per_symbol_cfg).lower() == 'true'
        log.info(f"🔧 ORB Strategy Active (Rev 00173) - Legacy multi-strategy alerts: {'ENABLED' if self.multi_strategy_per_symbol_enabled else 'DISABLED (archived)'}")
        log.info(f"🚀 DEPLOYMENT ID: {self.deployment_id}")
        
        # End of day scheduling
        self.scheduler_thread = None
        self.scheduler_running = False
        
        # OAuth configuration
        oauth_config = get_config_value('OAUTH_ALERTS_ENABLED', 'true')
        self.oauth_enabled = str(oauth_config).lower() == 'true' if isinstance(oauth_config, (str, bool)) else True
        self.oauth_renewal_url = get_config_value('OAUTH_RENEWAL_URL', 'https://easy-trading-oauth-v2.web.app')
        self.oauth_morning_hour = int(get_config_value('OAUTH_MORNING_HOUR', '21'))
        self.oauth_morning_minute = int(get_config_value('OAUTH_MORNING_MINUTE', '0'))
        self.oauth_market_open_hour = int(get_config_value('OAUTH_MARKET_OPEN_HOUR', '5'))
        self.oauth_market_open_minute = int(get_config_value('OAUTH_MARKET_OPEN_MINUTE', '30'))
        self.oauth_timezone = get_config_value('OAUTH_TIMEZONE', 'America/New_York')
        
        # OAuth tracking
        self.oauth_status = {
            'prod': {'last_renewed': None, 'is_valid': False, 'expires_at': None},
        }
        
        # System status
        self.is_initialized = False
        
        log.info(f"Prime Alert Manager v{self.version} initialized")
    
    def _load_configuration(self):
        """Load configuration from .env files"""
        try:
            from .config_loader import load_configuration, get_config_value as config_get_value
            import os
            # Determine environment from ENVIRONMENT env var or default to production in cloud
            environment = os.getenv('ENVIRONMENT', 'production').lower()
            if environment == 'production':
                load_configuration('standard', 'demo', 'production')
            else:
                load_configuration('standard', 'demo', 'development')
            log.info(f"✅ Configuration loaded successfully (environment: {environment})")
        except Exception as e:
            log.warning(f"⚠️ Could not load configuration: {e}")
            # Continue without configuration loading
    
    def _load_telegram_credentials_from_secret_manager(self):
        """Load Telegram credentials from Google Secret Manager (Rev 00248)"""
        try:
            from google.cloud import secretmanager
            from .config_loader import get_cloud_config
            
            client = secretmanager.SecretManagerServiceClient()
            cloud_config = get_cloud_config()
            project_id = cloud_config["project_id"]
            
            # Load Telegram bot token
            bot_token_secret = f"projects/{project_id}/secrets/telegram-bot-token/versions/latest"
            bot_token_response = client.access_secret_version(request={"name": bot_token_secret})
            bot_token = bot_token_response.payload.data.decode("UTF-8").strip()
            
            # Load Telegram chat ID
            chat_id_secret = f"projects/{project_id}/secrets/telegram-chat-id/versions/latest"
            chat_id_response = client.access_secret_version(request={"name": chat_id_secret})
            chat_id = chat_id_response.payload.data.decode("UTF-8").strip()
            
            if bot_token and chat_id:
                self.telegram_bot_token = bot_token
                self.telegram_chat_id = chat_id
                log.info(f"✅ Loaded Telegram credentials from Secret Manager (token: {bot_token[:10]}..., chat_id: {chat_id})")
            else:
                log.warning("⚠️ Telegram credentials from Secret Manager are empty")
                
        except Exception as e:
            log.error(f"❌ Failed to load Telegram credentials from Secret Manager: {e}")
            # Continue without Secret Manager credentials
    
    async def initialize(self) -> bool:
        """Initialize the alert manager"""
        try:
            if self.telegram_enabled:
                # Test Telegram connection
                if await self._test_telegram_connection():
                    log.info("✅ Telegram connection successful")
                else:
                    log.warning("⚠️ Telegram connection failed")
                    self.telegram_enabled = False
            
            self.is_initialized = True
            log.info("Prime Alert Manager initialized successfully")
            return True
            
        except Exception as e:
            log.error(f"Failed to initialize alert manager: {e}")
            return False
    
    async def _test_telegram_connection(self) -> bool:
        """Test Telegram bot connection"""
        try:
            if not self.telegram_bot_token or not self.telegram_chat_id:
                return False
            
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/getMe"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                bot_info = response.json()
                if bot_info.get('ok'):
                    log.info(f"Telegram bot connected: @{bot_info['result']['username']}")
                    return True
            
            return False
            
        except Exception as e:
            log.error(f"Telegram connection test failed: {e}")
            return False
    
    # ============================================================================================================================================
    # TRADE ALERTS
    # ============================================================================================================================================
    
    async def send_trade_entry_alert(self, trade_data: TradeAlert) -> bool:
        """Send trade entry alert"""
        try:
            alert = Alert(
                alert_id=f"entry_{trade_data.symbol}_{int(time.time())}",
                alert_type=AlertType.TRADE_ENTRY,
                level=AlertLevel.SUCCESS,
                title="✅ Trade Executed",
                message=self._format_trade_entry_message(trade_data),
                symbol=trade_data.symbol,
                strategy=trade_data.strategy,
                confidence=trade_data.confidence,
                expected_return=trade_data.expected_return,
                metadata={
                    'action': trade_data.action,
                    'price': trade_data.price,
                    'quantity': trade_data.quantity,
                    'stop_loss': trade_data.stop_loss,
                    'take_profit': trade_data.take_profit
                }
            )
            
            return await self._send_alert(alert)
            
        except Exception as e:
            log.error(f"Failed to send trade entry alert: {e}")
            return False
    
    async def send_trade_exit_alert(self, symbol: str, side: str, quantity: int, 
                                    entry_price: float, exit_price: float, 
                                    pnl_dollars: float, pnl_percent: float,
                                    exit_reason: str, holding_time_minutes: int = 0,
                                    mode: str = "DEMO", trade_id: str = "") -> bool:
        """
        Send trade exit alert for position closure
        
        Args:
            symbol: Symbol that was traded
            side: "SELL" (closing position)
            quantity: Number of shares
            entry_price: Original entry price
            exit_price: Exit/sale price
            pnl_dollars: Profit/loss in dollars
            pnl_percent: Profit/loss percentage
            exit_reason: Reason for exit (TAKE_PROFIT, STEALTH_STOP, BREAKEVEN, etc.)
            holding_time_minutes: How long position was held
            mode: DEMO or LIVE
            trade_id: Original trade ID
        
        Returns:
            True if alert sent successfully
        """
        try:
            # Exit reason emoji and description (Rev 00180 - Complete mapping)
            exit_reasons = {
                # Stealth Trailing Exit Reasons
                'STOP_LOSS': ('🚨', 'Stop Loss Hit'),
                'stop_loss': ('🚨', 'Stop Loss Hit'),
                'ASSET_STOP': ('🚨', 'Asset Stop Loss Hit (−1.8% ORB/TL · −0.8% BO under/over entry)'),
                'price_stop': ('🚨', 'Asset Stop Loss Hit (−1.8% ORB/TL · −0.8% BO under/over entry)'),
                'asset_stop': ('🚨', 'Asset Stop Loss Hit (−1.8% ORB/TL · −0.8% BO under/over entry)'),
                'TAKE_PROFIT': ('🎯', 'Take Profit Target Reached'),
                'mfe_take_profit': ('🎯', 'Take Profit Target Reached'),
                'premium_trail': ('🛡️', 'Premium Trail (20pp after ARM)'),
                'prem_giveback': ('🛡️', 'Premium Trail (20pp after ARM)'),
                'moon_giveback': ('🛡️', 'Premium Trail (lock@200%)'),
                'UND_TRAIL': ('🛡️', 'Underlying Trail Exit (after +30% gate)'),
                'price_trail': ('🛡️', 'Underlying Trail Exit (after +30% gate)'),
                'und_trail': ('🛡️', 'Underlying Trail Exit (after +30% gate)'),
                'UND_TAKE_PROFIT': ('🎯', 'Underlying Take Profit (+1% favorable)'),
                'take_profit': ('🎯', 'Underlying Take Profit (+1% favorable)'),
                'und_take_profit': ('🎯', 'Underlying Take Profit (+1% favorable)'),
                'TRAILING_STOP': ('🛡️', 'Trailing Stop Hit'),
                'mfe_trailing_stop': ('🛡️', 'Trailing Stop Hit'),
                'trailing_stop': ('🛡️', 'Trailing Stop Hit'),
                'end_of_day_exit': ('🌇', 'End of Day Exit'),
                'no_data': ('⚠️', 'No Data Exit'),  # Oct 6 SHIP: label was missing
                'degraded_data': ('⚠️', 'Degraded Data Exit'),  # Oct 6 SHIP: label was missing
                'mfe_trail': ('🛡️', 'Afternoon MFE Trail (−10pp after 12:30)'),
                'immature_stress': ('📉', 'Immature Stress Exit'),
                'shallow_time_stop': ('⏱️', 'Shallow 15m / <7.5% MFE Stop'),
                'gb30_giveback': ('🛡️', '30–50% Giveback (−24pp)'),
                'gb32_timed_giveback': ('🛡️', '32–40% Timed Giveback (−15pp, high ≥120s)'),
                'deep_green_stop': ('📉', 'Deep Green Stop (10–30% MFE, bid ≤ −65%)'),
                'shallow_floor_stop': ('📉', 'Shallow Floor Stop (5–10% MFE, bid ≤ −18%)'),  # Oct 6 V3A SF
                'stall_floor_stop': ('🛡️', 'Stall Floor Stop (15–40% MFE, stalled high, bid ≤ +4%)'),  # Oct 6 V3A SBE v2-A
                'hwm_rtc': ('🛡️', 'Mature High-Water Retrace'),
                'catastrophic_premium_stop': ('🚨', 'Catastrophic Premium Stop (−80%)'),
                'shallow_proof_stop': ('📉', 'Shallow Proof Stop (10–20% MFE, bid ≤ −30%)'),
                'absorption_exit': ('🛡️', 'Absorption Exit (+2% weak expansion)'),
                'shallow_green_exit': ('🟢', 'CALL Shallow-Green Exit'),
                'contract_fade_exit': ('🛡️', 'Contract Fade Exit (off)'),
                'premium_trail_>50%_25trail': ('🛡️', 'Hybrid Premium Trail (50% / 25pp)'),
                'premium_trail_>30%_10trail': ('🛡️', 'Hybrid Premium Trail (30% / 10pp)'),
                'premium_spike_fail': ('🛡️', 'Premium Spike Fail (30–60% / 15pp)'),
                'underlying_mfe_capture': ('🛡️', 'Underlying-confirmed MFE Capture (175-250%)'),
                'guarded_retrace_exit': ('🛡️', 'Guarded UL Retrace Capture (30-200% MFE, UL off 0.5%, bid ≥ 65% of peak)'),  # Oct 22 GBUL
                'ul_giveback_ratio_exit': ('🛡️', 'UL Giveback Ratio Capture (30-200% MFE, UL gave back 30%, bid ≥ 70% of peak)'),  # Oct 22 UFR
                'atr_loss_cut': ('📉', 'ATR Loss Cut (bid ≤ −30%, UL 2 ATR against, below VWAP)'),  # Oct 27 STEP 29
                'vwap_cross_exit': ('🛡️', 'VWAP Cross Exit (50%+ peak, 0.25 ATR through VWAP, bid ≥ +10%)'),  # Oct 27 STEP 30
                'bounded_mfe_capture': ('🛡️', 'Bounded MFE Capture (290-305% / 10pp)'),
                'twenty_percent_save': ('🛡️', '20% Save'),
                'premium_stale_fail': ('🛡️', 'Premium Stale High Fail'),
                'runner_timed_capture': ('🛡️', '350% Runner Timed Capture'),
                'runner_profit_floor': ('🛡️', '350% Runner Profit Floor'),
                'runner_extended_capture': ('🛡️', 'Runner Extended Capture (R2X)'),  # Oct 6 R2X
                'runner_extension_floor': ('🛡️', 'Runner Extension Floor (R2X, bid < +50%)'),  # Oct 6 R2X
                'theta_exit': ('🧪', 'Theta Never-Green Exit (off)'),
                'eod_close': ('🌇', 'End of Day Exit'),
                'STEALTH_STOP': ('🛡️', 'Stealth Trailing Stop Hit'),
                'BREAKEVEN_PROTECTION': ('⚖️', 'Breakeven Protection Triggered'),
                'breakeven_protection': ('⚖️', 'Breakeven Protection Triggered'),
                'BREAKEVEN': ('⚖️', 'Breakeven Protection Triggered'),
                'TIME_EXIT': ('⏰', 'Profit Timeout or Max Holding Time'),  # Will be overridden below if TIME_EXIT
                'time_exit': ('⏰', 'Profit Timeout or Max Holding Time'),  # Will be overridden below if time_exit
                'TIME_LIMIT': ('⏰', 'Maximum Holding Time Reached'),
                'VOLUME_EXIT': ('📊', 'Low Volume Exit'),
                'volume_exit': ('📊', 'Low Volume Exit'),
                'VOLUME_DECLINE': ('📊', 'Volume Decline Exit'),
                'MOMENTUM_EXIT': ('📉', 'RSI Momentum Loss'),
                'momentum_exit': ('📉', 'RSI Momentum Loss'),
                'RSI_MOMENTUM': ('📉', 'RSI Momentum Loss'),
                'GAP_RISK': ('⚠️', 'Gap Risk Detected'),
                'gap_risk': ('⚠️', 'Gap Risk Detected'),
                'SCALE_OUT_T1': ('💰', 'Partial Profit T1 (+3%)'),
                'scale_out_t1': ('💰', 'Partial Profit T1 (+3%)'),
                'SCALE_OUT_T2': ('💰', 'Partial Profit T2 (+7%)'),
                'scale_out_t2': ('💰', 'Partial Profit T2 (+7%)'),
                'REBALANCE': ('🔄', 'Adaptive Rebalancing'),
                'rebalance': ('🔄', 'Adaptive Rebalancing'),
                'SO_ALLOWLIST_VIOLATION': ('🚫', 'Not on SO signal collection list'),
                'so_allowlist_violation': ('🚫', 'Not on SO signal collection list'),
            }
            
            reason_emoji, reason_desc = exit_reasons.get(exit_reason, ('🔚', exit_reason))
            
            # Rev 00171: Distinguish between Profit Timeout and Max Holding Time for TIME_EXIT
            # Profit Timeout: >= 2.5 hours (150 min) AND >= +0.1% profit
            # Max Holding Time: >= 4.0 hours (240 min) with no protection
            if exit_reason in ('TIME_EXIT', 'time_exit'):
                if holding_time_minutes >= 240:  # 4.0 hours - Max Holding Time
                    reason_emoji = '⏰'
                    reason_desc = 'Max Holding Time Reached'
                elif holding_time_minutes >= 150 and pnl_percent >= 0.001:  # 2.5 hours AND +0.1% profit - Profit Timeout
                    reason_emoji = '⏰'
                    reason_desc = 'Profit Timeout'
                else:
                    # Fallback: Default to Max Holding Time (shouldn't happen, but safety)
                    reason_emoji = '⏰'
                    reason_desc = 'Max Holding Time Reached'
            
            # P&L emoji and strings (% and $ must use the same sign for losses)
            pnl_emoji = "💰" if pnl_percent >= 0 else "📉"
            pnl_pct_str = _fmt_pct_suffix(pnl_percent)
            pnl_dollars_str = _fmt_usd_signed(pnl_dollars)

            # Calculate holding time display
            if holding_time_minutes > 60:
                hours = holding_time_minutes // 60
                minutes = holding_time_minutes % 60
                holding_display = f"{hours}h {minutes}m"
            else:
                holding_display = f"{holding_time_minutes}m"
            
            # Calculate trade value
            exit_value = quantity * exit_price
            
            sym_esc = _telegram_html_escape(symbol)
            tid_esc = _telegram_html_escape(_trade_id_text(trade_id)) if str(trade_id or "").strip() else ""
            reason_esc = _telegram_html_escape(reason_desc)
            tid_block = f"\n          <b>Trade ID:</b> {tid_esc}" if tid_esc else ""

            # Rev 00180AE: Build message in updated format with bold (Telegram HTML)
            message = f"""====================================================================

📉 <b>POSITION CLOSED</b>
{_alert_mode_header_line(mode)}

1) 🔰 <b>{pnl_pct_str}</b> {pnl_dollars_str}
          {quantity} {sym_esc} @ {_fmt_usd(entry_price)} · {_fmt_usd(entry_price * quantity)}
          <b>Entry:</b> {_fmt_usd(entry_price)} · <b>Exit:</b> {_fmt_usd(exit_price)}
          <b>Reason:</b> {reason_esc}

          <b>Holding time:</b> {holding_display}{tid_block}

📊 Position closed by Stealth Trailing System
"""
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                log.info(f"Trade exit alert sent for {symbol} - {reason_desc} - P&L: {pnl_pct_str} {pnl_dollars_str}")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending trade exit alert: {e}")
            return False
    
    async def send_aggregated_exit_alert(self, closed_positions: List[Dict[str, Any]], 
                                        exit_reason: str, mode: str = "DEMO") -> bool:
        """
        Send aggregated alert for multiple position closes (Rev 00076)
        
        Used for: EOD close, emergency exits, weak day exits
        
        Args:
            closed_positions: List of dicts with position data
            exit_reason: Common exit reason
            mode: DEMO or LIVE
        
        Returns:
            True if alert sent successfully
        """
        try:
            if not closed_positions:
                return False
            
            # Build message header
            message = f"""====================================================================

📉 <b>POSITIONS CLOSED</b>
{_alert_mode_header_line(mode)}

"""
            
            # Add each position
            for i, pos in enumerate(closed_positions, 1):
                pnl_pct = pos['pnl_pct']
                pnl_dollars = pos['pnl']
                
                # P&L emoji and sign
                if pnl_pct > 0:
                    pnl_emoji = "💰"
                    pnl_sign = "+"
                else:
                    pnl_emoji = "📉"
                    pnl_sign = "-"
                
                # Calculate holding time display
                holding_minutes = pos.get('holding_time', 0)
                if holding_minutes > 60:
                    hours = holding_minutes // 60
                    minutes = holding_minutes % 60
                    holding_display = f"{hours}h {minutes}m"
                else:
                    holding_display = f"{holding_minutes}m"
                
                pnl_pct_str = _fmt_pct_suffix(pnl_pct)
                pnl_dollars_str = _fmt_usd_signed(pnl_dollars)
                
                message += f"""{i}) {pnl_emoji} <b>{pnl_pct_str}</b> {pnl_dollars_str}
          {pos['quantity']} {_telegram_html_escape(str(pos['symbol']))} @ {_fmt_usd(pos['entry_price'])} · {_fmt_usd(pos['quantity'] * pos['entry_price'])}
          <b>Entry:</b> {_fmt_usd(pos['entry_price'])} · <b>Exit:</b> {_fmt_usd(pos['exit_price'])}
          <b>Reason:</b> {_telegram_html_escape(str(exit_reason))}

          <b>Holding time:</b> {holding_display}
          <b>Trade ID:</b> {_telegram_html_escape(_trade_id_text(pos.get('trade_id', 'N/A')))}

"""
            
            message += f"📊 Positions closed by Stealth Trailing System"
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                log.info(f"Aggregated exit alert sent for {len(closed_positions)} positions - {exit_reason}")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending aggregated exit alert: {e}")
            return False
    
    async def send_end_of_day_report(self, daily_stats: Dict[str, Any], weekly_stats: Dict[str, Any], 
                                    active_positions: int = 0, mode: str = "DEMO", 
                                    account_balance: float = 0.0, starting_balance: float = 1000.0,
                                    all_time_stats: Optional[Dict[str, Any]] = None) -> bool:
        """
        Send unified End of Day report for both Demo and Live modes
        
        Args:
            daily_stats: Today's trading statistics
            weekly_stats: This week's trading statistics  
            active_positions: Number of currently open positions
            mode: "DEMO" or "LIVE"
            account_balance: Current account balance (for % calculation)
            starting_balance: Starting balance for % calculation
        """
        try:
            # Calculate stats
            daily_closed = daily_stats.get('positions_closed', 0)
            daily_wins = daily_stats.get('winning_trades', 0)
            daily_losses = daily_stats.get('losing_trades', 0)
            daily_pnl = daily_stats.get('total_pnl', 0.0)
            daily_best = daily_stats.get('best_trade', 0.0)
            daily_worst = daily_stats.get('worst_trade', 0.0)
            
            weekly_closed = weekly_stats.get('positions_closed', 0)
            weekly_wins = weekly_stats.get('winning_trades', 0)
            weekly_losses = weekly_stats.get('losing_trades', 0)
            weekly_pnl = weekly_stats.get('total_pnl', 0.0)
            
            # Calculate rates
            daily_win_rate = (daily_wins / daily_closed * 100) if daily_closed > 0 else 0.0
            weekly_win_rate = (weekly_wins / weekly_closed * 100) if weekly_closed > 0 else 0.0
            
            # Calculate P&L percentages (today/week vs period-start balance; all-time vs seed)
            from .eod_report_helpers import (
                all_time_pnl_pct as _all_time_pnl_pct,
                period_pnl_pct as _period_pnl_pct,
                resolve_eod_account_balance as _resolve_eod_account_balance,
            )

            _all_time_pnl_for_balance = float((all_time_stats or {}).get("total_pnl", 0.0) or 0.0)
            _eod_balance = _resolve_eod_account_balance(
                account_balance, starting_balance, _all_time_pnl_for_balance
            )
            daily_pnl_pct = _period_pnl_pct(daily_pnl, _eod_balance, fallback_base=starting_balance)
            weekly_pnl_pct = _period_pnl_pct(weekly_pnl, _eod_balance, fallback_base=starting_balance)
            
            # Format P&L
            daily_pnl_sign = "+" if daily_pnl >= 0 else ""
            weekly_pnl_sign = "+" if weekly_pnl >= 0 else ""
            
            # Rev 00135: Calculate profit factors from actual trade data (passed in stats)
            # Profit factor = sum of all wins / sum of all losses (absolute values)
            
            # Daily profit factor: Use actual wins/losses sums if available
            daily_total_wins = daily_stats.get('total_wins_sum', None)
            daily_total_losses = daily_stats.get('total_losses_sum', None)
            
            # If not provided, calculate from stats (fallback)
            if daily_total_wins is None or daily_total_losses is None:
                if daily_wins > 0 and daily_losses > 0:
                    # Estimate from average win/loss
                    avg_win = (daily_pnl / daily_wins) if daily_wins > 0 else 0.0
                    avg_loss = abs(daily_stats.get('worst_trade', 0.0)) if daily_losses > 0 and daily_stats.get('worst_trade', 0.0) < 0 else 0.0
                    if avg_loss == 0.0 and daily_losses > 0:
                        estimated_wins_sum = avg_win * daily_wins
                        avg_loss = abs((daily_pnl - estimated_wins_sum) / daily_losses) if daily_losses > 0 else 0.0
                    daily_total_wins = avg_win * daily_wins if daily_wins > 0 else 0.0
                    daily_total_losses = avg_loss * daily_losses if daily_losses > 0 else 0.0
                elif daily_wins > 0:
                    daily_total_wins = daily_pnl
                    daily_total_losses = 0.0
                elif daily_losses > 0:
                    daily_total_wins = 0.0
                    daily_total_losses = abs(daily_pnl)
                else:
                    daily_total_wins = 0.0
                    daily_total_losses = 0.0
            
            profit_factor = (daily_total_wins / daily_total_losses) if daily_total_losses > 0 else (daily_total_wins if daily_total_wins > 0 else (float('inf') if daily_total_wins > 0 and daily_total_losses == 0 else 0.0))
            
            # Weekly profit factor: Use actual wins/losses sums if available
            weekly_total_wins = weekly_stats.get('total_wins_sum', None)
            weekly_total_losses = weekly_stats.get('total_losses_sum', None)
            
            # If not provided, calculate from stats (fallback)
            if weekly_total_wins is None or weekly_total_losses is None:
                if weekly_wins > 0 and weekly_losses > 0:
                    weekly_avg_win = (weekly_pnl / weekly_wins) if weekly_wins > 0 else 0.0
                    estimated_wins_sum = weekly_avg_win * weekly_wins
                    weekly_avg_loss = abs((weekly_pnl - estimated_wins_sum) / weekly_losses) if weekly_losses > 0 else 0.0
                    weekly_total_wins = weekly_avg_win * weekly_wins
                    weekly_total_losses = weekly_avg_loss * weekly_losses
                elif weekly_wins > 0:
                    weekly_total_wins = weekly_pnl
                    weekly_total_losses = 0.0
                elif weekly_losses > 0:
                    weekly_total_wins = 0.0
                    weekly_total_losses = abs(weekly_pnl)
                else:
                    weekly_total_wins = 0.0
                    weekly_total_losses = 0.0
            
            weekly_profit_factor = (weekly_total_wins / weekly_total_losses) if weekly_total_losses > 0 else (weekly_total_wins if weekly_total_wins > 0 else (float('inf') if weekly_total_wins > 0 and weekly_total_losses == 0 else 0.0))
            
            # All-time stats (Rev 00135)
            all_time_total_trades = 0
            all_time_wins = 0
            all_time_losses = 0
            all_time_pnl = 0.0
            all_time_profit_factor = 0.0
            all_time_pnl_pct = 0.0
            
            if all_time_stats:
                all_time_total_trades = all_time_stats.get('total_trades', 0)
                all_time_wins = all_time_stats.get('winning_trades', 0)
                all_time_losses = all_time_stats.get('losing_trades', 0)
                all_time_pnl = all_time_stats.get('total_pnl', 0.0)
                
                # Rev 00135: Calculate all-time profit factor from actual trade data
                if all_time_total_trades > 0:
                    # Use actual wins/losses sums if available (more accurate)
                    all_time_wins_sum = all_time_stats.get('total_wins_sum', 0.0)
                    all_time_losses_sum = all_time_stats.get('total_losses_sum', 0.0)
                    
                    if all_time_wins_sum > 0 and all_time_losses_sum > 0:
                        # Have both wins and losses - use actual sums
                        all_time_profit_factor = all_time_wins_sum / all_time_losses_sum
                    elif all_time_wins_sum > 0 and all_time_losses_sum == 0:
                        # All wins - infinite profit factor
                        all_time_profit_factor = float('inf')
                    elif all_time_wins_sum == 0 and all_time_losses_sum > 0:
                        # All losses
                        all_time_profit_factor = 0.0
                    else:
                        # Fallback to estimation method
                        if all_time_wins > 0 and all_time_losses > 0:
                            all_time_avg_win = (all_time_pnl / all_time_wins) if all_time_wins > 0 else 0.0
                            estimated_wins_sum = all_time_avg_win * all_time_wins
                            all_time_avg_loss = abs((all_time_pnl - estimated_wins_sum) / all_time_losses) if all_time_losses > 0 else 0.0
                            all_time_total_wins_sum = all_time_avg_win * all_time_wins
                            all_time_total_losses_sum = all_time_avg_loss * all_time_losses
                            all_time_profit_factor = (all_time_total_wins_sum / all_time_total_losses_sum) if all_time_total_losses_sum > 0 else 0.0
                        elif all_time_wins > 0:
                            all_time_profit_factor = float('inf')
                        else:
                            all_time_profit_factor = 0.0
                
                # Calculate all-time P&L percentage (lifetime vs original starting balance)
                all_time_pnl_pct = _all_time_pnl_pct(all_time_pnl, starting_balance)
            
            all_time_win_rate = (all_time_wins / all_time_total_trades * 100) if all_time_total_trades > 0 else 0.0
            all_time_pnl_sign = "+" if all_time_pnl >= 0 else ""
            
            # Rev 00183: Calculate worst trade sign for formatting
            daily_worst_sign = "-" if daily_worst < 0 else "+"
            
            # Rev 00183: Calculate actual account balance (starting + total P&L)
            actual_account_balance = starting_balance + all_time_pnl
            
            # Get report timestamps
            from zoneinfo import ZoneInfo
            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
            now_et = datetime.now(ZoneInfo("America/New_York"))
            pt_time = now_pt.strftime('%I:%M %p PT')
            et_time = now_et.strftime('%I:%M %p ET')
            report_date = datetime.utcnow().strftime('%Y-%m-%d')  # Rev 00075: UTC consistency
            
            # Rev 00135: Calculate averages for display
            # Rev 00183: Fix average calculations - use actual wins/losses sums
            avg_win = (daily_total_wins / daily_wins) if daily_wins > 0 else 0.0
            avg_loss = -(daily_total_losses / daily_losses) if daily_losses > 0 else 0.0  # Rev 00183: Negative for losses
            
            # Format profit factors (handle infinity)
            daily_pf_str = _fmt_profit_factor(profit_factor)
            weekly_pf_str = _fmt_profit_factor(weekly_profit_factor)
            all_time_pf_str = _fmt_profit_factor(all_time_profit_factor)
            
            # Telegram HTML (parse_mode=HTML via _send_telegram_message)
            message = f"""====================================================================

🛂 <b>END-OF-DAY REPORT</b>
{_alert_mode_header_line(mode)}

📈 <b>P&L (TODAY):</b>
          <b>{_fmt_pct(daily_pnl_pct)}% {_fmt_usd_signed(daily_pnl)}</b>
          Win Rate: {daily_win_rate:.1f}% • Total Trades: {daily_closed}
          Wins: {daily_wins} • Losses: {daily_losses}
          Profit Factor: {daily_pf_str}
          Average Win: {_fmt_usd(avg_win)}
          Average Loss: {_fmt_usd_signed(avg_loss)}
          Best Trade: {_fmt_usd_signed(daily_best)}
          Worst Trade: {_fmt_usd_signed(daily_worst)}

🎖️⭐ <b>P&L (WEEK M-F):</b>
          <b>{_fmt_pct(weekly_pnl_pct)}% {_fmt_usd_signed(weekly_pnl)}</b>
          Win Rate: {weekly_win_rate:.1f}% • Total Trades: {weekly_closed}
          Profit Factor: {weekly_pf_str}

💎 <b>Account Balances (All Time):</b>
          <b>{_fmt_pct(all_time_pnl_pct)}% {_fmt_usd_signed(all_time_pnl)}</b>
          <b>{_fmt_usd(actual_account_balance)}</b>
          Win Rate: {all_time_win_rate:.1f}% • Total Trades: {all_time_total_trades}
          Profit Factor: {all_time_pf_str}
          Wins: {all_time_wins} • Losses: {all_time_losses}

📅 <b>Report Date:</b> {report_date}
          Time: {pt_time} ({et_time})
"""
            
            # Send via Telegram
            success = await self._send_telegram_message(message, AlertLevel.INFO)
            
            if success:
                log.info(f"{mode} Mode EOD Report sent - Daily: {daily_pnl_sign}${daily_pnl:.2f} ({daily_pnl_sign}{daily_pnl_pct:.2f}%), Weekly: {weekly_pnl_sign}${weekly_pnl:.2f} ({weekly_pnl_sign}{weekly_pnl_pct:.2f}%)")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending EOD report: {e}")
            return False
    
    # ============================================================================================================================================
    # PERFORMANCE ALERTS
    # ============================================================================================================================================
    
    async def send_system_alert(self, title: str, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        """Send system alert"""
        try:
            alert = Alert(
                alert_id=f"system_{int(time.time())}",
                alert_type=AlertType.SYSTEM_STATUS,
                level=level,
                title=title,
                message=message,
                metadata={'timestamp': datetime.now().isoformat()}
            )
            
            return await self._send_alert(alert)
            
        except Exception as e:
            log.error(f"Failed to send system alert: {e}")
            return False
    
    # ============================================================================================================================================
    # CORE ALERT SENDING
    # ============================================================================================================================================
    
    async def _send_alert(self, alert: Alert) -> bool:
        """Send alert via configured channels"""
        try:
            # Check if alert level is enabled
            if not self.alert_levels_enabled.get(alert.level, False):
                return False

            # Do not drop on flood — paced delivery lives in _send_telegram_message.
            if self.telegram_enabled:
                success = await self._send_telegram_alert(alert)
                if success:
                    self._track_alert(alert)
                    return True

            # Log alert if no other channels available
            log.info(f"Alert: {alert.title} - {alert.message}")
            self._track_alert(alert)
            return True

        except Exception as e:
            log.error(f"Failed to send alert: {e}")
            return False

    async def _send_telegram_alert(self, alert: Alert) -> bool:
        """Send alert via Telegram (same banner + HTML pipeline as _send_telegram_message)."""
        try:
            if not self.telegram_bot_token or not self.telegram_chat_id:
                return False
            return await self._send_telegram_message(self._format_telegram_message(alert), alert.level)
        except Exception as e:
            log.error(f"Failed to send Telegram alert: {e}")
            return False

    def _ensure_telegram_send_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_telegram_send_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._telegram_send_lock = lock
        return lock

    def _ensure_telegram_outbound_queue(self) -> asyncio.PriorityQueue:
        q = getattr(self, "_telegram_outbound_queue", None)
        if q is None:
            q = asyncio.PriorityQueue()
            self._telegram_outbound_queue = q
            self._telegram_outbound_seq = 0
            worker = getattr(self, "_telegram_outbound_worker", None)
            if worker is None or worker.done():
                self._telegram_outbound_worker = asyncio.create_task(
                    self._telegram_outbound_worker_loop(),
                    name="telegram_outbound_worker",
                )
        return q

    async def _telegram_outbound_worker_loop(self) -> None:
        """Single worker: priority queue + paced delivery (fills ahead of rejects)."""
        q = self._ensure_telegram_outbound_queue()
        while True:
            job: _TelegramOutboundJob = await q.get()
            try:
                ok = await self._deliver_telegram_message_paced(job.message)
                if not job.future.done():
                    job.future.set_result(bool(ok))
            except Exception as worker_exc:
                log.error("TELEGRAM_SEND | action=worker_error | error=%s", worker_exc)
                if not job.future.done():
                    job.future.set_result(False)
            finally:
                try:
                    q.task_done()
                except Exception:
                    pass

    def _telegram_min_send_interval_sec(self) -> float:
        """Effective same-chat spacing: max(configured floor, 60/max_per_minute)."""
        try:
            configured = float(getattr(self, "telegram_min_interval_sec", 1.05) or 1.05)
        except (TypeError, ValueError):
            configured = 1.05
        try:
            per_min = int(getattr(self, "max_alerts_per_minute", 30) or 30)
        except (TypeError, ValueError):
            per_min = 30
        paced = 60.0 / max(1, per_min)
        return max(0.05, configured, paced)

    @staticmethod
    def _telegram_retry_after_seconds(response: Any) -> Optional[float]:
        """Parse Telegram 429 retry_after from JSON body or Retry-After header."""
        try:
            data = response.json() if response is not None else {}
        except Exception:
            data = {}
        if isinstance(data, dict):
            params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
            ra = params.get("retry_after")
            if ra is not None:
                try:
                    return max(0.05, float(ra))
                except (TypeError, ValueError):
                    pass
            desc = str(data.get("description") or "")
            m = re.search(r"retry after (\d+(?:\.\d+)?)", desc, re.I)
            if m:
                try:
                    return max(0.05, float(m.group(1)))
                except (TypeError, ValueError):
                    pass
        try:
            header = (response.headers or {}).get("Retry-After") if response is not None else None
            if header is not None:
                return max(0.05, float(header))
        except Exception:
            pass
        return None

    async def _telegram_pace_before_send(self) -> None:
        """Wait so consecutive Telegram posts stay under the chat flood ceiling."""
        interval = self._telegram_min_send_interval_sec()
        last = float(getattr(self, "_telegram_last_send_monotonic", 0.0) or 0.0)
        if last <= 0.0:
            return
        wait = interval - (time.monotonic() - last)
        if wait > 0.0:
            log.info(
                "TELEGRAM_SEND | action=pace_wait | wait_sec=%.2f | min_interval_sec=%.2f",
                wait,
                interval,
            )
            await asyncio.sleep(wait)

    def _deliver_telegram_message_once(self, message: str) -> Tuple[bool, Optional[float]]:
        """One HTTP attempt (HTML, then plain on 400). Returns (ok, retry_after_sec)."""
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        data = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(url, json=data, timeout=10)
        except Exception as exc:
            log.error("Telegram request failed: %s", exc)
            return False, 2.0

        if response.status_code == 200:
            try:
                result = response.json()
            except Exception:
                result = {}
            if isinstance(result, dict) and result.get("ok"):
                return True, None
            error_desc = str((result or {}).get("description") or "Unknown error")
            log.error("Telegram API error: %s", error_desc)
            if "parse" in error_desc.lower() or "html" in error_desc.lower():
                return self._deliver_telegram_plain_fallback(url, message)
            return False, None

        if response.status_code == 429:
            retry_after = self._telegram_retry_after_seconds(response) or 3.0
            err_body = ""
            try:
                err_body = (response.text or "")[:300]
            except Exception:
                pass
            log.warning(
                "TELEGRAM_SEND | action=flood_control | http=429 | retry_after=%.1f | body=%s",
                retry_after,
                err_body,
            )
            return False, retry_after

        err_body = ""
        try:
            err_body = (response.text or "")[:300]
        except Exception:
            pass
        log.error("Telegram HTTP error: %s | body=%s", response.status_code, err_body)

        if response.status_code == 400:
            return self._deliver_telegram_plain_fallback(url, message)
        if response.status_code >= 500:
            return False, 2.0
        return False, None

    def _deliver_telegram_plain_fallback(self, url: str, message: str) -> Tuple[bool, Optional[float]]:
        """Retry without parse_mode when HTML is rejected."""
        log.warning("⚠️ HTML/parse issue — retrying Telegram send as plain text")
        plain_text = _telegram_fit_message(_telegram_plain_text_from_html(message))
        data_plain = {
            "chat_id": self.telegram_chat_id,
            "text": plain_text,
            "disable_web_page_preview": True,
        }
        try:
            response_plain = requests.post(url, json=data_plain, timeout=10)
        except Exception as exc:
            log.error("Telegram plain-text request failed: %s", exc)
            return False, 2.0
        if response_plain.status_code == 429:
            return False, self._telegram_retry_after_seconds(response_plain) or 3.0
        if response_plain.status_code == 200:
            try:
                result_plain = response_plain.json()
            except Exception:
                result_plain = {}
            if isinstance(result_plain, dict) and result_plain.get("ok"):
                log.info("✅ Telegram message sent successfully (plain text fallback)")
                return True, None
            log.error(
                "Telegram API error (plain text): %s",
                (result_plain or {}).get("description", "Unknown error"),
            )
            return False, None
        try:
            log.error(
                "Telegram plain-text fallback failed: %s | %s",
                response_plain.status_code,
                (response_plain.text or "")[:300],
            )
        except Exception:
            pass
        if response_plain.status_code >= 500:
            return False, 2.0
        return False, None

    async def _deliver_telegram_message_paced(self, message: str) -> bool:
        """Pace + retry one already-prepared Telegram payload (worker-only)."""
        max_retries = max(0, int(getattr(self, "telegram_send_max_retries", 8) or 8))
        lock = self._ensure_telegram_send_lock()
        async with lock:
            for attempt in range(max_retries + 1):
                await self._telegram_pace_before_send()
                try:
                    ok, retry_after = await asyncio.to_thread(
                        self._deliver_telegram_message_once, message
                    )
                except Exception as deliver_exc:
                    log.error("Telegram deliver failed: %s", deliver_exc)
                    ok, retry_after = False, 2.0
                self._telegram_last_send_monotonic = time.monotonic()
                if ok:
                    if attempt > 0:
                        log.warning(
                            "TELEGRAM_SEND | action=sent | attempt=%d | after_retry=true",
                            attempt + 1,
                        )
                    else:
                        log.debug("Telegram message sent successfully")
                    return True
                if retry_after is not None and attempt < max_retries:
                    sleep_for = max(0.05, float(retry_after))
                    log.warning(
                        "TELEGRAM_SEND | action=throttle_retry | attempt=%d/%d | "
                        "sleep_sec=%.1f",
                        attempt + 1,
                        max_retries + 1,
                        sleep_for,
                    )
                    await asyncio.sleep(sleep_for)
                    continue
                break
        log.error(
            "TELEGRAM_SEND | action=failed | attempts=%d | reason=exhausted_retries_or_fatal",
            max_retries + 1,
        )
        return False

    async def _send_telegram_message(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        *,
        priority: int = TELEGRAM_PRIORITY_NORMAL,
        wait: bool = True,
    ) -> bool:
        """Queue Telegram with same-chat pacing + 429 retries (never silently drop).

        A single outbound worker serializes delivery and spaces by
        TELEGRAM_MIN_INTERVAL_SEC / TELEGRAM_MAX_MESSAGES_PER_MINUTE.
        Fill alerts use priority=0 so they jump ahead of reject floods.
        When wait=False, returns True once queued (delivery continues in background).
        """
        _ = level
        try:
            # RESEARCH desk: inject RESEARCH APP banner on every Telegram payload.
            message = _prepare_research_app_telegram_message(message)
            if len(message) > TELEGRAM_SAFE_MESSAGE_LEN:
                log.warning(
                    "Telegram message truncated | chars_before=%d | chars_after=%d",
                    len(message),
                    TELEGRAM_SAFE_MESSAGE_LEN,
                )
                message = _telegram_fit_message(message)
            log.info(
                "🔍🔍🔍 _send_telegram_message CALLED - Message preview: %s...",
                message[:100],
            )

            if not self.telegram_bot_token or not self.telegram_chat_id:
                log.info("🔍🔍🔍 No Telegram credentials, returning False")
                return False

            q = self._ensure_telegram_outbound_queue()
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            seq = int(getattr(self, "_telegram_outbound_seq", 0) or 0)
            self._telegram_outbound_seq = seq + 1
            pri = max(0, int(priority))
            await q.put(
                _TelegramOutboundJob(
                    priority=pri,
                    seq=seq,
                    message=message,
                    future=fut,
                )
            )
            depth = q.qsize()
            log.info(
                "TELEGRAM_SEND | action=queued | priority=%d | depth=%d | wait=%s",
                pri,
                depth,
                str(bool(wait)).lower(),
            )
            if not wait:
                def _done(f: asyncio.Future) -> None:
                    try:
                        ok = bool(f.result())
                    except Exception:
                        ok = False
                    if not ok:
                        log.error(
                            "TELEGRAM_SEND | action=failed_async | priority=%d",
                            pri,
                        )

                fut.add_done_callback(_done)
                return True
            return bool(await fut)
        except Exception as e:
            log.error(f"Failed to send Telegram message: {e}")
            return False
    
    # ============================================================================================================================================
    # MESSAGE FORMATTING
    # ============================================================================================================================================
    
    def _format_telegram_message(self, alert: Alert) -> str:
        """Format alert for Telegram"""
        emoji_map = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "🚨",
            AlertLevel.CRITICAL: "🔥",
            AlertLevel.SUCCESS: "✅"
        }
        
        emoji = emoji_map.get(alert.level, "📢")
        timestamp = alert.timestamp.strftime("%H:%M:%S")
        
        title_esc = _telegram_html_escape(alert.title)
        message = f"{emoji} <b>{title_esc}</b>\n"
        message += f"🕐 {timestamp}\n\n"
        
        if alert.symbol:
            message += f"📈 <b>Symbol:</b> {_telegram_html_escape(alert.symbol)}\n"
        
        if alert.strategy:
            message += f"🎯 <b>Strategy:</b> {_telegram_html_escape(alert.strategy)}\n"
        
        if alert.confidence:
            message += f"🎲 <b>Confidence:</b> {alert.confidence:.1%}\n"
        
        if alert.expected_return:
            message += f"💰 <b>Expected return:</b> {alert.expected_return:.2%}\n"
        
        message += f"\n{alert.message}"
        
        return message
    
    def _format_trade_signal_message(self, signal_data: Dict[str, Any]) -> str:
        """Format trade signal message"""
        symbol = signal_data.get('symbol', 'Unknown')
        strategy = signal_data.get('strategy', 'Unknown')
        confidence = signal_data.get('confidence', 0)
        expected_return = signal_data.get('expected_return', 0)
        entry_price = signal_data.get('entry_price', 0)
        
        sym_esc = _telegram_html_escape(symbol)
        strat_esc = _telegram_html_escape(strategy)
        message = f"<b>{sym_esc}</b> · {strat_esc}\n"
        message += f"🎯 <b>Entry price:</b> {_fmt_usd(entry_price)}\n"
        message += f"🎲 <b>Confidence:</b> {confidence:.1%}\n"
        message += f"💰 <b>Expected return:</b> {expected_return:.2%}\n"
        
        if signal_data.get('stop_loss'):
            message += f"🛑 <b>Stop loss:</b> {_fmt_usd(signal_data['stop_loss'])}\n"
        
        if signal_data.get('take_profit'):
            message += f"🎯 <b>Take profit:</b> {_fmt_usd(signal_data['take_profit'])}\n"
        
        return message
    
    def _format_trade_entry_message(self, trade_data: TradeAlert) -> str:
        """Format trade entry message"""
        sym_e = _telegram_html_escape(trade_data.symbol)
        st_e = _telegram_html_escape(trade_data.strategy)
        act_e = _telegram_html_escape(trade_data.action)
        message = f"<b>{sym_e}</b> · {st_e}\n"
        message += f"✅ <b>{act_e}</b> {trade_data.quantity} shares @ {_fmt_usd(trade_data.price)}\n"
        message += f"🎲 <b>Confidence:</b> {trade_data.confidence:.1%}\n"
        message += f"💰 <b>Expected return:</b> {trade_data.expected_return:.2%}\n"
        
        if trade_data.stop_loss:
            message += f"🛑 <b>Stop loss:</b> {_fmt_usd(trade_data.stop_loss)}\n"
        
        if trade_data.take_profit:
            message += f"🎯 <b>Take profit:</b> {_fmt_usd(trade_data.take_profit)}\n"
        
        if trade_data.reason:
            message += f"📝 <b>Reason:</b> {_telegram_html_escape(trade_data.reason)}\n"
        
        return message
    
    def _format_trade_exit_message(self, trade_data: TradeAlert, pnl_pct: float) -> str:
        """Format trade exit message"""
        pnl_emoji = "💰" if pnl_pct > 0 else "📉"
        
        sym_x = _telegram_html_escape(trade_data.symbol)
        st_x = _telegram_html_escape(trade_data.strategy)
        act_x = _telegram_html_escape(trade_data.action)
        message = f"<b>{sym_x}</b> · {st_x}\n"
        message += f"🔚 <b>{act_x}</b> {trade_data.quantity} shares @ {_fmt_usd(trade_data.price)}\n"
        message += f"{pnl_emoji} <b>P/L:</b> {_fmt_pct_suffix(pnl_pct)}\n"
        
        if trade_data.reason:
            message += f"📝 <b>Reason:</b> {_telegram_html_escape(trade_data.reason)}\n"
        
        return message
    
    def _format_performance_message(self, performance_data: Dict[str, Any]) -> str:
        """Format performance update message"""
        message = f"📊 <b>Performance update</b>\n\n"
        
        if 'win_rate' in performance_data:
            message += f"🎯 <b>Win rate:</b> {performance_data['win_rate']:.1%}\n"
        
        if 'total_pnl' in performance_data:
            message += f"💰 <b>Total P/L:</b> {performance_data['total_pnl']:.2%}\n"
        
        if 'active_positions' in performance_data:
            message += f"📈 <b>Active positions:</b> {performance_data['active_positions']}\n"
        
        if 'daily_return' in performance_data:
            message += f"📅 <b>Daily return:</b> {performance_data['daily_return']:.2%}\n"
        
        return message
    
    # ARCHIVED (Rev 00047): Old formatting method for archived send_end_of_day_summary()
    async def _send_demo_eod_summary(self):
        """Send Demo Mode end-of-day summary (with GCS-based deduplication - Rev 00180AD)"""
        try:
            from datetime import date, timezone
            from typing import Optional
            from zoneinfo import ZoneInfo
            from .eod_report_helpers import pt_trading_date

            PT_TZ = ZoneInfo("America/Los_Angeles")

            def _so_trade_closed_date_pt(trade) -> Optional[date]:
                ts = getattr(trade, "exit_timestamp", None)
                if not ts:
                    return None
                if getattr(ts, "tzinfo", None) is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts.astimezone(PT_TZ).date()

            # Rev 00047: Enhanced validation and logging
            if not hasattr(self, "_mock_executor") or not self._mock_executor:
                try:
                    from .mock_trading_executor import MockTradingExecutor

                    self._mock_executor = MockTradingExecutor(alert_manager=self)
                    log.info(
                        "EOD_ORB_SO | cold-start mock executor loaded from GCS for EOD (%d closed)",
                        len(getattr(self._mock_executor, "closed_trades", None) or []),
                    )
                except Exception as cold_exc:
                    log.error("❌ CRITICAL: Could not load mock executor for EOD: %s", cold_exc)
                    return
            
            log.info(f"✅ Mock executor available for EOD (instance: {id(self._mock_executor)})")
            
            # DEDUPLICATION: Check if EOD report already sent today (Rev 00180AD - GCS-based)
            # This prevents duplicate reports across Cloud Run instances
            # Rev 00047: Date-based marker (internal scheduler disabled, so no blocking)
            # Ensures only ONE EOD report per day, even if Cloud Scheduler retries
            from .eod_report_helpers import pt_trading_date_iso, try_claim_eod_marker
            from .runtime_mode import alert_dedup_service_token

            today = pt_trading_date_iso()  # Rev 00362: PT trading day (desk session)
            eod_sent_key = f"eod_sent_{alert_dedup_service_token()}_{today}_unified"
            
            # Try GCS-based deduplication first (works across all instances)
            try:
                from .gcs_persistence import get_gcs_persistence
                gcs = get_gcs_persistence()
                
                if not try_claim_eod_marker(gcs, eod_sent_key):
                    log.info(f"✅ EOD report already sent today ({today}) - only ONE report per day, skipping duplicate")
                    return
                
                log.info(f"📝 Created GCS EOD marker for {today}")
                
            except Exception as gcs_error:
                log.warning(f"GCS deduplication failed (non-critical): {gcs_error}")
                # Fall back to class-level cache (works within same instance)
                if not hasattr(self.__class__, '_eod_sent_cache'):
                    self.__class__._eod_sent_cache = {}
                
                if eod_sent_key in self.__class__._eod_sent_cache:
                    log.info(f"EOD report already sent today ({today}) - skipping duplicate (class cache)")
                    return
                
                # Mark as sent in class cache
                self.__class__._eod_sent_cache[eod_sent_key] = datetime.utcnow()  # Rev 00075: UTC consistency
            
            # Rev 00180AE: Use unified EOD report function (centralized in alert_manager)
            # DIAGNOSTIC (Rev 00180AE): Check mock executor state before reading stats
            log.info(f"📊 EOD DIAGNOSTIC: Mock executor instance: {id(self._mock_executor)}")
            log.info(f"📊 EOD DIAGNOSTIC: Active trades: {len(self._mock_executor.active_trades)}")
            log.info(f"📊 EOD DIAGNOSTIC: Closed trades: {len(self._mock_executor.closed_trades)}")
            log.info(f"📊 EOD DIAGNOSTIC: Daily stats raw: {self._mock_executor.daily_stats}")
            log.info(f"📊 EOD DIAGNOSTIC: Weekly stats raw: {self._mock_executor.weekly_stats}")
            log.info(f"📊 EOD DIAGNOSTIC: Account balance: ${self._mock_executor.account_balance:,.2f}")
            
            # Rev 00153: Validate stats consistency before reading (early detection)
            today_pt = pt_trading_date()
            today_trades_count = sum(
                1
                for trade in self._mock_executor.closed_trades
                if _so_trade_closed_date_pt(trade) == today_pt
            )
            stats_count = self._mock_executor.daily_stats.get('positions_closed', 0)
            if today_trades_count > 0 and stats_count != today_trades_count:
                log.warning(f"⚠️ EOD VALIDATION: Discrepancy detected before report - stats: {stats_count}, actual: {today_trades_count}")
                log.warning(f"   Will be fixed in recovery logic below")
            
            # Rev 00135: Calculate profit factors from actual trade data
            today_pt = pt_trading_date()
            current_week_start = today_pt
            days_since_monday = current_week_start.weekday()
            monday = current_week_start - timedelta(days=days_since_monday)
            
            # Calculate daily profit factor from today's trades
            daily_total_wins = 0.0
            daily_total_losses = 0.0
            for trade in self._mock_executor.closed_trades:
                if _so_trade_closed_date_pt(trade) == today_pt:
                    if trade.pnl > 0:
                        daily_total_wins += trade.pnl
                    else:
                        daily_total_losses += abs(trade.pnl)
            
            # Calculate weekly profit factor from this week's trades
            weekly_total_wins = 0.0
            weekly_total_losses = 0.0
            weekly_trades = []  # Rev 00223: Collect all weekly trades for accurate calculation
            for trade in self._mock_executor.closed_trades:
                closed_date = _so_trade_closed_date_pt(trade)
                if closed_date and closed_date >= monday:
                    weekly_trades.append(trade)
                    if trade.pnl > 0:
                        weekly_total_wins += trade.pnl
                    else:
                        weekly_total_losses += abs(trade.pnl)
            
            # Get stats from mock executor
            daily_stats = {
                'positions_closed': self._mock_executor.daily_stats['positions_closed'],
                'winning_trades': self._mock_executor.daily_stats['winning_trades'],
                'losing_trades': self._mock_executor.daily_stats['losing_trades'],
                'total_pnl': self._mock_executor.daily_stats['total_pnl'],
                'best_trade': self._mock_executor.daily_stats['best_trade'],
                'worst_trade': self._mock_executor.daily_stats['worst_trade'],
                'best_symbol': 'N/A',  # TODO: Track symbols
                'worst_symbol': 'N/A',
                'total_wins_sum': daily_total_wins,  # Rev 00135: For accurate profit factor
                'total_losses_sum': daily_total_losses  # Rev 00135: For accurate profit factor
            }
            
            # Rev 00223: Calculate weekly stats from closed_trades (source of truth) instead of weekly_stats
            # This ensures weekly stats include ALL trades from the week, even after deployments
            weekly_total_pnl = sum(t.pnl for t in weekly_trades)
            weekly_winning_trades = sum(1 for t in weekly_trades if t.pnl > 0)
            weekly_losing_trades = sum(1 for t in weekly_trades if t.pnl <= 0)
            weekly_best_trade = max((t.pnl for t in weekly_trades), default=0.0)
            weekly_worst_trade = min((t.pnl for t in weekly_trades), default=0.0)
            
            # Get weekly stats from closed_trades (Rev 00223: Calculate from source of truth)
            weekly_stats = {
                'positions_closed': len(weekly_trades),  # Rev 00223: Count from closed_trades
                'winning_trades': weekly_winning_trades,  # Rev 00223: Calculated from closed_trades
                'losing_trades': weekly_losing_trades,  # Rev 00223: Calculated from closed_trades
                'total_pnl': weekly_total_pnl,  # Rev 00223: Calculated from closed_trades
                'total_wins_sum': weekly_total_wins,  # Rev 00135: For accurate profit factor
                'total_losses_sum': weekly_total_losses  # Rev 00135: For accurate profit factor
            }
            
            # Rev 00223: Log if discrepancy found between executor weekly_stats and calculated
            if (self._mock_executor.weekly_stats.get('positions_closed', 0) != len(weekly_trades) or
                abs(self._mock_executor.weekly_stats.get('total_pnl', 0.0) - weekly_total_pnl) > 0.01):
                log.warning(f"⚠️ EOD WEEKLY STATS DISCREPANCY: Executor weekly_stats (trades={self._mock_executor.weekly_stats.get('positions_closed', 0)}, P&L=${self._mock_executor.weekly_stats.get('total_pnl', 0.0):.2f}) != closed_trades (count={len(weekly_trades)}, P&L=${weekly_total_pnl:.2f})")
                log.warning(f"   Using closed_trades as source of truth for weekly stats (ensures persistence)")
            
            # DIAGNOSTIC (Rev 00180AE): Validate stats before sending
            total_trades_today = daily_stats.get('positions_closed', 0)
            total_trades_week = weekly_stats.get('positions_closed', 0)
            
            # Rev 00132: RECOVERY - If daily_stats is empty (e.g., after deployment), recover from closed_trades
            # Rev 00153: Also fix discrepancies when stats count doesn't match closed_trades count
            today_pt = pt_trading_date()
            today_trades = []
            for trade in self._mock_executor.closed_trades:
                closed_date = _so_trade_closed_date_pt(trade)
                if closed_date == today_pt:
                    today_trades.append(trade)
                elif not closed_date and trade.timestamp:
                    entry_ts = trade.timestamp
                    if getattr(entry_ts, "tzinfo", None) is None:
                        entry_ts = entry_ts.replace(tzinfo=timezone.utc)
                    if entry_ts.astimezone(PT_TZ).date() == today_pt:
                        today_trades.append(trade)
            
            actual_trades_count = len(today_trades)
            
            # Check for discrepancy or empty stats
            if (total_trades_today == 0 and actual_trades_count > 0) or (actual_trades_count > 0 and total_trades_today != actual_trades_count):
                if total_trades_today == 0:
                    log.warning(f"⚠️ EOD Report: daily_stats is empty but {actual_trades_count} closed trades found!")
                    log.warning(f"   Recovering daily stats from closed_trades (likely deployment wiped in-memory stats)")
                else:
                    log.warning(f"⚠️ EOD Report DISCREPANCY: daily_stats shows {total_trades_today} trades, but {actual_trades_count} trades closed today!")
                    log.warning(f"   Recovering daily stats from closed_trades to fix discrepancy (Rev 00153)")
                
                if today_trades:
                    log.info(f"✅ Recovered {len(today_trades)} trades from today in closed_trades")
                    
                    # Recalculate daily stats from recovered trades
                    recovered_pnl = sum(t.pnl for t in today_trades)
                    recovered_wins = sum(1 for t in today_trades if t.pnl > 0)
                    recovered_losses = sum(1 for t in today_trades if t.pnl <= 0)
                    recovered_best = max((t.pnl for t in today_trades), default=0.0)
                    recovered_worst = min((t.pnl for t in today_trades), default=0.0)
                    
                    # Update daily_stats with recovered data
                    daily_stats = {
                        'positions_closed': len(today_trades),
                        'winning_trades': recovered_wins,
                        'losing_trades': recovered_losses,
                        'total_pnl': recovered_pnl,
                        'best_trade': recovered_best,
                        'worst_trade': recovered_worst,
                        'best_symbol': 'N/A',  # TODO: Track symbols
                        'worst_symbol': 'N/A'
                    }
                    
                    log.info(f"✅ Recovered daily stats: {len(today_trades)} trades, ${recovered_pnl:.2f} P&L, {recovered_wins}W/{recovered_losses}L (was {total_trades_today} trades, ${daily_stats.get('total_pnl', 0.0):.2f} P&L)")
                    total_trades_today = len(today_trades)
                    
                    # Rev 00153: Also update mock_executor's daily_stats to prevent future discrepancies
                    self._mock_executor.daily_stats['positions_closed'] = len(today_trades)
                    self._mock_executor.daily_stats['total_pnl'] = recovered_pnl
                    self._mock_executor.daily_stats['winning_trades'] = recovered_wins
                    self._mock_executor.daily_stats['losing_trades'] = recovered_losses
                    self._mock_executor.daily_stats['best_trade'] = recovered_best
                    self._mock_executor.daily_stats['worst_trade'] = recovered_worst
                    log.info(f"✅ Updated mock_executor.daily_stats to match recovered data")
            
            if total_trades_today == 0 and total_trades_week == 0:
                log.warning(f"⚠️ EOD Report: No trades recorded today or this week!")
                log.warning(f"   This may indicate a stats tracking issue or all positions still open")
            
            active_positions = len(self._mock_executor.active_trades)
            account_balance = self._mock_executor.account_balance
            # Rev 00135: Use persisted starting balance for accurate % calculations
            from .mock_trading_executor import get_so_demo_starting_balance

            starting_balance = getattr(
                self._mock_executor, 'starting_balance', get_so_demo_starting_balance()
            )
            
            # Rev 00135: Get all-time stats from mock executor
            # Rev 00185: Calculate ALL stats from closed_trades to ensure accuracy and persistence
            # This ensures all-time stats include ALL historical trades, even after deployments
            all_time_total_wins_sum = 0.0
            all_time_total_losses_sum = 0.0
            all_time_total_pnl = 0.0
            all_time_winning_trades = 0
            all_time_losing_trades = 0
            
            for trade in self._mock_executor.closed_trades:
                all_time_total_pnl += trade.pnl
                if trade.pnl > 0:
                    all_time_total_wins_sum += trade.pnl
                    all_time_winning_trades += 1
                else:
                    all_time_total_losses_sum += abs(trade.pnl)
                    all_time_losing_trades += 1
            
            # Rev 00185: Use calculated values from closed_trades (source of truth)
            # This ensures all-time stats are always accurate and include all historical trades
            all_time_stats = {
                'total_trades': len(self._mock_executor.closed_trades),  # Count from closed_trades
                'winning_trades': all_time_winning_trades,  # Calculated from closed_trades
                'losing_trades': all_time_losing_trades,  # Calculated from closed_trades
                'total_pnl': all_time_total_pnl,  # Calculated from closed_trades
                'total_wins_sum': all_time_total_wins_sum,  # For accurate profit factor
                'total_losses_sum': all_time_total_losses_sum  # For accurate profit factor
            }
            
            # Rev 00185: Validate consistency and log if discrepancy found
            if (self._mock_executor.total_trades != len(self._mock_executor.closed_trades) or
                abs(self._mock_executor.total_pnl - all_time_total_pnl) > 0.01):
                log.warning(f"⚠️ EOD STATS DISCREPANCY: Executor stats (trades={self._mock_executor.total_trades}, P&L=${self._mock_executor.total_pnl:.2f}) != closed_trades (count={len(self._mock_executor.closed_trades)}, P&L=${all_time_total_pnl:.2f})")
                log.warning(f"   Using closed_trades as source of truth for all-time stats (ensures persistence)")
            
            # Send unified EOD report (🛃 format with weekly stats and all-time stats)
            log.info(f"📤 Sending unified 🛃 EOD report...")
            success = await self.send_end_of_day_report(
                daily_stats=daily_stats,
                weekly_stats=weekly_stats,
                active_positions=active_positions,
                mode="DEMO",
                account_balance=account_balance,
                starting_balance=starting_balance,
                all_time_stats=all_time_stats
            )
            
            if not success:
                log.error(f"❌ Failed to send unified 🛃 EOD report - send_end_of_day_report returned False")
                # Don't return - still reset stats and mark as sent
            else:
                log.info(f"✅ Unified 🛃 EOD report sent successfully!")
            
            # Rev 00135: Save account balance and weekly stats before resetting daily stats
            # Rev 00185: Save ALL trade history to GCS to ensure persistence across deployments
            # NOTE: Account balance and weekly stats are NOT reset - they persist across days
            # NOTE: closed_trades are NEVER cleared - they accumulate all historical trades
            self._mock_executor._save_mock_data()
            log.info(f"💰 Saved account balance: ${self._mock_executor.account_balance:,.2f} (will persist for tomorrow)")
            log.info(f"📊 Saved {len(self._mock_executor.closed_trades)} total trades to GCS (all historical trades preserved)")
            
            # Reset daily stats after sending report (mock executor handles this internally)
            self._mock_executor.reset_daily_stats()
            
            log.info(f"✅ Demo Mode EOD summary complete ({today}) - UNIFIED 🛃 FORMAT")
            
        except Exception as e:
            log.error(f"Failed to send Demo Mode EOD summary: {e}")
    
    async def _send_live_eod_summary(self):
        """Send Live Mode end-of-day summary (with GCS-based deduplication - Rev 00180AE)"""
        try:
            if not hasattr(self, '_unified_trade_manager') or not self._unified_trade_manager:
                log.warning("No unified trade manager available for Live EOD summary")
                return
            
            # DEDUPLICATION: Check if EOD report already sent today (Rev 00180AE - GCS-based)
            # Rev 00047: Date-based marker (internal scheduler disabled, so no blocking)
            # Ensures only ONE EOD report per day, even if Cloud Scheduler retries
            from .eod_report_helpers import pt_trading_date_iso, try_claim_eod_marker
            from .runtime_mode import alert_dedup_service_token

            today = pt_trading_date_iso()  # Rev 00362: PT trading day
            eod_sent_key = f"eod_sent_{alert_dedup_service_token()}_{today}_LIVE_unified"
            
            # Try GCS-based deduplication first (works across all instances)
            try:
                from .gcs_persistence import get_gcs_persistence
                gcs = get_gcs_persistence()
                
                if not try_claim_eod_marker(gcs, eod_sent_key):
                    log.info(f"✅ Live EOD report already sent today ({today}) - only ONE report per day, skipping duplicate")
                    return
                
                log.info(f"📝 Created GCS EOD marker for {today} LIVE")
                
            except Exception as gcs_error:
                log.warning(f"GCS deduplication failed (non-critical): {gcs_error}")
                # Fall back to class-level cache (works within same instance)
                if not hasattr(self.__class__, '_eod_sent_cache_live'):
                    self.__class__._eod_sent_cache_live = {}
                
                if eod_sent_key in self.__class__._eod_sent_cache_live:
                    log.info(f"Live EOD report already sent today ({today}) - skipping duplicate (class cache)")
                    return
                
                # Mark as sent in class cache
                self.__class__._eod_sent_cache_live[eod_sent_key] = datetime.utcnow()  # Rev 00075: UTC consistency
            
            # Rev 00180AE: Use unified EOD report function (centralized in alert_manager)
            # DIAGNOSTIC (Rev 00180AE): Check unified trade manager state before reading stats
            log.info(f"📊 LIVE EOD DIAGNOSTIC: Unified TM instance: {id(self._unified_trade_manager)}")
            log.info(f"📊 LIVE EOD DIAGNOSTIC: Active positions: {len(self._unified_trade_manager.active_positions)}")
            log.info(f"📊 LIVE EOD DIAGNOSTIC: Daily stats raw: {self._unified_trade_manager.daily_stats}")
            log.info(f"📊 LIVE EOD DIAGNOSTIC: Weekly stats raw: {self._unified_trade_manager.weekly_stats}")
            
            # Rev 00135: Calculate profit factors from actual trade data (like Demo mode)
            today = datetime.utcnow().date()
            current_week_start = datetime.utcnow().date()
            days_since_monday = current_week_start.weekday()
            monday = current_week_start - timedelta(days=days_since_monday)
            
            # Calculate daily profit factor from today's trades
            daily_total_wins = 0.0
            daily_total_losses = 0.0
            if hasattr(self._unified_trade_manager, 'trade_history') and self._unified_trade_manager.trade_history:
                for trade in self._unified_trade_manager.trade_history:
                    if hasattr(trade, 'exit_time') and trade.exit_time and trade.exit_time.date() == today:
                        pnl = getattr(trade, 'pnl', getattr(trade, 'pnl_dollars', 0.0))
                        if pnl > 0:
                            daily_total_wins += pnl
                        else:
                            daily_total_losses += abs(pnl)
                    elif isinstance(trade, dict):
                        exit_time = trade.get('exit_time')
                        if exit_time:
                            if isinstance(exit_time, datetime):
                                exit_date = exit_time.date()
                            else:
                                exit_date = exit_time
                            if exit_date == today:
                                pnl = trade.get('pnl', trade.get('pnl_dollars', 0.0))
                                if pnl > 0:
                                    daily_total_wins += pnl
                                else:
                                    daily_total_losses += abs(pnl)
            
            # Calculate weekly profit factor from this week's trades
            weekly_total_wins = 0.0
            weekly_total_losses = 0.0
            weekly_trades = []  # Rev 00224: Collect all weekly trades for accurate calculation
            if hasattr(self._unified_trade_manager, 'trade_history') and self._unified_trade_manager.trade_history:
                for trade in self._unified_trade_manager.trade_history:
                    exit_date = None
                    if hasattr(trade, 'exit_time') and trade.exit_time:
                        exit_date = trade.exit_time.date() if isinstance(trade.exit_time, datetime) else trade.exit_time
                    elif isinstance(trade, dict):
                        exit_time = trade.get('exit_time')
                        if exit_time:
                            exit_date = exit_time.date() if isinstance(exit_time, datetime) else exit_time
                    
                    if exit_date and exit_date >= monday:
                        weekly_trades.append(trade)
                        pnl = getattr(trade, 'pnl', getattr(trade, 'pnl_dollars', 0.0)) if hasattr(trade, 'pnl') or hasattr(trade, 'pnl_dollars') else (trade.get('pnl', trade.get('pnl_dollars', 0.0)) if isinstance(trade, dict) else 0.0)
                        if pnl > 0:
                            weekly_total_wins += pnl
                        else:
                            weekly_total_losses += abs(pnl)
            
            # Get stats from unified trade manager
            daily_stats = {
                'positions_closed': self._unified_trade_manager.daily_stats['positions_closed'],
                'winning_trades': self._unified_trade_manager.daily_stats['winning_trades'],
                'losing_trades': self._unified_trade_manager.daily_stats['losing_trades'],
                'total_pnl': self._unified_trade_manager.daily_stats['total_pnl'],
                'best_trade': self._unified_trade_manager.daily_stats.get('best_trade', 0.0),
                'worst_trade': self._unified_trade_manager.daily_stats.get('worst_trade', 0.0),
                'best_symbol': 'N/A',  # TODO: Track symbols
                'worst_symbol': 'N/A',
                'total_wins_sum': daily_total_wins,  # Rev 00135: For accurate profit factor
                'total_losses_sum': daily_total_losses  # Rev 00135: For accurate profit factor
            }
            
            # DIAGNOSTIC (Rev 00180AE): Validate stats before sending
            total_trades_today = daily_stats.get('positions_closed', 0)
            if total_trades_today == 0:
                log.warning(f"⚠️ Live EOD Report: No trades recorded today!")
                log.warning(f"   This may indicate a stats tracking issue or all positions still open")
            
            # Rev 00224: Calculate weekly stats from trade_history (source of truth) instead of weekly_stats
            # This ensures weekly stats include ALL trades from the week, even after deployments
            weekly_total_pnl = 0.0
            weekly_winning_trades = 0
            weekly_losing_trades = 0
            for trade in weekly_trades:
                pnl = getattr(trade, 'pnl', getattr(trade, 'pnl_dollars', 0.0)) if hasattr(trade, 'pnl') or hasattr(trade, 'pnl_dollars') else (trade.get('pnl', trade.get('pnl_dollars', 0.0)) if isinstance(trade, dict) else 0.0)
                weekly_total_pnl += pnl
                if pnl > 0:
                    weekly_winning_trades += 1
                else:
                    weekly_losing_trades += 1
            
            weekly_stats = {
                'positions_closed': len(weekly_trades),  # Rev 00224: Count from trade_history
                'winning_trades': weekly_winning_trades,  # Rev 00224: Calculated from trade_history
                'losing_trades': weekly_losing_trades,  # Rev 00224: Calculated from trade_history
                'total_pnl': weekly_total_pnl,  # Rev 00224: Calculated from trade_history
                'total_wins_sum': weekly_total_wins,  # Rev 00135: For accurate profit factor
                'total_losses_sum': weekly_total_losses  # Rev 00135: For accurate profit factor
            }
            
            # Rev 00224: Log if discrepancy found between executor weekly_stats and calculated
            if (self._unified_trade_manager.weekly_stats.get('positions_closed', 0) != len(weekly_trades) or
                abs(self._unified_trade_manager.weekly_stats.get('total_pnl', 0.0) - weekly_total_pnl) > 0.01):
                log.warning(f"⚠️ LIVE EOD WEEKLY STATS DISCREPANCY: Executor weekly_stats (trades={self._unified_trade_manager.weekly_stats.get('positions_closed', 0)}, P&L=${self._unified_trade_manager.weekly_stats.get('total_pnl', 0.0):.2f}) != trade_history (count={len(weekly_trades)}, P&L=${weekly_total_pnl:.2f})")
                log.warning(f"   Using trade_history as source of truth for weekly stats (ensures persistence)")
            
            active_positions = len(self._unified_trade_manager.active_positions)
            
            # Get account balance from unified trade manager (via compound engine or E*TRADE)
            account_balance = 10000.0  # Default fallback
            starting_balance = 10000.0  # Default fallback
            
            try:
                # Rev 00108: Live mode uses E*TRADE directly - no compound engine
                # Get account balance from E*TRADE API
                if hasattr(self._unified_trade_manager, 'etrade_trading') and self._unified_trade_manager.etrade_trading:
                    account_summary = self._unified_trade_manager.etrade_trading.get_account_summary()
                    if 'error' not in account_summary:
                        account_balance = account_summary['balance'].get('account_value', 10000.0)
                        starting_balance = getattr(self._unified_trade_manager, 'starting_balance', account_balance)
                        log.debug(f"📊 Live EOD: Account ${account_balance:,.2f} from E*TRADE (real-time)")
            except Exception as balance_error:
                log.warning(f"⚠️ Could not get Live account balance: {balance_error}, using fallback")
            
            # Rev 00135: Get all-time stats from unified_metrics
            all_time_total_wins_sum = 0.0
            all_time_total_losses_sum = 0.0
            if hasattr(self._unified_trade_manager, 'trade_history') and self._unified_trade_manager.trade_history:
                for trade in self._unified_trade_manager.trade_history:
                    pnl = getattr(trade, 'pnl', getattr(trade, 'pnl_dollars', 0.0)) if hasattr(trade, 'pnl') or hasattr(trade, 'pnl_dollars') else (trade.get('pnl', trade.get('pnl_dollars', 0.0)) if isinstance(trade, dict) else 0.0)
                    if pnl > 0:
                        all_time_total_wins_sum += pnl
                    else:
                        all_time_total_losses_sum += abs(pnl)
            
            all_time_stats = {
                'total_trades': self._unified_trade_manager.unified_metrics.get('total_trades', 0),
                'winning_trades': self._unified_trade_manager.unified_metrics.get('winning_trades', 0),
                'losing_trades': self._unified_trade_manager.unified_metrics.get('losing_trades', 0),
                'total_pnl': self._unified_trade_manager.unified_metrics.get('total_pnl', 0.0),
                'total_wins_sum': all_time_total_wins_sum,  # Rev 00135: For accurate profit factor
                'total_losses_sum': all_time_total_losses_sum  # Rev 00135: For accurate profit factor
            }
            
            # Send unified EOD report (🛃 format with weekly stats and all-time stats)
            log.info(f"📤 Sending unified 🛃 EOD report (LIVE)...")
            success = await self.send_end_of_day_report(
                daily_stats=daily_stats,
                weekly_stats=weekly_stats,
                active_positions=active_positions,
                mode="LIVE",
                account_balance=account_balance,
                starting_balance=starting_balance,
                all_time_stats=all_time_stats  # Rev 00135: Add all-time stats
            )
            
            if not success:
                log.error(f"❌ Failed to send unified 🛃 EOD report (LIVE) - send_end_of_day_report returned False")
            else:
                log.info(f"✅ Unified 🛃 EOD report (LIVE) sent successfully!")
            
            log.info(f"✅ Live Mode EOD summary complete ({today}) - UNIFIED 🛃 FORMAT")
            
        except Exception as e:
            log.error(f"Failed to send Live Mode EOD summary: {e}")
    
    def _calculate_max_drawdown(self, trades: List[Dict]) -> float:
        """Calculate maximum drawdown from trades"""
        if not trades:
            return 0.0
        
        peak = 0.0
        max_dd = 0.0
        running_pnl = 0.0
        
        for trade in trades:
            running_pnl += trade.get('pnl', 0)
            if running_pnl > peak:
                peak = running_pnl
            dd = peak - running_pnl
            if dd > max_dd:
                max_dd = dd
        
        return max_dd
    
    def _calculate_capital_used_pct(self) -> float:
        """Calculate percentage of capital used"""
        # This would need to be calculated based on account balance and position sizes
        # For now, return a default value
        return 82.0
    
    def add_trade_to_history(self, trade_data: Dict[str, Any]):
        """Add a trade to the daily history for end-of-day summary"""
        trade_data['date'] = datetime.now()
        self.trade_history.append(trade_data)
        
        # Keep only last 30 days of trades
        cutoff_date = datetime.now() - timedelta(days=30)
        self.trade_history = [t for t in self.trade_history 
                            if t.get('date', datetime.now()) > cutoff_date]
    
    # ============================================================================================================================================
    # UTILITY METHODS
    # ============================================================================================================================================
    
    def _check_alert_throttling(self, alert: Alert) -> bool:
        """Check if alert should be throttled"""
        current_time = time.time()
        alert_key = f"{alert.alert_type.value}_{alert.level.value}"
        
        # Check cooldown
        if current_time - self.last_alert_time.get(alert_key, 0) < self.alert_cooldown_seconds:
            return False
        
        # Check rate limiting
        minute_key = f"{alert_key}_{current_time // 60}"
        if self.alert_counts[minute_key] >= self.max_alerts_per_minute:
            return False
        
        self.last_alert_time[alert_key] = current_time
        self.alert_counts[minute_key] += 1
        
        return True
    
    def _track_alert(self, alert: Alert):
        """Track alert for analytics"""
        self.alert_history.append(alert)
    
    def get_alert_statistics(self) -> Dict[str, Any]:
        """Get alert statistics"""
        if not self.alert_history:
            return {}
        
        stats = {
            'total_alerts': len(self.alert_history),
            'alerts_by_type': defaultdict(int),
            'alerts_by_level': defaultdict(int),
            'recent_alerts': []
        }
        
        for alert in self.alert_history:
            stats['alerts_by_type'][alert.alert_type.value] += 1
            stats['alerts_by_level'][alert.level.value] += 1
        
        # Get recent alerts (last 10)
        stats['recent_alerts'] = [
            {
                'type': alert.alert_type.value,
                'level': alert.level.value,
                'title': alert.title,
                'timestamp': alert.timestamp.isoformat()
            }
            for alert in list(self.alert_history)[-10:]
        ]
        
        return stats
    
    # ================================================================================================================================================
    # OAUTH ALERT METHODS
    # ================================================================================================================================================
    
    
    async def send_oauth_renewal_success(self, environment: str, token_valid: bool = True) -> bool:
        """
        Send OAuth token renewal success notification
        
        Args:
            environment: Environment (prod or sandbox)
            token_valid: Whether token has been confirmed valid (default: True)
            
        Returns:
            True if alert sent successfully
            
        Note:
            Only sends success alert if token_valid is True.
            If token_valid is False, this method returns False without sending.
        """
        try:
            # Only send success alert if token is confirmed valid
            if not token_valid:
                log.warning(f"Token renewal completed but token is INVALID for {environment} - skipping success alert")
                return False
            
            # Get current time in Pacific Time and Eastern Time
            from zoneinfo import ZoneInfo
            pt_tz = ZoneInfo('America/Los_Angeles')
            et_tz = ZoneInfo('America/New_York')
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            
            # Format times properly
            pt_time = now_pt.strftime('%I:%M %p PT')
            et_time = now_et.strftime('%I:%M %p ET')
            
            # Determine system mode based on environment
            if environment.lower() == 'prod':
                system_mode = "Live Trading Enabled"
                env_label = "Production"
                mode_label = "Live"
            else:
                system_mode = "Demo Trading Available"
                env_label = "Sandbox"
                mode_label = "Demo"
            
            message = f"""====================================================================

✅ OAuth {env_label} Token Renewed
          Time: {pt_time} ({et_time})

🎉 Success! E*TRADE {environment.lower()} token successfully renewed for {mode_label}

📊 System Mode: {system_mode}
💎 Status: Trading system ready and operational

🌐 Public Dashboard: 
          https://easy-trading-oauth-v2.web.app"""
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                # Update OAuth status
                self.oauth_status[environment]['last_renewed'] = datetime.now()
                self.oauth_status[environment]['is_valid'] = True
                log.info(f"OAuth renewal success alert sent for {environment}")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending OAuth renewal success alert: {e}")
            return False
    
    async def send_oauth_renewal_error(self, environment: str, error_message: str) -> bool:
        """Send OAuth token renewal error notification"""
        try:
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo("America/New_York"))
            et_time = now_et.strftime("%I:%M %p ET")
            message = f"""❌ OAuth Token Renewal Failed

🔐 Environment: {environment.upper()}
⏰ Time: {et_time}
🚨 Error: {error_message}

🔧 Please check the OAuth web app and try again
🔗 URL: {self.oauth_renewal_url}/oauth/start?env={environment}"""
            
            success = await self._send_telegram_message(message, AlertLevel.ERROR)
            
            if success:
                # Update OAuth status
                self.oauth_status[environment]['is_valid'] = False
                log.info(f"OAuth renewal error alert sent for {environment}")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending OAuth renewal error alert: {e}")
            return False
    
    def update_oauth_status(self, environment: str, is_valid: bool, last_renewed: datetime = None):
        """Update OAuth token status"""
        try:
            self.oauth_status[environment]['is_valid'] = is_valid
            if last_renewed:
                self.oauth_status[environment]['last_renewed'] = last_renewed
            else:
                self.oauth_status[environment]['last_renewed'] = datetime.now()
            
            log.info(f"OAuth status updated for {environment}: valid={is_valid}")
            
        except Exception as e:
            log.error(f"Error updating OAuth status: {e}")
    
    def get_oauth_status(self, environment: str) -> Dict[str, Any]:
        """Get current OAuth status for environment"""
        return self.oauth_status.get(environment, {
            'last_renewed': None,
            'is_valid': False,
            'expires_at': None
        })
    
    async def schedule_oauth_morning_alert(self) -> bool:
        """Schedule OAuth morning alert (called by Cloud Scheduler)"""
        try:
            if not self.oauth_enabled:
                log.info("OAuth alerts disabled, skipping morning alert scheduling")
                return False
            
            # This method is called by Cloud Scheduler at 8:30 AM ET
            success = await self.send_oauth_morning_alert()
            
            if success:
                log.info("OAuth morning alert scheduled and sent")
            else:
                log.error("Failed to send scheduled OAuth morning alert")
            
            return success
            
        except Exception as e:
            log.error(f"Error in scheduled OAuth morning alert: {e}")
            return False
    
    async def shutdown(self):
        """Shutdown the alert manager"""
        log.info("Prime Alert Manager shutting down...")
        
        # Send shutdown notification
        await self.send_system_alert(
            "🔌 System Shutdown",
            "Easy ORB Strategy system is shutting down gracefully.",
            AlertLevel.INFO
        )
    
    async def send_oauth_alert(self, title: str, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        """
        Send OAuth-related alert
        
        Args:
            title: Alert title
            message: Alert message
            level: Alert level
            
        Returns:
            True if alert sent successfully
        """
        try:
            # Format OAuth alert
            formatted_message = (
                f"🔐 <b>OAuth alert</b>\n\n"
                f"<b>{_telegram_html_escape(title)}</b>\n\n"
                f"{_telegram_html_escape(message)}"
            )
            
            # Send alert
            success = await self._send_telegram_message(formatted_message, level)
            
            if success:
                log.info(f"OAuth alert sent: {title}")
            else:
                log.warning(f"Failed to send OAuth alert: {title}")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending OAuth alert: {e}")
            return False

    async def send_oauth_warning(self, environment: str, message: str) -> bool:
        """OAuth warning — thin wrapper for keepalive / integration call sites."""
        env_esc = _telegram_html_escape(environment)
        body_esc = _telegram_html_escape(message)
        return await self.send_oauth_alert(
            f"OAuth Warning ({env_esc})",
            body_esc,
            AlertLevel.WARNING,
        )

    async def send_custom_alert(
        self,
        title: str,
        message: str,
        alert_type: str = "custom",
        priority: str = "medium",
        level: AlertLevel = AlertLevel.INFO,
    ) -> bool:
        """Generic system alert for health endpoints and similar call sites."""
        title_esc = _telegram_html_escape(title)
        body_esc = _telegram_html_escape(message.replace("**", ""))
        alert_level = AlertLevel.ERROR if priority == "high" else level
        formatted = f"<b>{title_esc}</b>\n\n{body_esc}"
        return await self._send_telegram_message(formatted, alert_level)

    def _morning_alert_sent_cache_key(self, today: str) -> str:
        from .runtime_mode import alert_dedup_service_token, mode_label
        mode_token = mode_label().lower()
        service_token = alert_dedup_service_token()
        return f"morning_alert_sent_{mode_token}_{service_token}_{today}"

    def _is_morning_or_holiday_alert_sent_today(self, today: str) -> bool:
        morning_alert_sent_key = self._morning_alert_sent_cache_key(today)
        try:
            from .gcs_persistence import get_gcs_persistence
            from .runtime_mode import gcs_alert_marker_path
            gcs = get_gcs_persistence()
            if gcs.file_exists(gcs_alert_marker_path(morning_alert_sent_key)):
                return True
        except Exception as gcs_error:
            log.warning(f"GCS deduplication check failed (non-critical): {gcs_error}")
        if not hasattr(self.__class__, '_morning_alert_sent_cache'):
            self.__class__._morning_alert_sent_cache = {}
        return morning_alert_sent_key in self.__class__._morning_alert_sent_cache

    def _mark_morning_or_holiday_alert_sent_today(self, today: str) -> None:
        morning_alert_sent_key = self._morning_alert_sent_cache_key(today)
        try:
            from .gcs_persistence import get_gcs_persistence
            from .runtime_mode import gcs_alert_marker_path
            gcs = get_gcs_persistence()
            gcs.upload_string(
                gcs_alert_marker_path(morning_alert_sent_key),
                datetime.now().isoformat(),
            )
            log.info(f"📝 Created GCS morning/holiday alert marker for {today}")
        except Exception as gcs_error:
            log.warning(f"GCS deduplication marker failed (non-critical): {gcs_error}")
        if not hasattr(self.__class__, '_morning_alert_sent_cache'):
            self.__class__._morning_alert_sent_cache = {}
        self.__class__._morning_alert_sent_cache[morning_alert_sent_key] = datetime.utcnow()

    async def send_oauth_morning_alert(self, live_api_ok: Optional[bool] = None) -> bool:
        """
        Send Good Morning alert 1 hour before market open (8:30 AM ET / 5:30 AM PT)
        Shows token status and system readiness
        
        OCT 25, 2025: Now checks for holidays and sends holiday alert instead of morning alert
        Rev 00233 (Jan 8, 2026): Added time validation and GCS-based deduplication
        Rev 00356: live_api_ok from SPY quote probe — metadata-only Valid is not enough for ORB.
        
        Args:
            live_api_ok: True/False if caller ran ensure_valid_token_before_orb_capture; None = probe here.
        
        Returns:
            True if alert sent successfully
        """
        try:
            # Get current time in both timezones
            from zoneinfo import ZoneInfo
            et_tz = ZoneInfo('America/New_York')
            pt_tz = ZoneInfo('America/Los_Angeles')
            now_et = datetime.now(et_tz)
            now_pt = datetime.now(pt_tz)
            
            # Rev 00233: Time validation - only send between 5:30-5:35 AM PT (8:30-8:35 AM ET)
            # This prevents accidental alerts from manual testing or misconfigured schedulers
            pt_now = datetime.now(pt_tz)
            pt_time = pt_now.time()
            from modules.session_clock import in_morning_alert_window

            if not in_morning_alert_window(pt_time):
                from modules.session_clock import morning_alert_window_pt, fmt_hhmm

                start_a, end_a = morning_alert_window_pt()
                log.warning(
                    f"⚠️ Good Morning alert called outside valid window ({pt_time.strftime('%H:%M')} PT). "
                    f"Valid window: {fmt_hhmm(start_a)}-{fmt_hhmm(end_a)} PT. Skipping."
                )
                return False
            
            today = now_pt.date().isoformat()
            today_date = now_et.date()
            day_name = now_et.strftime('%A, %B %d, %Y')

            # Skip weekends (already handled by Cloud Scheduler schedule)
            if today_date.weekday() >= 5:
                log.info("Weekend - skipping morning alert")
                return True

            # Holiday check BEFORE dedup marker — holiday alert replaces Good Morning
            try:
                from .dynamic_holiday_calculator import should_skip_trading

                should_skip, skip_reason, holiday_name = should_skip_trading(today_date)

                if should_skip:
                    if self._is_morning_or_holiday_alert_sent_today(today):
                        log.info(
                            f"✅ Morning/holiday alert already sent today ({today}) - skipping duplicate"
                        )
                        return True
                    log.info(f"🎃 Holiday detected: {holiday_name} ({skip_reason}) - sending holiday alert")
                    success = await self.send_holiday_alert(holiday_name, skip_reason)
                    if success:
                        self._mark_morning_or_holiday_alert_sent_today(today)
                    return success
            except Exception as e:
                log.warning(f"Could not check holiday status: {e}")

            # GCS dedup: one Good Morning OR holiday alert per day (read before send; write after success)
            if self._is_morning_or_holiday_alert_sent_today(today):
                log.info(f"✅ Good Morning alert already sent today ({today}) - only ONE alert per day, skipping duplicate")
                return True
            
            # Check token status from Secret Manager
            # Production OAuth token only (E*TRADE sandbox access tokens removed)
            import json
            import os
            from datetime import timezone, timedelta
            from zoneinfo import ZoneInfo
            from datetime import datetime as dt_cls
            
            et_tz = ZoneInfo('America/New_York')
            now_et = dt_cls.now(et_tz)
            
            # Calculate last midnight ET (tokens before this are expired)
            last_midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
            
            log.info("🔍 Checking production OAuth token...")
            log.info(f"📅 Current time: {now_et.strftime('%I:%M %p ET on %B %d, %Y')}")
            log.info(f"⏰ Last midnight ET: {last_midnight_et.strftime('%I:%M %p ET on %B %d, %Y')}")
            
            def check_token_expiration(env: str) -> tuple[bool, str]:
                """
                Check if token is valid (not expired)
                Returns: (is_valid, status_message)
                """
                try:
                    # Rev 00190: Use centralized cloud config instead of hardcoded project_id
                    from .config_loader import get_cloud_config
                    from .secret_version_cleanup import access_latest_enabled_secret
                    cloud_config = get_cloud_config()
                    project_id = cloud_config["project_id"]
                    
                    token_json = access_latest_enabled_secret(
                        f"etrade-oauth-{env}", project_id=project_id
                    )
                    token_data = json.loads(token_json)
                    
                    # Get token timestamp
                    timestamp_str = token_data.get('timestamp', '')
                    oauth_token = token_data.get('oauth_token', '')
                    oauth_secret = token_data.get('oauth_token_secret', '')
                    
                    # Check if tokens exist
                    if not oauth_token or len(oauth_token) < 10:
                        return False, "Token missing"
                    if not oauth_secret or len(oauth_secret) < 10:
                        return False, "Secret missing"
                    
                    # Check expiration
                    if timestamp_str:
                        token_time = dt_cls.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        token_time_et = token_time.astimezone(et_tz)
                        
                        # Token is valid if created AFTER last midnight ET
                        if token_time_et >= last_midnight_et:
                            time_str = token_time_et.strftime('%I:%M %p ET')
                            log.info(f"✅ {env.upper()} token VALID (renewed {time_str}, after midnight)")
                            return True, f"Valid (renewed {time_str})"
                        else:
                            time_str = token_time_et.strftime('%I:%M %p ET')
                            log.warning(f"❌ {env.upper()} token EXPIRED (renewed {time_str}, before midnight)")
                            return False, f"Expired (renewed {time_str})"
                    else:
                        log.warning(f"❌ {env.upper()} token has no timestamp")
                        return False, "No timestamp"
                        
                except Exception as e:
                    log.error(f"❌ Error checking {env} token: {e}")
                    return False, f"Error: {str(e)[:30]}"
            
            prod_valid, prod_status = check_token_expiration('prod')
            
            # Live E*TRADE quote probe (ORB uses API, not Secret Manager timestamp alone).
            if live_api_ok is None and prod_valid:
                try:
                    from .prime_etrade_trading import PrimeETradeTrading
                    probe = PrimeETradeTrading(environment='prod')
                    probe._load_tokens()
                    live_api_ok = probe.is_token_valid()
                except Exception as probe_err:
                    log.warning(f"Good Morning live API probe failed: {probe_err}")
                    live_api_ok = False
            
            tokens_valid = prod_valid and (live_api_ok is not False)
            metadata_only_invalid = prod_valid and live_api_ok is False
            
            log.info(
                f"📊 Token Status: Production metadata={prod_valid} ({prod_status}), "
                f"live_api={live_api_ok}, System Ready={tokens_valid}"
            )
            
            if tokens_valid:
                message = f"""====================================================================

🌅 <b>Good Morning!</b> ☁️☁️🌤️☁️☁️☁️🕊️☁️
          {day_name}

⏰ Market opens in 1 hour (9:30 AM ET)

🔐 Broker:
          • E*TRADE token:
            ✅ Valid
{_good_morning_broker_other_tokens_na()}

{_good_morning_status_ready_block()}

{_good_morning_trading_schedule_block()}

🌐 Public Dashboard: 
          https://easy-trading-oauth-v2.web.app

✅ Ready to trade!"""
                
                alert_level = AlertLevel.INFO
                log.info("Sending Good Morning alert - Production token VALID (metadata + live API)")
            
            elif metadata_only_invalid:
                message = f"""====================================================================

🌅 <b>Good Morning!</b> ☁️☁️🌧️☁️☁️☁️☁️
          {day_name}

⏰ Market opens in 1 hour (9:30 AM ET)

🔐 Broker:
          • E*TRADE token:
            ⚠️ Secret Manager: {prod_status}
            ❌ Live API: REJECTED
{_good_morning_broker_other_tokens_na()}

💎 Status:
          Renew production token NOW — https://easy-trading-oauth-v2.web.app

{_good_morning_trading_schedule_block()}

🌐 Public Dashboard: 
          https://easy-trading-oauth-v2.web.app

🚨 ORB will be PAUSED until live E*TRADE quotes succeed"""
                alert_level = AlertLevel.ERROR
                log.error("Sending Good Morning alert - metadata valid but live API REJECTED")
            
            else:
                # Production token invalid - critical
                message = f"""====================================================================

🌅 <b>Good Morning!</b> ☁️🌧️⛈️🌧️⛈️🌧️☁️🌧️
          {day_name}

⏰ Market opens in 1 hour (9:30 AM ET)

⚠️ TOKEN ALERT - 😱

🔐 Broker:
          • E*TRADE token:
            ❌ Invalid — {prod_status}
{_good_morning_broker_other_tokens_na()}

❌ Action required: Renew Production token NOW

{_good_morning_trading_schedule_block()}

🌐 Public Dashboard:
          https://easy-trading-oauth-v2.web.app

⚠️ Please renew Production token before market open!"""
                
                alert_level = AlertLevel.CRITICAL
                log.error("Sending Good Morning alert - Production token INVALID (NO TRADING POSSIBLE)")
            
            success = await self._send_telegram_message(message, alert_level)
            
            if success:
                self._mark_morning_or_holiday_alert_sent_today(today)
                log.info(f"Good Morning alert sent successfully (tokens {'valid' if tokens_valid else 'INVALID'})")
            else:
                log.warning("Failed to send Good Morning alert")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending Good Morning alert: {e}")
            return False
    
    async def send_oauth_token_renewed_confirmation(
        self, environment: str, live_api_ok: bool = True
    ) -> bool:
        """
        Send OAuth token renewal confirmation alert when tokens are successfully renewed
        
        Args:
            environment: "prod" (production OAuth only)
            live_api_ok: False skips alert (caller should send renewal error instead)
            
        Returns:
            True if alert sent successfully
        """
        try:
            if not live_api_ok:
                log.warning(
                    "Skipping renewal confirmation for %s — live E*TRADE API probe failed",
                    environment,
                )
                return False
            # Get current time in Pacific Time and Eastern Time
            from zoneinfo import ZoneInfo
            pt_tz = ZoneInfo('America/Los_Angeles')
            et_tz = ZoneInfo('America/New_York')
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            
            # Format times properly
            pt_time = now_pt.strftime('%I:%M %p PT')
            et_time = now_et.strftime('%I:%M %p ET')
            
            env_label = "Production"
            system_mode = "Trading enabled (Demo = sim account, Live = live account)"
            token_type = "Production token successfully renewed"
            
            message = f"""====================================================================

✅ OAuth {env_label} Token Renewed
          Time: {pt_time} ({et_time})

🎉 Success! E*TRADE {token_type}

📊 System Mode: {system_mode}
💎 Status: Trading system ready (live SPY quote verified)

🌐 Public Dashboard: 
          https://easy-trading-oauth-v2.web.app"""
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                log.info(f"OAuth {environment} token renewal confirmation sent (live API verified)")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending OAuth token renewal confirmation: {e}")
            return False

    async def send_buy_signal_alert(self, symbol: str, company_name: str, shares: int,
                                   entry_price: float, total_value: float, confidence: float,
                                   expected_return: float, quality_score: float,
                                   strategies_agreeing: str, stop_loss: float,
                                   take_profit: float, note: str = "") -> bool:
        """Send buy signal alert with comprehensive details"""
        try:
            # Determine confidence emoji
            if confidence >= 0.98:
                emoji = "🔰🔰🔰"
            elif confidence >= 0.85:
                emoji = "🔰🔰"
            elif confidence >= 0.70:
                emoji = "🔰"
            elif confidence >= 0.60:
                emoji = "📟"
            else:
                emoji = "🟡"
            
            sym_e = _telegram_html_escape(symbol)
            co_e = _telegram_html_escape(company_name)
            message = f"""📈 <b>BUY SIGNAL - {sym_e}</b> {emoji}

📊 <b>BUY</b> - {shares} shares - {co_e}
• Entry: {_fmt_usd(entry_price)}
• Total value: {_fmt_usd(total_value)}

💼 <b>Position details</b>
Symbol: {sym_e}
Confidence: {confidence:.0%}
Expected return: {expected_return:.1%}
Quality score: {quality_score:.0%}

📊 <b>Risk management</b>
Stop loss: {_fmt_usd(stop_loss)} ({((stop_loss - entry_price) / entry_price * 100):.1f}%)
Take profit: {_fmt_usd(take_profit)} ({((take_profit - entry_price) / entry_price * 100):.1f}%)

⏰ <b>Entry time:</b> {datetime.utcnow().strftime('%H:%M:%S')} UTC"""
            
            if note:
                message += f"\n\n{_telegram_html_escape(note)}"
            
            return await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
        except Exception as e:
            log.error(f"Failed to send buy signal alert: {e}")
            return False
    
    async def send_telegram_alert(self, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        """Public method to send raw Telegram message"""
        return await self._send_telegram_message(message, level)
    
    async def send_so_signal_collection(self, so_signals: List[Dict[str, Any]],
                                        total_scanned: int, mode: str = "DEMO",
                                        spx_orb_data: Optional[Dict[str, Any]] = None,
                                        qqq_orb_data: Optional[Dict[str, Any]] = None,
                                        spy_orb_data: Optional[Dict[str, Any]] = None,
                                        dte0_signals_qualified: Optional[int] = None,
                                        dte0_signals_list: Optional[List[Dict[str, Any]]] = None,
                                        dte_symbols_list: Optional[List[str]] = None,
                                        hard_gated_symbols: Optional[List[Dict[str, Any]]] = None,
                                        dte0_collection_candidates: Optional[int] = None,
                                        dte0_convex_passed: Optional[int] = None,
                                        dte0_hard_gate_passed: Optional[int] = None,
                                        dte0_pending_execution: Optional[int] = None,
                                        red_day_blocked: bool = False,
                                        red_day_reason: Optional[str] = None,  # Rev 00258
                                        zero_signals_reason: Optional[str] = None,  # Rev 00265: Diagnostic when 0 signals
                                        breakout_candidates_list: Optional[List[str]] = None) -> bool:
        """
        Send SO Signal Collection alert after scan completes (Rev 00180AE)
        
        Shows actual scan results - handles both signals found and no signals cases.
        Replaces the old separate "No Signals" alert.
        
        Args:
            so_signals: List of SO signals collected (may be empty)
            total_scanned: Total symbols scanned
            mode: Trading mode (DEMO/LIVE)
        """
        try:
            # Fail-closed: no Trade Signal Collection alert when SC is disabled.
            try:
                from modules.orb1_0dte_watch import signal_collection_enabled as _sc_on
                _sc_enabled = _sc_on()
            except Exception:
                _sc_enabled = os.getenv("ENABLE_ORB_SIGNAL_COLLECTION", "false").strip().lower() in (
                    "1", "true", "yes", "on",
                )
            if not _sc_enabled:
                log.info(
                    "ALERT_SKIP | alert=trade_signal_collection | reason=ENABLE_ORB_SIGNAL_COLLECTION=false"
                )
                return False

            # Rev 00065: Removed redundant datetime import (already imported at top)
            from zoneinfo import ZoneInfo
            
            # Rev 00232 (Jan 8, 2026): Add GCS-based deduplication to prevent duplicate alerts
            # across multiple Cloud Run instances (same pattern as EOD report)
            pt_tz = ZoneInfo('America/Los_Angeles')
            now_pt = datetime.now(pt_tz)
            today = now_pt.date().isoformat()  # Use PT date for consistency
            from .runtime_mode import alert_dedup_service_token, gcs_signal_collection_marker_path

            service_token = alert_dedup_service_token()
            collection_sent_key = f"so_collection_sent_{service_token}_{today}_{mode.lower()}"
            
            # Try GCS-based deduplication first (works across all instances)
            try:
                from .gcs_persistence import get_gcs_persistence
                gcs = get_gcs_persistence()
                
                # Per-service namespace — no cross-app dedup with FINAL/RESEARCH sibling
                gcs_marker_path = gcs_signal_collection_marker_path(collection_sent_key)
                
                # Try to download marker (if exists, alert already sent today)
                if gcs.file_exists(gcs_marker_path):
                    log.info(f"✅ SO Signal Collection alert already sent today ({today}, {mode} mode) - only ONE alert per day, skipping duplicate")
                    return True  # Return True since alert was already sent (not an error)
                
                # Upload marker file to GCS (atomic operation prevents race conditions)
                marker_content = datetime.now().isoformat()
                gcs.upload_string(gcs_marker_path, marker_content)
                log.info(f"📝 Created GCS SO collection marker for {today} ({mode} mode)")
                
            except Exception as gcs_error:
                log.warning(f"GCS deduplication failed (non-critical): {gcs_error}")
                # Fall back to class-level cache (works within same instance)
                if not hasattr(self.__class__, '_so_collection_sent_cache'):
                    self.__class__._so_collection_sent_cache = {}
                
                if collection_sent_key in self.__class__._so_collection_sent_cache:
                    log.info(f"SO Signal Collection alert already sent today ({today}, {mode} mode) - skipping duplicate (class cache)")
                    return True  # Return True since alert was already sent (not an error)
                
                # Mark as sent in class cache
                self.__class__._so_collection_sent_cache[collection_sent_key] = datetime.utcnow()
            
            # Get current time in both timezones
            et_tz = ZoneInfo('America/New_York')
            now_et = datetime.now(et_tz)
            
            pt_time = now_pt.strftime('%I:%M %p')
            et_time = now_et.strftime('%I:%M %p')
            
            def _load_symbol_order(csv_path: str) -> Dict[str, int]:
                order_map: Dict[str, int] = {}
                try:
                    if not os.path.exists(csv_path):
                        return order_map
                    with open(csv_path, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        if not reader.fieldnames:
                            return order_map
                        for row in reader:
                            symbol = str((row or {}).get("symbol", "")).strip().upper()
                            if symbol and symbol not in order_map:
                                order_map[symbol] = len(order_map)
                except Exception as order_error:
                    log.debug("Signal collection list order load failed for %s: %s", csv_path, order_error)
                return order_map

            # Jun 29, 2026: ORB SO + 0DTE shareable watchlist — single 0dte_list.csv source
            so_order_map = _load_symbol_order("data/watchlist/0dte_list.csv")
            dte_order_map = so_order_map or _load_symbol_order("data/watchlist/0dte_list.csv")

            # Rev 00271: Standard Orders are LONG-only; ensure we only show LONG in "Standard Orders Ready"
            so_long_only = [s for s in so_signals if s.get('side') == 'LONG']
            # Sort by watchlist order first so list alert matches configured symbol priority.
            # Fall back to existing priority rank/score behavior when symbol is not in the map.
            def _so_sort_key(item):
                idx, sig = item
                symbol = str(sig.get('symbol', '') or '').upper()
                list_pos = so_order_map.get(symbol)
                if list_pos is not None:
                    return (0, list_pos, idx)
                pr = sig.get('priority_rank')
                if pr is not None:
                    try:
                        return (1, float(pr), idx)
                    except (TypeError, ValueError):
                        pass
                ps = sig.get('priority_score')
                if ps is not None:
                    try:
                        return (2, -float(ps), idx)
                    except (TypeError, ValueError):
                        pass
                return (3, idx, idx)
            so_long_only = [sig for _, sig in sorted(list(enumerate(so_long_only)), key=_so_sort_key)]
            signal_count = len(so_long_only)
            
            # Extract ORB symbol list from signals (abbreviated list for confirmation)
            orb_symbol_list = []
            if signal_count > 0:
                for signal in so_long_only:
                    symbol = signal.get('symbol') or signal.get('original_symbol')
                    if symbol:
                        orb_symbol_list.append(symbol)
                orb_symbols_text = ", ".join(orb_symbol_list)
            else:
                orb_symbols_text = ""

            # Extract 0DTE symbol list from approved signals (abbreviated list for confirmation)
            # Rev 00230: Keep abbreviated - detailed info goes to execution alert
            # Rev 00272: Split into CALL (Long) and PUT (Short) for Signal Collection alert
            dte_symbol_list = []
            dte_call_list = []   # LONG / CALL
            dte_put_list = []    # SHORT / PUT
            if dte0_signals_list:
                # Sort by 0DTE watchlist order first, then existing rank/score fallback.
                def _dte_sort_key(item):
                    idx, sig = item
                    symbol = str(sig.get('symbol', '') or '').upper()
                    list_pos = dte_order_map.get(symbol)
                    if list_pos is not None:
                        return (0, list_pos, idx)
                    pr = sig.get('priority_rank')
                    if pr is not None:
                        try:
                            return (1, float(pr), idx)
                        except (TypeError, ValueError):
                            pass
                    ps = sig.get('priority_score')
                    if ps is not None:
                        try:
                            return (2, -float(ps), idx)
                        except (TypeError, ValueError):
                            pass
                    return (3, idx, idx)
                dte0_signals_list = [sig for _, sig in sorted(list(enumerate(dte0_signals_list)), key=_dte_sort_key)]
                for signal in dte0_signals_list:
                    symbol = signal.get('symbol', '')
                    if not symbol:
                        continue
                    direction = (signal.get('direction') or signal.get('option_type_label') or '').upper()
                    is_put = 'PUT' in direction or direction == 'SHORT'
                    if symbol not in dte_symbol_list:
                        dte_symbol_list.append(symbol)
                    if is_put:
                        if symbol not in dte_put_list:
                            dte_put_list.append(symbol)
                    else:
                        if symbol not in dte_call_list:
                            dte_call_list.append(symbol)
                dte_symbols_text = ", ".join(dte_symbol_list)
                dte_call_text = ", ".join(dte_call_list) if dte_call_list else ""
                dte_put_text = ", ".join(dte_put_list) if dte_put_list else ""
            else:
                dte_symbols_text = ""
                dte_call_text = ""
                dte_put_text = ""
            
            # Rev 00230: Hard Gated info moved to execution alert (removed from signal collection)
            # Jul 30: Breakout candidates removed from Signal Collection (activate after ORB2+TL1).
            _ = breakout_candidates_list  # accepted for caller compat; not rendered

            # Build message based on whether signals were found
            orb_section = ""
            dte_section = ""
            if (
                signal_count > 0
                or (dte0_signals_qualified is not None and dte0_signals_qualified > 0)
            ):
                # Signals found
                status_line = "Signal collection finished, ordered by watchlist priority…"
                
                # ORB SO CALL rank pool (Research app only; not shown in Final app tree)
                if orb_symbols_text:
                    # Leading \n = one blank line before this section.
                    orb_section = f"""
📑 <b>ORB SO Ready</b> <i>(Call only)</i> ({signal_count} symbols):
          <b>SO:</b> {orb_symbols_text}
"""
                
                # Build 0DTE section — complete list that passed verification
                if dte_symbols_text:
                    n_dte = len(dte_symbol_list)
                    dte_section = f"""
🔮 <b>0DTE Options Ready</b> ({n_dte} symbols):"""
                    if dte_call_text:
                        dte_section += f"""
          <b>CALL:</b> {dte_call_text}"""
                    if dte_put_text:
                        dte_section += f"""
          <b>PUT:</b> {dte_put_text}"""
                    if not dte_call_text and not dte_put_text:
                        dte_section += f"""
{dte_symbols_text}"""
                
                next_section = f"""
📡 <b>Signal Window:</b>
          7:15-7:30 AM PT (10:15-10:30 AM ET)
🚀 <b>Next:</b> ORB Options Execution
          7:30 AM PT (10:30 AM ET)"""
            else:
                # No signals - show status
                # Rev 00257: Indicate if Red Day filter blocked signals
                if red_day_blocked:
                    status_line = "🚨 <b>Red Day Detected</b>"
                    next_section = f"""

💡 <b>Status:</b>
          Signals cleared due to Red Day filter
          (Bearish market conditions detected)
📊 <b>Next:</b> Position monitoring
          (Throughout trading day)"""
                else:
                    status_line = "💢 <b>No Signals</b>"
                    # Rev 00262/00265: Add diagnostic information for 0 signals (include reason when available)
                    diagnostic_info = ""
                    if zero_signals_reason:
                        diagnostic_info = f"""
💡 <b>Reason (diagnostic):</b>
          {zero_signals_reason}"""
                    if dte0_signals_qualified == 0 and total_scanned > 0 and not zero_signals_reason:
                        diagnostic_info = f"""
💡 <b>Diagnostic:</b>
          • ORB validation rules may not have been met
          • 0DTE Long: price above ORB high + GREEN volume + prev candle close above open
          • 0DTE Short: price below ORB low + RED volume + prev candle close below open
          • Red Day filter does NOT block 0DTE (can go long or short)"""
                    next_section = f"""

💡 <b>Status:</b>
          No qualified setups detected{diagnostic_info}
📊 <b>Next:</b> Position monitoring
          (Throughout trading day)"""
            
            # Build 0DTE count display
            dte_count_display = ""
            if dte0_signals_qualified is not None:
                n_call = len(dte_call_list) if dte_call_list else 0
                n_put = len(dte_put_list) if dte_put_list else 0
                dte_count_display = (
                    f"          • <b>0DTE Options Signals:</b> {dte0_signals_qualified}\n"
                    f"             CALL = {n_call}, PUT = {n_put}"
                )

            # 0DTE pipeline diagnostics (7:30 visibility for collection→Convex→HardGate→execution handoff)
            dte_pipeline_display = ""
            if any(v is not None for v in [dte0_collection_candidates, dte0_convex_passed, dte0_hard_gate_passed, dte0_pending_execution]):
                cand = dte0_collection_candidates if dte0_collection_candidates is not None else 0
                cx = dte0_convex_passed if dte0_convex_passed is not None else 0
                hg = dte0_hard_gate_passed if dte0_hard_gate_passed is not None else 0
                pe = dte0_pending_execution if dte0_pending_execution is not None else 0
                dte_pipeline_display = (
                    f"\n          • <b>0DTE Pipeline:</b> collection={cand}\n"
                    f"             convex={cx}, hard_gate={hg}\n"
                    f"             queued_exec={pe}\n"
                )
            
            message = f"""🪽 <b>Trade Signal Collection</b>
{_alert_mode_header_line(mode)}
          Time: {pt_time} PT ({et_time} ET)

{status_line}

📊 <b>Results:</b>
          • <b>Symbols Scanned:</b> {total_scanned}
          • <b>ORB Signals Generated:</b> {signal_count}
{dte_count_display}{dte_pipeline_display}{orb_section}{dte_section}
{next_section}
"""

            success = await self._send_telegram_message(message, AlertLevel.INFO)
            
            if success:
                dte_count = dte0_signals_qualified if dte0_signals_qualified is not None else 0
                log.info(f"✅ SO Signal Collection alert sent: {signal_count} ORB + {dte_count} 0DTE signals from {total_scanned} symbols")
            
            return success
            
        except Exception as e:
            log.error(f"Failed to send SO signal collection alert: {e}")
            return False

    async def send_orb_so_execution_aggregated(self, so_signals: List[Dict[str, Any]], 
                                               total_scanned: int, mode: str = "DEMO",
                                               rejected_signals: List[Dict[str, Any]] = None,
                                               account_value: float = 1000.0,
                                               so_capital_pct: float = 90.0,
                                               filtered_expensive: int = 0,
                                               no_trades_reason: str = None,
                                               rejection_category: str = None) -> bool:
        """
        Send AGGREGATED Standard Order (SO) execution alert at 7:30 AM PT (batch execution).
        
        Shows all SO trades executed in ONE consolidated alert for clarity.
        Rev 00180g: Now includes rejected signals (insufficient capital)
        Oct 24, 2025: Added so_capital_pct parameter for unified configuration
        
        Args:
            so_signals: List of EXECUTED SO signals with trade details
            total_scanned: Total number of symbols scanned
            mode: Trading mode (DEMO or LIVE)
            rejected_signals: List of signals rejected at execution (capital or PP gates)
            account_value: Total account value
            so_capital_pct: SO capital allocation percentage (default 90.0)
            no_trades_reason: Explicit reason when 0 fills (Portfolio Protection, etc.)
            rejection_category: ``portfolio_protection`` | ``insufficient_capital`` for 0-fill copy
        
        Returns:
            True if alert sent successfully
        """
        try:
            # Fail-closed: no Standard Order Execution alert when ORB SO is disabled.
            if os.getenv("ENABLE_ORB_STRATEGY", "false").strip().lower() not in (
                "1", "true", "yes", "on",
            ):
                log.info(
                    "ALERT_SKIP | alert=standard_order_execution | reason=ENABLE_ORB_STRATEGY=false"
                )
                return False

            # Count signals
            so_count = len(so_signals)
            bullish_count = sum(1 for s in so_signals if s.get('side') == 'LONG' or 'inverse' not in s.get('reasoning', '').lower())
            inverse_count = so_count - bullish_count
            
            # Rev 00299: Sort by priority_rank ascending — matches demo batch sizing output order
            # (calculate_batch_position_sizes processes rank 1 first). Using priority_score alone can
            # diverge if scores tie or drift vs rank; rank is the execution contract.
            so_signals_sorted = sorted(
                so_signals,
                key=lambda s: (
                    int(s.get("priority_rank", 999) or 999),
                    -float(s.get("priority_score", s.get("confidence", 0)) or 0),
                ),
            )
            
            # Build trade list (NOW IN PRIORITY ORDER)
            trade_lines = []
            total_value = 0.0
            
            for i, signal in enumerate(so_signals_sorted, 1):
                symbol = signal.get('symbol', 'UNKNOWN')
                price = signal.get('price', 0.0)
                confidence = signal.get('confidence', 0.0)
                priority_score = signal.get('priority_score', confidence)  # Rev 00180AE: Get priority score
                priority_rank = signal.get('priority_rank', i)  # Rev 00180AE: Get rank
                is_inverse = signal.get('inverse_symbol') is not None
                original_symbol = signal.get('original_symbol', symbol)
                
                # Rev 00180V: Get actual quantity from signal if available (from execution)
                # Rev 00082: Use position_value from signal if available (already calculated post-execution)
                # account_value is now passed as parameter (actual Demo account balance)
                quantity = signal.get('quantity', 0)  # Get actual executed quantity if available
                trade_value = signal.get('position_value', 0.0)  # Rev 00082: Use pre-calculated value
                
                if quantity == 0 or trade_value == 0.0:
                    # Fallback: Estimate quantity (SO capital / num trades)
                    # Oct 24, 2025: Use parameter from unified config
                    so_capital = account_value * (so_capital_pct / 100.0)
                    num_trades = len(so_signals)
                    per_trade_value = so_capital / max(1, num_trades)
                    quantity = int(per_trade_value / price) if price > 0 else 0
                    trade_value = quantity * price
                
                total_value += trade_value
                
                # Rev 00180V: Calculate ACTUAL portfolio percentage (not ORB's raw %)
                actual_position_pct = (trade_value / account_value) * 100.0
                
                # Use actual trade ID from executed signal (if available)
                trade_id = signal.get('trade_id')
                if not trade_id:
                    # Fallback: Generate trade ID (Rev 00232: Shortened format for alerts - symbol_date_microseconds)
                    now = datetime.now()
                    date_str = now.strftime('%y%m%d')  # 2-digit year
                    microseconds_short = now.microsecond % 1000  # Last 3 digits for uniqueness
                    trade_prefix = "MOCK" if mode == "DEMO" else "LIVE"
                    trade_id = f"{trade_prefix}_{symbol}_{date_str}_{microseconds_short:03d}"
                
                # Format with inverse notation if applicable
                if is_inverse:
                    inverse_line = f"\n          🔄 <b>Inverse Trade:</b> {original_symbol} → {symbol}"
                else:
                    inverse_line = ""
                
                # Rev 00231: Enhanced format with bold formatting for key metrics
                # Bold: Rank, Priority Score, Confidence (per documentation)
                confidence_pct = int(confidence * 100)
                trade_lines.append(
                    f"{i}) 🟢 <b>BUY {quantity}</b> • <b>{symbol} @ {_fmt_usd(price)}</b> • <b>{_fmt_usd(trade_value)}</b>\n"
                    f"          <b>Rank #{priority_rank}</b> • Priority Score <b>{priority_score:.3f}</b>\n"
                    f"          <b>{confidence_pct}%</b> Confidence • {actual_position_pct:.1f}% Of Account{inverse_line}\n"
                    f"          <b>Trade ID:</b> <code>{_trade_id_text(trade_id)}</code>"
                )
            
            trades_text = "\n\n".join(trade_lines)
            
            # Rev 00180g: Build rejected signals section
            rejected_text = ""
            if rejected_signals and len(rejected_signals) > 0:
                rejected_lines = []
                for signal in rejected_signals:
                    symbol = signal.get('symbol', 'UNKNOWN')
                    price = signal.get('price', 0.0)
                    confidence = signal.get('confidence', 0.0)
                    pr = signal.get("priority_rank")
                    rank_suffix = f" — Rank #{int(pr)}" if pr is not None else ""
                    rejected_lines.append(
                        f"  • {symbol} @ {_fmt_usd(price)} ({confidence:.0%} confidence){rank_suffix}"
                    )

                if rejection_category == "portfolio_protection":
                    rejected_header = "⚠️ <b>Rejected (Portfolio Protection — Regime):</b>"
                elif rejection_category == "selection_filter":
                    rejected_header = "⚠️ <b>Rejected (Path Selection — not Regime PP):</b>"
                else:
                    rejected_header = "⚠️ <b>Rejected (Insufficient Capital):</b>"
                rejected_text = f"\n\n{rejected_header}\n" + "\n".join(rejected_lines)
            
            # FIX (Oct 24, 2025): Show deployment as % of TOTAL ACCOUNT
            # User requested: "$900 / $1,000 (90%)" not "$811 / $900 (81%)"
            # Display should show total account value (100%) and what % is deployed
            # Oct 24, 2025: Use parameter from unified config (no longer hardcoded)
            so_capital_total = account_value * (so_capital_pct / 100.0)  # Max deployable
            deployment_pct_of_account = (total_value / account_value) * 100.0 if account_value > 0 else 0.0
            
            # Rev 00180AE: Handle case where ALL trades were rejected (0 executed)
            if so_count > 0:
                # Some trades executed
                # FIX (Oct 24, 2025): Show denominator as total account (not SO allocation)
                execution_section = f"""💼 <b>Trades Executed:</b>

{trades_text}

💰 <b>Capital Deployment:</b>
          • <b>Deployed:</b> {_fmt_usd(total_value)} / {_fmt_usd(account_value, 0)} ({deployment_pct_of_account:.1f}%)

🛡️ <b>Monitoring:</b> All positions tracked by Stealth Trailing System (1.5% trailing)"""
            else:
                # NO trades executed - choose accurate reason (not always capital)
                total_rejected = len(rejected_signals) if rejected_signals else 0
                selection_lines_text = ""
                if rejection_category == "selection_filter" and rejected_signals:
                    try:
                        from .portfolio_protection import summarize_selection_rejects

                        _tid, _preason, _sel_lines = summarize_selection_rejects(rejected_signals)
                        if _sel_lines:
                            selection_lines_text = "\n" + "\n".join(
                                f"          • {line}" for line in _sel_lines
                            )
                    except Exception:
                        selection_lines_text = ""

                if rejection_category == "selection_filter":
                    reason_body = no_trades_reason or (
                        "Selection rejects (not Regime PP): execution pool empty after path filters."
                    )
                    if total_rejected > 0:
                        execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

Selection rejects <b>(not Regime PP)</b> cleared the SO pool ({total_rejected} symbol(s)).
{selection_lines_text}

📌 <b>Reason:</b>
          {reason_body}{rejected_text}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                    else:
                        execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

{reason_body}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                elif no_trades_reason or rejection_category == "portfolio_protection":
                    reason_body = no_trades_reason or (
                        "Blocked by Portfolio Protection (Regime) at 7:30 PT (no SO fills)."
                    )
                    if total_rejected > 0:
                        execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

Blocked by <b>Portfolio Protection (Regime)</b>.

📌 <b>Reason:</b>
          {reason_body}{rejected_text}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                    else:
                        execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

{reason_body}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                elif total_rejected > 0:
                    execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

All {total_rejected} signals were REJECTED due to insufficient capital.

💡 <b>Action Required:</b>
• Increase account balance for more trading capacity
• Review position sizing strategy
• Check risk management settings{rejected_text}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                else:
                    reason_text = no_trades_reason or "No qualifying SO signals remained at execution time."
                    execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

{reason_text}

📊 <b>System Status:</b> Ready for next trading opportunity"""
            
            # Build message (Rev 00180AE: Updated format with batch execution note)
            # Labels: "SO Signals Found" = ranked ORB SO pool entering 7:30 execution path.
            # filtered_expensive = count dropped by adaptive target / expense-ratio filter (not only "expensive" names).
            pool_total = so_count + len(rejected_signals or []) + filtered_expensive
            message = f"""====================================================================

🪽 <b>Standard Order Execution</b>
{_alert_mode_header_line(mode)}

📟 <b>ORB SO pipeline</b> <i>(collection 7:15–7:30 PT → batch execution 7:30 PT)</i>
          • <b>Symbols scanned (universe):</b> {total_scanned}
          • <b>SO signals in ranked pool:</b> {pool_total}
          • <b>Not selected for execution:</b> {filtered_expensive} <i>(SO adaptive filter: target size / fair-share vs price)</i>

{execution_section}

"""
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                log.info(f"SO execution aggregated alert sent - {so_count} trades from {total_scanned} symbols")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending SO execution aggregated alert: {e}")
            return False
    
    async def send_orb_capture_failed_alert(self, total_symbols: int,
                                            capture_time_seconds: float,
                                            mode: str = "DEMO") -> bool:
        """
        Send alert when ORB capture FAILS (0 symbols captured) - Rev 00180AE
        
        This indicates E*TRADE batch quotes returned empty (broker-only path; no yfinance fallback).
        
        Args:
            total_symbols: Total number of symbols attempted
            capture_time_seconds: Time spent attempting capture
            mode: Trading mode (DEMO or LIVE)
        
        Returns:
            True if alert sent successfully
        """
        try:
            from zoneinfo import ZoneInfo
            
            # Get current time
            pt_tz = ZoneInfo('America/Los_Angeles')
            et_tz = ZoneInfo('America/New_York')
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            
            pt_time = now_pt.strftime('%I:%M %p PT')
            et_time = now_et.strftime('%I:%M %p ET')
            
            # Mode emoji
            mode_emoji = _telegram_mode_emoji()
            
            # Build failure alert message
            message = f"""====================================================================

🔴 <b>ORB CAPTURE FAILED</b>
{_alert_mode_header_line(mode)}
⏰ Time: {pt_time} ({et_time})

❌ <b>Status: NO opening ranges captured!</b>

📊 <b>Capture Attempt:</b>
          • <b>Symbols Attempted:</b> {total_symbols}
          • <b>Symbols Captured:</b> 0
          • <b>Success Rate:</b> 0%
          • <b>Duration:</b> {capture_time_seconds:.1f} seconds

⚠️ <b>Issue:</b> E*TRADE OAuth rejected or batch quotes returned empty (check logs for token_rejected)

🔧 <b>Next Steps:</b>
          1. Renew production token: https://easy-trading-oauth-v2.web.app
          2. Confirm trading service reloaded tokens (OAuth webhook / 6:25 AM prep)
          3. Manual recovery: POST /api/manual-orb-capture?force=true

⚛️ <b>ORB Window:</b> 
          6:30-6:45 AM PT (9:30-9:45 AM ET)

🚨 Trading will be PAUSED until ORB data is available"""
            
            success = await self._send_telegram_message(message, AlertLevel.ERROR)
            
            if success:
                log.warning(f"ORB capture FAILED alert sent - 0/{total_symbols} symbols")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending ORB capture failed alert: {e}")
            return False
    
    async def send_orb_capture_complete_alert(
        self,
        symbols_captured: int,
        capture_time_seconds: float,
        filtered_count: int = 0,
        mode: str = "DEMO",
        spx_orb_data: Optional[Dict[str, Any]] = None,
        qqq_orb_data: Optional[Dict[str, Any]] = None,
        spy_orb_data: Optional[Dict[str, Any]] = None,
        dte_orb_data: Optional[Dict[str, Dict[str, Any]]] = None,
        orb_strategy_count: Optional[int] = None,
        dte_strategy_count: Optional[int] = None,
        *,
        phase: str = "orb1",
    ) -> bool:
        """
        Shared ORB capture-complete Telegram for ORB1 (6:45) and ORB2 (7:30).

        ``phase`` is ``orb1`` or ``orb2``. Legacy kwargs (spx/qqq/spy/dte counts)
        are accepted for call-site compatibility.
        """
        try:
            from zoneinfo import ZoneInfo

            _ = (spx_orb_data, qqq_orb_data, spy_orb_data, orb_strategy_count)
            phase_key = str(phase or "orb1").strip().lower()
            if phase_key in ("orb2", "2", "orb#2"):
                phase_key = "orb2"
            else:
                phase_key = "orb1"

            pt_tz = ZoneInfo("America/Los_Angeles")
            et_tz = ZoneInfo("America/New_York")
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            pt_time = now_pt.strftime("%I:%M %p PT")
            et_time = now_et.strftime("%I:%M %p ET")
            today = now_pt.date().isoformat()
            mode_label_upper = (mode or "DEMO").upper()

            if phase_key == "orb2":
                capture_n = int(symbols_captured or 0)
                active_n = max(0, capture_n - int(filtered_count or 0))
                title = "0DTE ORB2 Capture Complete"
                section = "0DTE ORB2 Capture"
                window = "6:30 - 7:30 AM PT (9:30 - 10:30 AM ET)"
                next_step = "ORB2 Breakout and Trendline Options"
                next_window = "7:30 - 11:30 AM PT (10:30 AM - 2:30 PM ET)"
                empty_status = "💢 No ORB2 Ranges Captured"
                log_label = "ORB2"
            else:
                active_symbols = int(symbols_captured or 0) - int(filtered_count or 0)
                if dte_strategy_count is not None:
                    dte_strategy_captured = dte_strategy_count
                elif dte_orb_data:
                    dte_strategy_captured = len(dte_orb_data)
                else:
                    dte_strategy_captured = symbols_captured
                dte_strategy_active = max(0, int(dte_strategy_captured) - int(filtered_count or 0))
                capture_n = dte_strategy_captured if dte_strategy_captured > 0 else symbols_captured
                active_n = dte_strategy_active if dte_strategy_captured > 0 else active_symbols
                title = "0DTE ORB1 Capture Complete"
                section = "0DTE ORB1 Capture"
                window = "6:30 - 6:45 AM PT (9:30 - 9:45 AM ET)"
                next_step = "ORB1 Breakouts"
                next_window = "6:50 - 7:10 AM PT (9:50 - 10:10 AM ET)"
                empty_status = "💢 No ORBs Captured"
                log_label = "ORB1"

            try:
                from easy0DTE.modules.prime_0dte_strategy_manager import load_0dte_symbols

                dte_watchlist_n = len(load_0dte_symbols())
                if int(capture_n or 0) > 0 and int(capture_n) != dte_watchlist_n:
                    log.warning(
                        "0DTE %s capture alert: captured %s != watchlist %s",
                        log_label,
                        capture_n,
                        dte_watchlist_n,
                    )
            except Exception as watchlist_err:
                log.debug("0DTE watchlist count check skipped (%s alert): %s", log_label, watchlist_err)

            if int(capture_n or 0) > 0:
                status_line = "✅ Status: All opening ranges captured successfully!"
                filtered_section = (
                    f"\n          • ⛔ <b>Filtered (Sideways):</b> {filtered_count}"
                    if filtered_count and int(filtered_count) > 0
                    else ""
                )
            else:
                status_line = empty_status
                filtered_section = ""

            message = f"""====================================================================

✅ <b>{title}</b>
{_alert_mode_header_line(mode_label_upper)}

{status_line}

🔮 <b>{section}:</b>
          • <b>Symbols Captured:</b> {capture_n}
          • <b>Active Symbols:</b> {active_n}{filtered_section}
          • <b>Capture Duration:</b> {float(capture_time_seconds):.1f} seconds

⚛️ <b>{log_label} Window:</b>
          {window}
🎯 <b>Next:</b> {next_step}
          {next_window}

📅 <b>Report Date:</b> {today}
          Time: {pt_time} ({et_time})
"""
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            if success:
                log.info(
                    "%s capture complete alert sent - %s symbols, %s active",
                    log_label,
                    capture_n,
                    active_n,
                )
            return success
        except Exception as e:
            log.error("Error sending ORB capture complete alert: %s", e)
            return False

    async def send_orb2_capture_complete_alert(
        self,
        symbols_captured: int,
        capture_time_seconds: float,
        filtered_count: int = 0,
        mode: str = "DEMO",
    ) -> bool:
        """ORB2 capture complete — shared capture contract with phase=orb2."""
        return await self.send_orb_capture_complete_alert(
            symbols_captured=symbols_captured,
            capture_time_seconds=capture_time_seconds,
            filtered_count=filtered_count,
            mode=mode,
            phase="orb2",
        )

    async def send_holiday_alert(self, holiday_name: str, skip_reason: str) -> bool:
        """
        Send alert on holidays (Rev 00087)
        
        Handles MARKET_CLOSED, LOW_VOLUME, and PRE_HOLIDAY skip sessions.
        Sent at 5:30 AM PT instead of Good Morning alert.
        
        Args:
            holiday_name: Name of the holiday (e.g., "Halloween", "Christmas Day")
            skip_reason: "MARKET_CLOSED", "LOW_VOLUME", or "PRE_HOLIDAY"
        
        Returns:
            True if alert sent successfully
        """
        try:
            from zoneinfo import ZoneInfo
            
            # Get current time
            pt_tz = ZoneInfo('America/Los_Angeles')
            et_tz = ZoneInfo('America/New_York')
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            
            day_name = now_et.strftime('%A, %B %d, %Y')
            
            emoji, title, reason_msg = _holiday_alert_copy(holiday_name, skip_reason)
            
            # Build message (user's exact format)
            message = f"""====================================================================

{emoji} <b>{title}</b>
          {day_name}

🎭 <b>No Trading Today!</b> ☁️🏖️🏝️⛱️🌤️☁️☁️

🚫 <b>Status:</b>
          Trading DISABLED today.

💡 <b>Why:</b>
          {reason_msg}

✅ <b>System Status:</b> 
          Normal

🔍 <b>Next Trading:</b> 
          System will resume at next normal trading day

🌐 <b>Dashboard:</b> 
          {_DESK_DASHBOARD_URL}

"""
            
            success = await self._send_telegram_message(message, AlertLevel.INFO)
            
            if success:
                log.info(f"Holiday alert sent - {holiday_name} ({skip_reason})")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending holiday alert: {e}")
            return False
    
    # NOTE (Rev 00180AE): Old send_end_of_day_report() removed - duplicate of line 454
    # The unified version at line 454 has account_balance parameter and updated formatting
    
    # ============================================================================================================================================
    # 0DTE STRATEGY ALERTS (Rev 00206)
    # ============================================================================================================================================
    
    def _orb_0dte_trade_quote_durable_footer_lines(
        self,
        position: Dict[str, Any],
        *,
        durable_display: str = "yes",
    ) -> str:
        """Per-trade footer aligned with Durable Confirmation Update (plain key=value lines + Durable)."""
        qg_raw = position.get("quote_grade")
        if qg_raw is None or str(qg_raw).strip() == "":
            qg_raw = position.get("execution_durable_quote_grade")
        if qg_raw is None or str(qg_raw).strip() == "":
            qg_raw = "degraded"
        qg = escape(str(qg_raw))

        qt_raw = position.get("quote_source")
        if qt_raw is None or str(qt_raw).strip() == "":
            qt_raw = position.get("execution_durable_quote_source")
        if qt_raw is None or str(qt_raw).strip() == "":
            qt_raw = "unknown"
        qt = escape(str(qt_raw))

        if "exit_grade_ready" in position:
            exit_ready = bool(position.get("exit_grade_ready"))
        else:
            exit_ready = bool(position.get("execution_durable_exit_grade_ready", False))
        warm_state = "ready" if exit_ready else "warming"
        dur_s = escape(str(durable_display))
        return (
            f"\n          Quote_grade={qg}\n"
            f"          Exit_grade_ready={'yes' if exit_ready else 'no'}\n"
            f"          Quote_src={qt}\n"
            f"          Exit-grade quotes={warm_state}\n"
            f"          Durable={dur_s}"
        )

    @staticmethod
    def _shorten_0dte_exec_diag_reason(reason: str) -> str:
        """Telegram-friendly wording for common 0DTE execution reject detail lines."""
        s = (reason or "").strip()
        if not s:
            return s
        needle = "No ITM probability spread found from chain/strike selector"
        idx = s.lower().find(needle.lower())
        if idx >= 0:
            return s[:idx] + "No ITM spread in chain/strike selector" + s[idx + len(needle) :]
        return s

    @staticmethod
    def _format_0dte_execution_diag_durable_row(sym: str, side: str, position_type: str, premium: float) -> str:
        """Multi-line durable_executed row for Options Execution diagnostics."""
        pt = escape(str(position_type or "—"))
        return (
            f"          • {sym} | {side} | <b>stage=durable_executed</b>\n"
            f"             result=ok | structure={pt}\n"
            f"             premium={_fmt_usd(premium)} | durable=<b>Stage A+</b>"
        )

    _POST_SELECTOR_REJECT_CODES = frozenset({
        "spread_too_wide",
        "option_spread_too_wide",
        "order_submit_failed",
        "execution_failed_no_position",
        "execution_risk_blocked",
        "insufficient_buying_power_or_clamps",
        "missing_bid_or_ask",
        "option_no_bid",
        "option_no_ask",
        "ask_size_too_small",
        "ask_size_missing",
        "bid_size_too_small",
        "bid_size_missing",
        "option_volume_missing",
        "option_open_interest_missing",
        "option_book_depth_missing",
        "underlying_adv_missing",
        "quote_stale",
        "option_quote_stale",
        "option_low_volume",
        "low_open_interest",
        "option_low_open_interest",  # legacy
        "option_premium_too_low",
        "option_too_expensive_for_budget",
        "option_live_chain_required",
        "option_liquidity_failed",
    })

    _POST_SELECTOR_RESULT_TAGS = frozenset({
        "entry_spread_reject",
        "broker_reject",
        "execution_reject",
        "risk_reject",
        "market_quality_reject",
    })

    @classmethod
    def _orb_0dte_is_post_selector_reject(
        cls, code: str, details: Optional[Dict[str, Any]] = None
    ) -> bool:
        code_lc = (code or "").strip().lower()
        if code_lc in cls._POST_SELECTOR_REJECT_CODES:
            return True
        det = details if isinstance(details, dict) else {}
        sel = str(det.get("selector_result") or "").strip().lower()
        return sel in cls._POST_SELECTOR_RESULT_TAGS

    @staticmethod
    def _orb_0dte_reject_display_reason(entry: Dict[str, Any]) -> str:
        """Prefer Premium/Delta selector summary over generic single-leg reject copy."""
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        code = str(entry.get("reason_code") or entry.get("reason") or "")
        summary = str(details.get("selector_reject_summary") or "").strip()
        if summary:
            return summary
        if PrimeAlertManager._orb_0dte_is_post_selector_reject(code, details):
            block = str(details.get("execution_block_reason") or "").strip()
            if block:
                return block
            return str(entry.get("reason") or code or "unknown")
        rr = str(details.get("reject_reason") or "").strip()
        generic_markers = (
            "no single-leg contract found",
            "no_lotto_contract",
            "liquidity_or_premium_filters",
            "single_leg_selection_failed",
        )
        if rr and not any(m in rr.lower() for m in generic_markers):
            return rr
        selector_diag = (
            details.get("selector_diagnostics")
            if isinstance(details.get("selector_diagnostics"), dict)
            else {}
        )
        if selector_diag:
            try:
                from modules.orb_0dte_opening_execution import summarize_single_leg_selector_reject

                computed = summarize_single_leg_selector_reject(selector_diag)
                if computed:
                    return computed
            except Exception:
                pass
        return str(entry.get("reason") or entry.get("reason_code") or "unknown")

    @staticmethod
    def _format_0dte_execution_detail_signal_line(symbol: str, reason_raw: str) -> str:
        score_part, detail = _split_0dte_convex_reason(reason_raw)
        if score_part:
            return (
                f"          • {symbol}: {escape(score_part)}\n"
                f"             {escape(_format_0dte_details_detail_line(detail))}"
            )
        return f"          • {symbol}: {escape(str(reason_raw))}"

    @staticmethod
    def _format_0dte_diag_lotto_meta_suffix(
        det: Dict[str, Any],
        *,
        code: str = "",
    ) -> str:
        """Multi-line lotto_first execution diagnostics for Symbol execution diagnostics."""
        if not (det.get("execution_structure") == "lotto_first" or det.get("orb_lotto_first")):
            return ""
        st730 = escape(str(det.get("strategy_type_at_730") or "—"))
        spread_primary = str(det.get("spread_primary_enabled", False)).lower()
        spread_fallback = str(det.get("spread_fallback_enabled", False)).lower()
        lines = [
            f"execution_structure=lotto_first | spread_primary={spread_primary}",
            f"strategy_type_at_730={st730} | spread_fallback={spread_fallback}",
        ]
        sel_res = str(det.get("selector_result") or "no_contract").strip()
        if sel_res:
            lines.append(f"selector_result={escape(sel_res)}")
        summary = str(det.get("selector_reject_summary") or "").strip()
        code_lc = (code or "").strip().lower()
        if summary and any(
            token in summary for token in ("Premium filter", "Delta filter", "Spread filter")
        ):
            rr_raw = summary
        elif code_lc:
            rr_raw = code_lc
        else:
            rr_raw = str(det.get("reject_reason") or "").strip()
        if rr_raw and rr_raw != "—":
            if " — " in rr_raw:
                left, right = rr_raw.split(" — ", 1)
                lines.append(f"reject_reason={escape(left.strip())}")
                lines.append(escape(right.strip()))
            else:
                lines.append(f"reject_reason={escape(rr_raw)}")
        return "\n             " + "\n             ".join(lines)

    @staticmethod
    def _format_0dte_execution_diag_reject_row(
        sym: str,
        side: str,
        code: str,
        reason_raw: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Multi-line pre_execution reject row; score and detail on separate lines."""
        code_lc = (code or "").strip().lower()
        option_side = escape(_orb_0dte_option_side_label(side))
        det = details if isinstance(details, dict) else {}
        post_selector = PrimeAlertManager._orb_0dte_is_post_selector_reject(code, det)
        if code_lc == "single_leg_selection_failed" and not post_selector:
            summary = str(det.get("selector_reject_summary") or "").strip()
            if summary:
                reason_raw = summary
        elif post_selector:
            summary = str(det.get("selector_reject_summary") or reason_raw or "").strip()
            if summary:
                reason_raw = summary
        score_part, detail_raw = _split_0dte_convex_reason(reason_raw)
        detail_line = escape(_format_0dte_diag_detail_line(detail_raw))
        code_e = escape(code)
        stage = "post_selector_execution" if post_selector else "pre_execution"
        head = f"          • {sym} | {option_side} | <b>stage={stage}</b>\n"
        lotto_suffix = ""
        meta_parts: List[str] = []
        if post_selector:
            sel_meta = escape(str(det.get("selector_result") or "—"))
            block_meta = escape(str(det.get("execution_block_reason") or "")[:80])
            meta_parts.append(f"selector_result={sel_meta}")
            if block_meta and block_meta != "—":
                meta_parts.append(f"execution_block={block_meta}")
        if det.get("execution_structure") == "lotto_first" or det.get("orb_lotto_first"):
            lotto_suffix = PrimeAlertManager._format_0dte_diag_lotto_meta_suffix(det, code=code)
        elif meta_parts:
            lotto_suffix = "\n             " + " | ".join(meta_parts)
        if code_lc == "selector_fallback_ladder_exhausted":
            reason = PrimeAlertManager._shorten_0dte_exec_diag_reason(reason_raw)
            r_esc = escape(reason)
            if r_esc:
                return (
                    head
                    + f"             result=reject | {code_e}\n"
                    + f"             {r_esc}"
                    + lotto_suffix
                )
        score_suffix = f" | {escape(score_part)}" if score_part else ""
        body = f"             result=reject | {code_e}{score_suffix}"
        if detail_line:
            body += f"\n             {detail_line}"
        elif not score_part:
            reason = escape(PrimeAlertManager._shorten_0dte_exec_diag_reason(reason_raw))
            body = f"             result=reject | {code_e} | {reason}"
        return head + body + lotto_suffix

    @staticmethod
    def _normalize_option_side(option_type: Any) -> str:
        """Normalize call/put for alert labels (handles CALL, Call, c, etc.)."""
        raw = str(option_type or "call").strip().lower()
        if raw.startswith("p"):
            return "put"
        return "call"

    @staticmethod
    def _orb_0dte_position_deployed_capital(position: Dict[str, Any]) -> float:
        """Actual capital at risk for display (premium/debit × contracts × 100)."""
        allocated = float(position.get("capital_allocated") or 0.0)
        if allocated > 0:
            return allocated
        qty = max(1, int(position.get("quantity") or 0))
        entry_price = float(position.get("entry_price") or 0.0)
        if entry_price > 0:
            return entry_price * qty * 100.0
        position_type = str(position.get("position_type") or "")
        if position_type == "lotto":
            lc = position.get("lotto_contract") if isinstance(position.get("lotto_contract"), dict) else {}
            prem = float(lc.get("mid_price") or lc.get("last") or lc.get("premium") or 0.0)
            if prem > 0:
                return prem * qty * 100.0
        if position_type == "debit_spread":
            ds = position.get("debit_spread") if isinstance(position.get("debit_spread"), dict) else {}
            debit = float(ds.get("debit_cost") or 0.0)
            if debit > 0:
                return debit * qty * 100.0
        if position_type == "credit_spread":
            cs = position.get("credit_spread") if isinstance(position.get("credit_spread"), dict) else {}
            margin = float(cs.get("margin_requirement") or cs.get("credit_received") or 0.0)
            if margin > 0:
                return margin * qty * 100.0
        return 0.0

    @staticmethod
    def _resolve_entry_account_equity(
        position: Dict[str, Any],
        *,
        account_equity_at_entry: Optional[float] = None,
        account_balance_fallback: Optional[float] = None,
        account_balance_post_close: Optional[float] = None,
        realized_pnl: Optional[float] = None,
    ) -> float:
        """Account equity at entry — cash plus open position cost (not cash-only ledger)."""
        for raw in (
            account_equity_at_entry,
            position.get("account_equity_at_entry"),
        ):
            if raw is not None:
                v = float(raw)
                if v > 0:
                    return v
        meta = position.get("metadata")
        if isinstance(meta, dict):
            stored = meta.get("account_equity_at_entry")
            if stored is not None:
                v = float(stored)
                if v > 0:
                    return v
        stored_bal = float(position.get("account_balance") or 0.0)
        if stored_bal > 0:
            return stored_bal
        if account_balance_fallback is not None:
            v = float(account_balance_fallback)
            if v > 0:
                return v
        if account_balance_post_close is not None and realized_pnl is not None:
            legacy_cash = float(account_balance_post_close) - float(realized_pnl)
            if legacy_cash > 0:
                return legacy_cash
        return 0.0

    @staticmethod
    def _deployed_capital_pct_of_equity(deployed: float, entry_equity: float) -> Optional[float]:
        if deployed <= 0 or entry_equity <= 0:
            return None
        return deployed / entry_equity * 100.0

    def _orb_0dte_execution_capital_pct(
        self,
        position: Dict[str, Any],
        *,
        account_balance_fallback: Optional[float] = None,
    ) -> float:
        entry_equity = self._resolve_entry_account_equity(
            position,
            account_balance_fallback=account_balance_fallback,
        )
        deployed = self._orb_0dte_position_deployed_capital(position)
        pct = self._deployed_capital_pct_of_equity(deployed, entry_equity)
        return float(pct) if pct is not None else 0.0

    def _options_exit_deployed_capital_pct(
        self,
        position: Dict[str, Any],
        *,
        deployed: float,
        account_balance_post_close: Optional[float] = None,
        realized_pnl: Optional[float] = None,
        account_balance_fallback: Optional[float] = None,
        account_equity_at_entry: Optional[float] = None,
    ) -> Optional[float]:
        """Capital % at entry — deployed capital as a fraction of account equity at entry."""
        if deployed <= 0:
            return None
        entry_equity = self._resolve_entry_account_equity(
            position,
            account_equity_at_entry=account_equity_at_entry,
            account_balance_fallback=account_balance_fallback,
            account_balance_post_close=account_balance_post_close,
            realized_pnl=realized_pnl,
        )
        return self._deployed_capital_pct_of_equity(deployed, entry_equity)

    @staticmethod
    def _format_exit_deployed_line(deployed: float, capital_pct: Optional[float]) -> str:
        if capital_pct is not None and capital_pct > 0:
            return f"{_fmt_usd(deployed)} deployed ({capital_pct:.1f}%)"
        return _fmt_usd(deployed)

    @staticmethod
    def _format_trendline_option_leg_label(quantity: int, symbol: str, option_side: str) -> str:
        """Qty • CALL/PUT on symbol — legacy leg label (prefer execution contract line)."""
        qty = max(0, int(quantity or 0))
        side = str(option_side or "N/A").upper()
        sym = str(symbol or "UNKNOWN")
        if side in ("PUT", "CALL"):
            return f"{_fmt_qty(qty)} • {side} on {sym}"
        return f"{_fmt_qty(qty)} • {sym} {side}"

    @staticmethod
    def _format_option_contract_line(
        quantity: int,
        symbol: str,
        option_side: str,
        entry_premium: float,
        *,
        lotto: bool = False,
    ) -> str:
        """Shared fill/close contract line: qty • SYM SIDE [@ Lotto] @ $prem x 100 ="""
        qty = max(0, int(quantity or 0))
        side = str(option_side or "N/A").upper()
        sym = str(symbol or "UNKNOWN")
        prem = float(entry_premium or 0.0)
        lotto_token = " Lotto" if lotto else ""
        if side in ("PUT", "CALL"):
            return f"{_fmt_qty(qty)} • {sym} {side}{lotto_token} @ {_fmt_usd(prem)} x 100 ="
        return f"{_fmt_qty(qty)} • {sym} {side}{lotto_token} @ {_fmt_usd(prem)} x 100 ="

    @staticmethod
    def _format_trendline_execution_contract_line(
        quantity: int,
        symbol: str,
        option_side: str,
        entry_premium: float,
    ) -> str:
        """Trendline fill — qty • SYM CALL/PUT @ premium x 100 ="""
        return PrimeAlertManager._format_option_contract_line(
            quantity, symbol, option_side, entry_premium, lotto=False
        )

    @staticmethod
    def _format_trendline_line_geometry_display(line_geometry: str) -> str:
        geom = str(line_geometry or "").strip().upper()
        if geom in ("BULL", "BEAR"):
            return f"{geom} Line"
        raw = str(line_geometry or "").strip()
        return raw if raw else "n/a"

    @staticmethod
    def _format_trendline_closed_contract_line(
        quantity: int,
        symbol: str,
        option_side: str,
        entry_premium: float,
    ) -> str:
        """Qty • SYM CALL/PUT @ entry premium x 100 = — close alerts."""
        return PrimeAlertManager._format_option_contract_line(
            quantity, symbol, option_side, entry_premium, lotto=False
        )

    @staticmethod
    def _format_orb_0dte_lotto_execution_contract_line(
        quantity: int,
        symbol: str,
        option_side: str,
        entry_premium: float,
    ) -> str:
        """ORB1 Breakout fill — qty • SYM CALL/PUT Lotto @ premium x 100 ="""
        return PrimeAlertManager._format_option_contract_line(
            quantity, symbol, option_side, entry_premium, lotto=True
        )

    @staticmethod
    def _format_orb_0dte_closed_contract_line(
        priority_rank: int,
        quantity: int,
        symbol: str,
        option_side: str,
        entry_premium: float,
    ) -> str:
        """ORB1 Breakout closed — qty • SYM CALL/PUT @ entry premium x 100 ="""
        # priority_rank kept for call-site compatibility; display always uses quantity.
        _ = priority_rank
        return PrimeAlertManager._format_option_contract_line(
            quantity, symbol, option_side, entry_premium, lotto=False
        )

    @staticmethod
    def _format_trendline_closed_exit_premium_line(
        entry_premium: float,
        exit_premium: float,
    ) -> str:
        entry_prem = float(entry_premium or 0.0)
        exit_prem = float(exit_premium or 0.0)
        net_per = exit_prem - entry_prem
        return f"Exit Premium: {_fmt_usd(exit_prem)} • Net: {_fmt_usd_signed(net_per)}"

    @staticmethod
    def _sort_0dte_executed_positions_by_rank(
        executed_positions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        return sorted(
            list(executed_positions or []),
            key=lambda p: (
                int(p.get("priority_rank") or 9999),
                str(p.get("symbol") or ""),
            ),
        )

    @staticmethod
    def _orb_0dte_execution_run_is_clean(
        *,
        executed_positions: List[Dict[str, Any]],
        rejected_signals: Optional[List[Dict[str, Any]]],
        true_failed_total: int,
        pending_confirmation_total: int,
        submitted_pending_confirmation_rows: Optional[List[Dict[str, Any]]],
        monitoring_active_not_yet_durable_rows: Optional[List[Dict[str, Any]]],
        durability_buckets: Optional[Dict[str, Any]],
    ) -> bool:
        if not executed_positions:
            return False
        if rejected_signals or int(true_failed_total or 0) > 0:
            return False
        if int(pending_confirmation_total or 0) > 0:
            return False
        if submitted_pending_confirmation_rows or monitoring_active_not_yet_durable_rows:
            return False
        db = durability_buckets if isinstance(durability_buckets, dict) else {}
        fail_keys = (
            "failed_before_selector",
            "failed_chain_health",
            "failed_selector",
            "failed_broker_submit",
            "durability_failed",
            "chain_health_failed",
            "selector_failed",
            "broker_submit_failed",
        )
        if any(int(db.get(k) or 0) > 0 for k in fail_keys):
            return False
        return True

    @staticmethod
    def _convex_pipeline_step_label() -> str:
        return "Quality Gate (pc_oi + ORB)"

    @staticmethod
    def _format_0dte_collapsed_pipeline_section(
        *,
        watchlist_n: Optional[int],
        collection_n: Optional[int],
        convex_n: Optional[int],
        executed_n: int,
        dropped_pre_gate: int = 0,
    ) -> str:
        wl = str(int(watchlist_n)) if watchlist_n is not None else "—"
        coll = str(int(collection_n)) if collection_n is not None else "—"
        conv = str(int(convex_n)) if convex_n is not None else "—"
        convex_word = PrimeAlertManager._convex_pipeline_step_label().split("(")[0].strip()
        tail = f" · {int(dropped_pre_gate)} dropped at {convex_word}" if int(dropped_pre_gate or 0) > 0 else ""
        return (
            f"🎙️ <b>{ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} pipeline (this run):</b>\n"
            f"          Pipeline: {wl} watchlist → {coll} collected → {conv} {convex_word} → "
            f"{int(executed_n)} executed clean{tail}"
        )

    @staticmethod
    def _delta_above_band_warning_line(abs_delta: float) -> str:
        try:
            from easy0DTE.modules.dte0_entry_selection import PATH_ORB_0DTE, delta_above_configured_band
        except ImportError:
            return ""
        if delta_above_configured_band(abs_delta, PATH_ORB_0DTE):
            return "\n          ⚠️ <b>DELTA ABOVE BAND</b>"
        return ""

    def _format_0dte_execution_trade_lines(
        self,
        executed_positions: List[Dict[str, Any]],
        start_index: int = 1,
        *,
        include_quote_durable_metrics: bool = True,
        account_balance_fallback: Optional[float] = None,
    ) -> List[str]:
        """
        Shared Telegram blocks for ORB1 Breakout executions — same layout for batch execution alerts
        and durability follow-ups so formatting stays consistent.
        """
        trade_lines: List[str] = []

        def _trade_id_and_footer(p: Dict[str, Any], pid: Any) -> str:
            tid = escape(_trade_id_text(pid))
            base = f"          <b>Trade ID:</b>\n          {tid}"
            if not include_quote_durable_metrics:
                return base
            return base + self._orb_0dte_trade_quote_durable_footer_lines(p, durable_display="yes")

        for offset, position in enumerate(executed_positions):
            i = start_index + offset
            symbol = position.get("symbol", "UNKNOWN")
            position_type = position.get("position_type", "debit_spread")
            debit_spread = position.get("debit_spread")
            credit_spread = position.get("credit_spread")
            lotto_contract = position.get("lotto_contract")
            entry_price = position.get("entry_price", 0.0)
            quantity = position.get("quantity", 1)
            position_id = position.get("position_id", "N/A")

            priority_rank = position.get("priority_rank", 0)
            priority_score = position.get("priority_score", 0.0)
            capital_pct = self._orb_0dte_execution_capital_pct(
                position,
                account_balance_fallback=account_balance_fallback,
            )

            confidence = position.get("confidence", 0.0)
            confidence_pct = int(confidence * 100) if confidence > 0 else 0

            if position_type == "debit_spread" and debit_spread:
                option_type = self._normalize_option_side(debit_spread.get("option_type", "call"))
                option_type_label = "CALL" if option_type == "call" else "PUT"
                direction_emoji = "🟢" if option_type == "call" else "🔴"
                long_strike = debit_spread.get("long_strike", 0.0)
                short_strike = debit_spread.get("short_strike", 0.0)
                debit_cost = debit_spread.get("debit_cost", 0.0)
                max_profit = debit_spread.get("max_profit", 0.0)

                long_contract = debit_spread.get("long_contract", {})
                short_contract = debit_spread.get("short_contract", {})
                long_price = long_contract.get("mid_price", 0.0) if isinstance(long_contract, dict) else 0.0
                short_price = short_contract.get("mid_price", 0.0) if isinstance(short_contract, dict) else 0.0
                long_delta = long_contract.get("delta", 0.0) if isinstance(long_contract, dict) else 0.0

                expiry = debit_spread.get("expiry", "")
                if expiry:
                    expiry_formatted = expiry.replace("-", "")[-6:] if len(expiry.replace("-", "")) >= 6 else "TODAY"
                else:
                    expiry_formatted = "TODAY"

                momentum_score = position.get("momentum_score", 0.0)
                underlying_price = float(position.get("underlying_price") or 0.0)
                delta_with_price = (
                    f"<b>{long_delta:.2f}</b> Delta @ <b>{_fmt_usd(underlying_price)}</b>"
                    if underlying_price > 0
                    else f"<b>{long_delta:.2f}</b> Delta"
                )

                priority_line = ""
                if priority_rank > 0:
                    priority_line = f"          <b>Rank #{priority_rank}</b> • Priority Score <b>{priority_score:.3f}</b>\n"

                confidence_momentum_line = ""
                if confidence_pct > 0 or momentum_score > 0:
                    parts = []
                    if confidence_pct > 0:
                        parts.append(f"<b>{confidence_pct}%</b> Confidence")
                    if momentum_score > 0:
                        parts.append(f"<b>{momentum_score:.0f}/100</b> Momentum")
                    confidence_momentum_line = " • ".join(parts) + "\n"

                trade_lines.append(
                    f"{i}) {direction_emoji} <b>{quantity}</b> • <b>{symbol} {option_type_label} Debit Spread</b>\n"
                    f"{priority_line}"
                    f"          {confidence_momentum_line}"
                    f"          {delta_with_price}\n"
                    f"          Long: {symbol} {expiry_formatted}{option_type[0].upper()}{long_strike:.0f} @ {_fmt_usd(long_price)}\n"
                    f"          Short: {symbol} {expiry_formatted}{option_type[0].upper()}{short_strike:.0f} @ {_fmt_usd(short_price)}\n"
                    f"          Net Debit: <b>{_fmt_usd(debit_cost)}</b> • Max Profit: <b>{_fmt_usd(max_profit)}</b>\n"
                    f"          {capital_pct:.1f}% Of Account\n"
                    + _trade_id_and_footer(position, position_id)
                )
            elif position_type == "credit_spread" and credit_spread:
                option_type = self._normalize_option_side(credit_spread.get("option_type", "put"))
                option_type_label = "PUT" if option_type == "put" else "CALL"
                direction_emoji = "🔴" if option_type == "put" else "🟢"
                short_strike = credit_spread.get("short_strike", 0.0)
                long_strike = credit_spread.get("long_strike", 0.0)
                credit_received = credit_spread.get("credit_received", 0.0)
                max_profit = credit_spread.get("max_profit", 0.0)

                short_contract = credit_spread.get("short_contract", {})
                long_contract = credit_spread.get("long_contract", {})
                short_price = short_contract.get("mid_price", 0.0) if isinstance(short_contract, dict) else 0.0
                long_price = long_contract.get("mid_price", 0.0) if isinstance(long_contract, dict) else 0.0
                short_delta = short_contract.get("delta", 0.0) if isinstance(short_contract, dict) else 0.0

                expiry = credit_spread.get("expiry", "")
                if expiry:
                    expiry_formatted = expiry.replace("-", "")[-6:] if len(expiry.replace("-", "")) >= 6 else "TODAY"
                else:
                    expiry_formatted = "TODAY"

                momentum_score = position.get("momentum_score", 0.0)
                underlying_price = float(position.get("underlying_price") or 0.0)
                delta_with_price = (
                    f"<b>{short_delta:.2f}</b> Delta @ <b>{_fmt_usd(underlying_price)}</b>"
                    if underlying_price > 0
                    else f"<b>{short_delta:.2f}</b> Delta"
                )

                priority_line = ""
                if priority_rank > 0:
                    priority_line = f"          <b>Rank #{priority_rank}</b> • Priority Score <b>{priority_score:.3f}</b>\n"

                confidence_momentum_line = ""
                if confidence_pct > 0 or momentum_score > 0:
                    parts = []
                    if confidence_pct > 0:
                        parts.append(f"<b>{confidence_pct}%</b> Confidence")
                    if momentum_score > 0:
                        parts.append(f"<b>{momentum_score:.0f}/100</b> Momentum")
                    confidence_momentum_line = " • ".join(parts) + "\n"

                trade_lines.append(
                    f"{i}) {direction_emoji} <b>{quantity}</b> • <b>{symbol} {option_type_label} Credit Spread</b>\n"
                    f"{priority_line}"
                    f"          {confidence_momentum_line}"
                    f"          {delta_with_price}\n"
                    f"          Short: {symbol} {expiry_formatted}{option_type[0].upper()}{short_strike:.0f} @ {_fmt_usd(short_price)}\n"
                    f"          Long: {symbol} {expiry_formatted}{option_type[0].upper()}{long_strike:.0f} @ {_fmt_usd(long_price)}\n"
                    f"          Net Credit: <b>{_fmt_usd(credit_received)}</b> • Max Profit: <b>{_fmt_usd(max_profit)}</b>\n"
                    f"          {capital_pct:.1f}% Of Account\n"
                    + _trade_id_and_footer(position, position_id)
                )
            elif position_type == "lotto" and lotto_contract:
                option_type = self._normalize_option_side(lotto_contract.get("option_type", "call"))
                option_type_label = "CALL" if option_type == "call" else "PUT"
                direction_emoji = "🟢" if option_type == "call" else "🔴"

                premium = float(
                    lotto_contract.get("mid_price")
                    or lotto_contract.get("last")
                    or lotto_contract.get("premium")
                    or entry_price
                    or 0.0
                )
                display_delta = float(
                    lotto_contract.get("delta")
                    or position.get("selected_delta")
                    or 0.0
                )
                signed_delta = float(display_delta)
                if option_type == "put":
                    signed_delta = -abs(float(display_delta))

                deployed_usd = self._orb_0dte_position_deployed_capital(position)
                delta_warn = self._delta_above_band_warning_line(abs(float(display_delta)))
                contract_line = self._format_orb_0dte_lotto_execution_contract_line(
                    int(quantity or 0),
                    str(symbol),
                    option_type_label,
                    premium,
                )

                trade_lines.append(
                    f"{i}) {direction_emoji} <b>{escape(contract_line)}</b>\n"
                    f"          {_fmt_usd(deployed_usd)} deployed ({capital_pct:.1f}%)\n"
                    f"          Δ {signed_delta:.2f}{delta_warn}\n"
                    f"          Trade ID: {escape(_trade_id_text(position_id))}"
                )
            else:
                trade_lines.append(
                    f"{i}) ⚪ <b>{quantity}</b> • <b>{symbol}</b> ({position_type})\n"
                    f"          Entry: <b>{_fmt_usd(float(entry_price or 0))}</b> • Qty: <b>{quantity}</b>\n"
                    + _trade_id_and_footer(position, position_id)
                )
        return trade_lines
    
    async def send_options_execution_alert(self, executed_positions: List[Dict[str, Any]],
                                          total_capital_deployed: float,
                                          account_balance: float = 1000.0,
                                          mode: str = "DEMO",
                                          rejected_signals: Optional[List[Dict[str, Any]]] = None,
                                          failed_count: int = 0,
                                          hard_gated_symbols: Optional[List[Dict[str, Any]]] = None,
                                          dte_symbols_count: Optional[int] = None,  # watchlist; backward compat
                                          dte_options_found: Optional[int] = None,  # collection rows; backward compat
                                          convex_rejected_count: int = 0,
                                          dte_watchlist_count: Optional[int] = None,
                                          dte_collection_underlying_count: Optional[int] = None,
                                          dte_convex_qualified_count: Optional[int] = None,
                                          dte_hard_gate_execution_count: Optional[int] = None,
                                          dte_queued_for_execution_count: Optional[int] = None,
                                          dte_execution_attempted_count: Optional[int] = None,
                                          execution_metrics: Optional[Dict[str, Any]] = None,
                                          durability_buckets: Optional[Dict[str, Any]] = None,
                                          submitted_pending_confirmation_rows: Optional[List[Dict[str, Any]]] = None,
                                          monitoring_active_not_yet_durable_rows: Optional[List[Dict[str, Any]]] = None,
                                          no_trades_reason: Optional[str] = None,
                                          rejection_category: Optional[str] = None,
                                          portfolio_protection_pool_count: Optional[int] = None) -> bool:
        """
        Send 0DTE Options Execution alert (Rev 00230: Enhanced with momentum and strategy details)
        
        Shows complete execution details including:
        - Momentum scores for each trade
        - Strategy types (Long Call/Put, Debit Spread, ITM Prob, Lotto, etc.)
        - Priority ranking
        - Hard Gate status (all executed trades passed)
        
        Args:
            executed_positions: List of executed OptionsPosition objects with trade details
            total_capital_deployed: Total capital deployed in options trades
            account_balance: Current account balance
            mode: Trading mode (DEMO or LIVE)
            rejected_signals: Optional list of rejected signals (insufficient capital, etc.)
            failed_count: Number of trades that failed to execute
            hard_gated_symbols: Optional list of Hard Gated symbols
            dte_symbols_count: Watchlist size (0dte_list.csv)
            dte_options_found: Collection underlyings (CALL+PUT rows from scan)
            dte_collection_underlying_count: Same as collection rows when passed explicitly
            dte_convex_qualified_count: After Convex / strategy filter
            dte_hard_gate_execution_count: Queued for Step 5 after Hard Gate
            dte_execution_attempted_count: Execution loop size (chain + momentum gates)
        
        Returns:
            True if alert sent successfully
        """
        try:
            # Strategy type labels for display (Rev 00230)
            strategy_labels = {
                'long_call': 'Long Call',
                'long_put': 'Long Put',
                'debit_spread': 'Debit Spread',
                'itm_probability_spread': 'ITM Prob',
                'lotto': 'Lotto',
                'no_trade': 'NO TRADE',
                'momentum_scalper': 'Scalper'
            }
            
            # Mode emoji
            mode_emoji = _telegram_mode_emoji()

            db_pre = durability_buckets if isinstance(durability_buckets, dict) else {}
            submitted_pending_confirmation_rows = (
                submitted_pending_confirmation_rows if isinstance(submitted_pending_confirmation_rows, list) else []
            )
            monitoring_active_not_yet_durable_rows = (
                monitoring_active_not_yet_durable_rows if isinstance(monitoring_active_not_yet_durable_rows, list) else []
            )
            filtered_count_pre = len(rejected_signals) if rejected_signals else 0
            execution_attempts_total_pre = int(
                db_pre.get("execution_attempts_total")
                if db_pre.get("execution_attempts_total") is not None
                else (dte_execution_attempted_count or 0)
            )
            true_failed_total_pre = int(
                db_pre.get("true_failed_total")
                if db_pre.get("true_failed_total") is not None
                else filtered_count_pre
            )
            pending_confirmation_total_pre = int(
                db_pre.get("pending_confirmation_total")
                if db_pre.get("pending_confirmation_total") is not None
                else (len(submitted_pending_confirmation_rows) + len(monitoring_active_not_yet_durable_rows))
            )
            executed_sorted = self._sort_0dte_executed_positions_by_rank(executed_positions)
            clean_run = self._orb_0dte_execution_run_is_clean(
                executed_positions=executed_sorted,
                rejected_signals=rejected_signals,
                true_failed_total=true_failed_total_pre,
                pending_confirmation_total=pending_confirmation_total_pre,
                submitted_pending_confirmation_rows=submitted_pending_confirmation_rows,
                monitoring_active_not_yet_durable_rows=monitoring_active_not_yet_durable_rows,
                durability_buckets=db_pre,
            )

            trade_lines = self._format_0dte_execution_trade_lines(
                executed_sorted,
                1,
                include_quote_durable_metrics=not clean_run,
                account_balance_fallback=account_balance,
            )
            trades_text = "\n\n".join(trade_lines) if trade_lines else "          <i>No trades executed</i>\n"
            
            # Calculate deployment percentage
            deployment_pct = (total_capital_deployed / account_balance * 100) if account_balance > 0 else 0.0
            
            # Rev 00230: Build 0DTE Summary section at the top (with canonical counters)
            filtered_count = filtered_count_pre
            db = db_pre
            submitted_pending_confirmation_rows = submitted_pending_confirmation_rows
            monitoring_active_not_yet_durable_rows = monitoring_active_not_yet_durable_rows
            execution_attempts_total = int(
                db.get("execution_attempts_total")
                if db.get("execution_attempts_total") is not None
                else (dte_execution_attempted_count or 0)
            )
            submitted_total = int(
                db.get("submitted_total")
                if db.get("submitted_total") is not None
                else (len(executed_positions) + len(submitted_pending_confirmation_rows) + len(monitoring_active_not_yet_durable_rows))
            )
            true_failed_total = int(
                db.get("true_failed_total")
                if db.get("true_failed_total") is not None
                else (filtered_count + int(db.get("durability_failed") or 0))
            )
            pending_confirmation_total = int(
                db.get("pending_confirmation_total")
                if db.get("pending_confirmation_total") is not None
                else (len(submitted_pending_confirmation_rows) + len(monitoring_active_not_yet_durable_rows))
            )
            
            # Calculate average momentum score
            momentum_scores = [p.get('momentum_score', 0.0) for p in executed_sorted if p.get('momentum_score', 0.0) > 0]
            avg_momentum = sum(momentum_scores) / len(momentum_scores) if momentum_scores else 0.0
            
            # Count strategy types
            strategy_counts = {}
            for p in executed_sorted:
                st = p.get('strategy_type', 'debit_spread')
                strategy_counts[st] = strategy_counts.get(st, 0) + 1
            
            strategy_summary = ""
            if strategy_counts:
                strategy_summary = ", ".join([f"{v}x {strategy_labels.get(k, k)}" for k, v in strategy_counts.items()])
            
            def _fmt_ci(n: Optional[int]) -> str:
                return str(int(n)) if n is not None else "—"
            
            wl_disp = dte_watchlist_count if dte_watchlist_count is not None else dte_symbols_count
            coll_disp = dte_collection_underlying_count if dte_collection_underlying_count is not None else dte_options_found
            if coll_disp is None:
                coll_disp = len(executed_sorted) + filtered_count

            if clean_run and len(executed_sorted) > 0:
                summary_section = self._format_0dte_collapsed_pipeline_section(
                    watchlist_n=wl_disp,
                    collection_n=coll_disp,
                    convex_n=dte_convex_qualified_count,
                    executed_n=len(executed_sorted),
                    dropped_pre_gate=max(0, convex_rejected_count),
                )
            else:
                _convex_n = int(dte_convex_qualified_count or 0)
                _hard_gate_n = int(dte_hard_gate_execution_count or 0)
                _queued_n = db.get("queued_for_execution")
                if _queued_n is None:
                    _queued_n = dte_queued_for_execution_count
                if _queued_n is None:
                    _queued_n = _hard_gate_n
                _queued_n = int(_queued_n or 0)
                _dropped_convex = max(0, int(convex_rejected_count or 0))
                _dropped_after_hard_gate = max(0, (_hard_gate_n - _queued_n) + (_convex_n - _hard_gate_n))
                _dropped_after_hard_gate_line = ""
                if _dropped_after_hard_gate > 0:
                    _dropped_after_hard_gate_line = (
                        f"\n          • <b>Dropped after Hard Gate</b>:\n"
                        f"            (rank + Gate 3c cap): {_dropped_after_hard_gate}"
                    )
                _convex_step = self._convex_pipeline_step_label()
                summary_section = f"""🎙️ <b>{ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): {_fmt_ci(wl_disp)}
          • <b>Collection CALL+PUT</b>: {_fmt_ci(coll_disp)}
          • <b>{_convex_step}</b> (strategy filter): {_fmt_ci(dte_convex_qualified_count)}
          • <b>Hard gate → execution queue</b>: {_fmt_ci(dte_hard_gate_execution_count)}
          • <b>Queued for execution</b>: {_fmt_ci(_queued_n)}
          • <b>Selector loop (sized)</b>: {_fmt_ci(db.get("selector_loop_n"))}
          • <b>Broker routing</b> (selector): {_fmt_ci(db.get("attempted_selector") if db.get("attempted_selector") is not None else execution_attempts_total)}
          • <b>Broker submitted</b>: {_fmt_ci(db.get("broker_submitted") if db.get("broker_submitted") is not None else submitted_total)}
          • <b>Execution durable</b>: {_fmt_ci(db.get("execution_durable") if db.get("execution_durable") is not None else len(executed_sorted))}
          • <b>Exit-grade durable</b>: {_fmt_ci(db.get("exit_grade_durable"))}
          • <b>Failed before selector</b>: {_fmt_ci(db.get("failed_before_selector"))}
          • <b>Failed chain health</b>: {_fmt_ci(db.get("failed_chain_health"))}
          • <b>Failed selector</b>: {_fmt_ci(db.get("failed_selector"))}
          • <b>Failed broker submit</b>: {_fmt_ci(db.get("failed_broker_submit"))}
          • <b>Execution attempts</b>: {execution_attempts_total}
          • <b>Broker accepted total</b>: {submitted_total}
          • <b>Pending confirmation</b>: {pending_confirmation_total}
          • <b>Dropped at Quality Gate</b>: {_dropped_convex}{_dropped_after_hard_gate_line}
          • <b>Execution failed/rejected</b>: {true_failed_total}"""
            
            if avg_momentum > 0:
                summary_section += f"\n          • <b>Avg Momentum:</b> {avg_momentum:.0f}/100"

            reason_breakdown_section = ""
            chain_health_histogram_section = ""
            metrics = execution_metrics if isinstance(execution_metrics, dict) else {}
            lotto_first_mode_section = ""
            if metrics.get("orb_lotto_first") or metrics.get("execution_structure") == "lotto_first":
                lotto_first_mode_section = (
                    "\n\n🎯 <b>ORB1 Breakout execution mode</b>:\n"
                    f"          • <b>execution_structure</b>: lotto_first\n"
                    f"          • <b>spread_primary_enabled</b>: "
                    f"{str(bool(metrics.get('orb_spread_primary_enabled'))).lower()}\n"
                    f"          • <b>spread_fallback_enabled</b>: "
                    f"{str(bool(metrics.get('orb_spread_fallback_enabled'))).lower()}"
                )
            chain_hist = metrics.get("chain_health_failures_by_type") if isinstance(metrics.get("chain_health_failures_by_type"), dict) else {}
            hist_order = [
                "no_chain_data",
                "empty_chain",
                "no_valid_strikes",
                "spread_too_wide",
                "low_liquidity",
                "API_error",
                "timeout",
            ]
            thin_pass_n = int(metrics.get("chain_health_thin_tradable_pass_count", 0) or 0)
            if chain_hist or thin_pass_n > 0:
                hist_lines = []
                if thin_pass_n > 0:
                    hist_lines.append(f"          • <b>thin_tradable_relaxed_pass</b>: {thin_pass_n}")
                for key in hist_order:
                    if int(chain_hist.get(key, 0) or 0) > 0:
                        hist_lines.append(f"          • <b>{escape(key)}</b>: {int(chain_hist.get(key, 0))}")
                if int(chain_hist.get("unknown", 0) or 0) > 0:
                    hist_lines.append(f"          • <b>unknown</b>: {int(chain_hist.get('unknown', 0))}")
                if hist_lines:
                    chain_health_histogram_section = (
                        "\n\n🧪 <b>Chain health diagnostics</b>:\n"
                        f"          • <b>Retry count</b>: {int(metrics.get('chain_health_fast_retry_count', 0) or 0)}\n"
                        f"          • <b>Relaxed pass count</b>: {int(metrics.get('chain_health_relaxed_pass_count', 0) or 0)}\n"
                        f"          • <b>Extension rejects</b>: {int(metrics.get('prequeue_extension_reject_count', 0) or 0)}\n"
                        f"          • <b>Execution count</b>: {len(executed_positions)}\n"
                        + "\n".join(hist_lines)
                    )

            if rejected_signals:
                buckets: Dict[str, Dict[str, Any]] = {}
                for entry in rejected_signals:
                    code = str(entry.get('reason_code') or entry.get('reason') or 'unknown')
                    reason_raw = str(entry.get('reason', code))
                    details = entry.get('details') if isinstance(entry.get('details'), dict) else {}
                    selector_diag = details.get('selector_diagnostics') if isinstance(details.get('selector_diagnostics'), dict) else {}
                    selector = details.get('selector') if isinstance(details.get('selector'), str) else selector_diag.get('selector')
                    failure_reason = selector_diag.get('failure_reason') if isinstance(selector_diag.get('failure_reason'), str) else None
                    profile = selector_diag.get('strictness_profile') if isinstance(selector_diag.get('strictness_profile'), str) else None
                    if code == 'spread_selection_failed' and selector:
                        reason_raw = f"{reason_raw} [{selector}{':' + failure_reason if failure_reason else ''}]"
                    elif code == 'poor_payoff_profile' and profile:
                        reason_raw = f"{reason_raw} [profile={profile}]"
                    if code in ('no_contracts', 'hard_gate'):
                        bucket_key = f"{code}:{reason_raw}"
                    else:
                        bucket_key = code
                    bucket = buckets.setdefault(bucket_key, {
                        'code': code,
                        'reason_text': reason_raw,
                        'symbols': []
                    })
                    sym = entry.get('symbol', 'UNKNOWN')
                    if sym not in bucket['symbols']:
                        bucket['symbols'].append(sym)

                lines = []
                for info in sorted(
                    buckets.values(),
                    key=lambda x: len(x.get('symbols', [])),
                    reverse=True
                )[:5]:
                    syms = info.get('symbols', [])
                    sample = ", ".join(escape(str(s)) for s in syms[:4])
                    more = f" … +{len(syms)-4}" if len(syms) > 4 else ""
                    code_e = escape(str(info.get('code', 'unknown')))
                    cnt = len(syms)
                    if sample:
                        lines.append(
                            f"          • <b>{code_e}</b>: {cnt}\n"
                            f"            (e.g. {sample}{more})"
                        )
                    else:
                        lines.append(f"          • <b>{code_e}</b>: {cnt}")
                if lines:
                    reason_breakdown_section = (
                        "\n\n📉 <b>Top execution reject reasons</b>:\n" + "\n".join(lines)
                    )
            
            # Build execution section
            pending_lines = []
            for entry in (monitoring_active_not_yet_durable_rows + submitted_pending_confirmation_rows)[:10]:
                sym = escape(str(entry.get("symbol", "UNKNOWN")))
                tid = escape(_trade_id_text(entry.get("position_id", "N/A")))
                state = escape(str(entry.get("monitor_state", entry.get("prior_state", "pending"))))
                qty = int(entry.get("quantity", 0) or 0)
                entry_premium = float(entry.get("entry_price", 0.0) or 0.0)
                pending_lines.append(
                    f"          - {sym} ({tid}) — {state}"
                    + (f" • qty={qty}" if qty > 0 else "")
                    + (f" • entry premium={_fmt_usd(entry_premium)}" if entry_premium > 0 else "")
                )
            pending_block = ""
            if pending_lines:
                pending_block = "\n\n⏳ <b>Submitted, awaiting durable confirmation</b>:\n" + "\n".join(pending_lines)

            diag_lines: List[str] = []
            if not clean_run:
                for p in executed_sorted or []:
                    if not isinstance(p, dict):
                        continue
                    sym = escape(str(p.get("symbol", "UNKNOWN")))
                    side = escape(str(p.get("direction", p.get("option_type", "—"))))
                    prem = float(p.get("entry_price", 0) or 0)
                    diag_lines.append(
                        self._format_0dte_execution_diag_durable_row(sym, side, str(p.get("position_type", "—")), prem)
                    )
                for entry in rejected_signals or []:
                    if not isinstance(entry, dict):
                        continue
                    sym = escape(str(entry.get("symbol", "UNKNOWN")))
                    side = str(entry.get("direction", "—"))
                    code = str(entry.get("reason_code", "rejected"))
                    reason_raw = self._orb_0dte_reject_display_reason(entry)[:500]
                    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
                    liq = details.get("liquidity_class") or entry.get("liquidity_class")
                    extras: List[str] = []
                    if liq:
                        extras.append(f"liq={liq}")
                    if details.get("opening_selector_active") or entry.get("opening_selector_active"):
                        extras.append("opening_sel=on")
                    if details.get("deferred_retry") or entry.get("deferred_retry"):
                        extras.append("deferred_retry=yes")
                    if details.get("degraded_tier") or entry.get("degraded_tier"):
                        extras.append("degraded=yes")
                    audit = details.get("selector_audit")
                    if isinstance(audit, list) and audit:
                        last = audit[-1] if isinstance(audit[-1], dict) else {}
                        near = (last.get("details") or {}).get("nearest_contracts")
                        if isinstance(near, list) and near:
                            nc = near[0]
                            if isinstance(nc, dict):
                                extras.append(
                                    "best_reject="
                                    + f"{nc.get('strike')}δ{nc.get('delta')} "
                                    f"sp{nc.get('spread_pct')}%"
                                )
                    if extras:
                        reason_raw = f"{reason_raw} [{' | '.join(extras)}]"
                    diag_lines.append(
                        self._format_0dte_execution_diag_reject_row(
                            sym, side, code, reason_raw, details
                        )
                    )
                for entry in (monitoring_active_not_yet_durable_rows + submitted_pending_confirmation_rows):
                    if not isinstance(entry, dict):
                        continue
                    sym = escape(str(entry.get("symbol", "UNKNOWN")))
                    st = escape(str(entry.get("monitor_state", entry.get("prior_state", "pending"))))
                    rc = escape(str(entry.get("reason_code", "pending")))
                    reason = escape(str(entry.get("reason", ""))[:120])
                    diag_lines.append(
                        f"          • {sym} | — | <b>stage={st}</b>\n"
                        f"             result=pending | {rc} | {reason}"
                    )
            pipeline_diag_block = ""
            if diag_lines:
                _diag_cap = 12 if len(executed_positions) == 0 else 36
                pipeline_diag_block = (
                    "\n\n📋 <b>Symbol execution diagnostics</b>:\n"
                    + "\n".join(diag_lines[:_diag_cap])
                )
                if len(diag_lines) > _diag_cap:
                    pipeline_diag_block += f"\n          <i>… +{len(diag_lines) - _diag_cap} more</i>"

            if len(executed_sorted) > 0:
                quote_summary = ""
                if clean_run:
                    quote_summary = "\n\n📡 <b>Quote:</b> exact · exit-grade warming normal at entry"
                execution_section = f"""💼 <b>{ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} — options executed:</b> {len(executed_sorted)}

{trades_text}{quote_summary}

💰 <b>Capital Deployment:</b>
          • <b>Deployed:</b> {_fmt_usd(total_capital_deployed)} / {_fmt_usd(account_balance)} ({deployment_pct:.1f}%)

🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~2s + shared backup){pending_block}{lotto_first_mode_section}{reason_breakdown_section}{chain_health_histogram_section}"""
            else:
                # No durable executions yet — distinguish timing lag vs true rejects.
                total_rejected = len(rejected_signals) if rejected_signals else 0
                
                reason_summary_lines = []
                symbol_reason_lines = []
                
                if rejected_signals:
                    # Group by reason_code when available, otherwise by reason text
                    buckets: Dict[str, Dict[str, Any]] = {}
                    for entry in rejected_signals:
                        code = entry.get('reason_code') or entry.get('reason') or 'unknown'
                        reason_raw = str(entry.get('reason', code))
                        # Keep "no contracts" and hard-gate reasons direction-specific.
                        # Otherwise "No put contracts found" can be merged under a prior
                        # "No call contracts found" bucket via shared reason_code.
                        if code in ('no_contracts', 'hard_gate'):
                            bucket_key = f"{code}:{reason_raw}"
                        else:
                            bucket_key = code
                        bucket = buckets.setdefault(bucket_key, {
                            'reason_text': reason_raw,
                            'symbols': []
                        })
                        sym = entry.get('symbol', 'UNKNOWN')
                        if sym not in bucket['symbols']:
                            bucket['symbols'].append(sym)
                    
                    # Build compact summary per reason
                    for code, info in buckets.items():
                        symbols = info['symbols']
                        reason_text = escape(str(info['reason_text']))
                        count = len(symbols)
                        sample = ", ".join(escape(str(s)) for s in symbols[:5])
                        more_suffix = f" … +{count - 5} more" if count > 5 else ""
                        reason_summary_lines.append(
                            f"• <b>{reason_text}</b>: {count} symbols"
                            + (f" (e.g. {sample}{more_suffix})" if sample else "")
                        )
                    
                    # Optional detailed per-symbol list (first N) for deep diagnosis
                    max_detailed = 15
                    seen_symbol_reason = set()
                    for entry in rejected_signals:
                        symbol_raw = entry.get('symbol', 'UNKNOWN')
                        reason_raw = self._orb_0dte_reject_display_reason(entry)
                        dedupe_key = (str(symbol_raw), str(reason_raw))
                        if dedupe_key in seen_symbol_reason:
                            continue
                        seen_symbol_reason.add(dedupe_key)

                        symbol = escape(str(symbol_raw))
                        symbol_reason_lines.append(
                            self._format_0dte_execution_detail_signal_line(symbol, str(reason_raw))
                        )
                        if len(symbol_reason_lines) >= max_detailed:
                            break
                
                reasons_block = "\n".join(reason_summary_lines) if reason_summary_lines else "• No rejection details were recorded."
                detailed_block = "\n".join(symbol_reason_lines) if symbol_reason_lines else ""
                if detailed_block:
                    detailed_block = f"\n\n🔍 <b>Details (first {len(symbol_reason_lines)} signals):</b>\n{detailed_block}"
                
                if pending_confirmation_total > 0:
                    execution_section = f"""⏳ <b>ORB1 Breakout orders submitted; awaiting durable confirmation</b>

<b>Execution attempts:</b> {execution_attempts_total}
<b>Broker accepted total:</b> {submitted_total}
<b>Pending confirmation:</b> {pending_confirmation_total}

💡 <b>True rejection reasons (so far):</b>
{reasons_block}{detailed_block}{pending_block}{lotto_first_mode_section}{chain_health_histogram_section}

📊 <b>System status:</b> Monitoring active positions; durable confirmation pending"""
                elif (
                    no_trades_reason
                    and execution_attempts_total == 0
                    and len(executed_positions) == 0
                ):
                    reason_text = escape(str(no_trades_reason))
                    detail_suffix = ""
                    if reasons_block and reasons_block != "• No rejection details were recorded.":
                        detail_suffix = (
                            f"\n\n💡 <b>Rejection details:</b>\n{reasons_block}{detailed_block}"
                        )
                    _queued_n = dte_queued_for_execution_count
                    if _queued_n is None:
                        _queued_n = dte_hard_gate_execution_count
                    _pp_batch_block = (
                        rejection_category == "portfolio_protection"
                        and not (_queued_n and int(_queued_n) > 0)
                    )
                    if _pp_batch_block:
                        _pp_n = portfolio_protection_pool_count
                        if _pp_n is None:
                            _pp_n = coll_disp if isinstance(coll_disp, int) else 0
                        if _pp_n and _pp_n > 0:
                            execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

All {_pp_n} signals were REJECTED due to Portfolio Protection trigger.

📌 <b>Reason:</b>
          {reason_text}{detail_suffix}{lotto_first_mode_section}{chain_health_histogram_section}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                        else:
                            execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

{reason_text}{detail_suffix}{lotto_first_mode_section}{chain_health_histogram_section}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                    else:
                        execution_section = f"""⚠️ <b>NO TRADES EXECUTED</b>

{reason_text}{detail_suffix}{lotto_first_mode_section}{chain_health_histogram_section}

📊 <b>System Status:</b> Ready for next trading opportunity"""
                else:
                    execution_section = f"""⚠️ <b>NO {ALERT_LABEL_EASY_ORB_0DTE_OPTIONS.upper()} OPTIONS TRADES EXECUTED</b>

<b>Execution attempts:</b> {execution_attempts_total} (each line below is one queued signal that hit chain/momentum/strategy gates).

All {true_failed_total} attempts were rejected or failed.

💡 <b>Actual rejection reasons:</b>
{reasons_block}{detailed_block}{lotto_first_mode_section}{chain_health_histogram_section}

📊 <b>System status:</b> Ready for next trading opportunity"""
            
            message = f"""====================================================================

{_orb_0dte_execution_alert_header_block(mode)}

{summary_section}

{execution_section}
"""
            if pipeline_diag_block:
                message = message.rstrip() + pipeline_diag_block + "\n"
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                if len(executed_positions) > 0:
                    log.info(f"Easy 0DTE Options Execution alert sent - {len(executed_positions)} trades, ${total_capital_deployed:.2f} deployed")
                else:
                    if pending_confirmation_total > 0:
                        log.info(
                            "Easy 0DTE Options Execution alert sent - no durable executions yet; pending_confirmation_total=%d",
                            pending_confirmation_total,
                        )
                    else:
                        log.warning("Easy 0DTE Options Execution alert sent - 0 trades executed")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending Options Execution alert: {e}")
            return False

    async def send_options_durability_update_alert(
        self,
        durable_positions: List[Dict[str, Any]],
        mode: str = "DEMO",
    ) -> bool:
        """Send a follow-up when pending 0DTE submissions become durable."""
        if not durable_positions:
            return False
        try:
            from zoneinfo import ZoneInfo

            pt_tz = ZoneInfo("America/Los_Angeles")
            et_tz = ZoneInfo("America/New_York")
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            pt_time = now_pt.strftime("%I:%M %p PT")
            et_time = now_et.strftime("%I:%M %p ET")

            slice_rows = durable_positions[:10]
            acct_fb = None
            if slice_rows:
                acct_fb = float(slice_rows[0].get("account_balance") or 0.0) or None
            trade_lines = self._format_0dte_execution_trade_lines(
                slice_rows,
                1,
                include_quote_durable_metrics=True,
                account_balance_fallback=acct_fb,
            )
            trades_block = "\n\n".join(trade_lines) if trade_lines else ""

            message = f"""====================================================================

✅ <b>{ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} — Durable Confirmation Update</b>
{_alert_mode_header_line(mode)}
          Time: {pt_time} ({et_time})

⏳ Previously pending 0DTE submissions are now <b>durable and fully monitored</b>:

{trades_block}
"""
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            if success:
                log.info(
                    "0DTE durability follow-up alert sent | confirmed_count=%d",
                    len(durable_positions),
                )
            return success
        except Exception as e:
            log.error(f"Error sending 0DTE durability follow-up alert: {e}")
            return False

    async def send_options_exit_grade_durable_alert(
        self,
        rows: List[Dict[str, Any]],
        mode: str = "DEMO",
        *,
        strategy_label: Optional[str] = None,
    ) -> bool:
        """Follow-up when exit-grade durable marks stabilize for a submitted 0DTE position."""
        if not rows:
            return False
        try:
            from zoneinfo import ZoneInfo

            from modules.exit_grade_durability import (
                STRATEGY_LABEL_0DTE,
                strategy_alert_label,
            )

            pt_tz = ZoneInfo("America/Los_Angeles")
            et_tz = ZoneInfo("America/New_York")
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            pt_time = now_pt.strftime("%I:%M %p PT")
            et_time = now_et.strftime("%I:%M %p ET")
            lines = []
            inferred_label = str(strategy_label or "").strip()
            for r in rows[:12]:
                sym = escape(str(r.get("symbol", "UNKNOWN")))
                tid = escape(_trade_id_text(r.get("position_id", "N/A")))
                ticks = int(r.get("good_mark_ticks", 0) or 0)
                qs = escape(
                    PrimeAlertManager._quote_source_display(r.get("quote_source", "unknown"))
                )
                lines.append(f"          • <b>{sym}</b> ({tid}) — exit-grade durable • good_mark_ticks={ticks} • quote_src={qs}")
                if not inferred_label:
                    inferred_label = strategy_alert_label(str(r.get("strategy_type") or ""))
            if not inferred_label:
                inferred_label = STRATEGY_LABEL_0DTE
            # Safety: Stage B is unified Easy 0DTE branding (kept for logs only)
            if inferred_label not in (STRATEGY_LABEL_0DTE, "Easy 0DTE"):
                inferred_label = STRATEGY_LABEL_0DTE
            body = "\n".join(lines) if lines else "          <i>(no rows)</i>"
            message = f"""====================================================================

🎯 <b>Exit-Grade Durable (Stage B)</b>
{_alert_mode_header_line(mode)}
          Time: {pt_time} ({et_time})

{body}

📊 <b>Status:</b> Option marks reached exit-grade stability threshold; monitoring continues for exits.
"""
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            if success:
                log.info(
                    "0DTE exit-grade durable alert sent | rows=%d | strategy_label=%s",
                    len(rows),
                    inferred_label,
                )
            return success
        except Exception as e:
            log.error(f"Error sending exit-grade durable alert: {e}")
            return False

    async def send_options_durability_terminal_alert(
        self,
        *,
        rows: List[Dict[str, Any]],
        mode: str,
        run_id: str,
        reason: str,
    ) -> bool:
        """Observability-only Telegram when any broker-submitted row remains non-terminal after reconcile window.

        Does not close positions, cancel orders, or mutate trading state — notification only.
        """
        if not rows:
            return False
        try:
            from zoneinfo import ZoneInfo

            pt_tz = ZoneInfo("America/Los_Angeles")
            et_tz = ZoneInfo("America/New_York")
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            pt_time = now_pt.strftime("%I:%M %p PT")
            et_time = now_et.strftime("%I:%M %p ET")
            lines = []
            for r in rows[:15]:
                sym = escape(str(r.get("symbol", "UNKNOWN")))
                tid = escape(_trade_id_text(r.get("position_id", "N/A")))
                st = escape(str(r.get("monitor_state", r.get("prior_state", "unknown"))))
                rc = escape(str(r.get("reason_code", "pending")))
                lines.append(f"          • <b>{sym}</b> ({tid}) — state={st} — code={rc}")
            body = "\n".join(lines) if lines else "          <i>(no rows)</i>"
            message = f"""====================================================================

⚠️ <b>{ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} — Durability terminal notice</b>
{_alert_mode_header_line(mode)}
          Time: {pt_time} ({et_time})
          Run: <code>{escape(str(run_id))}</code>

<b>Issue:</b> {escape(str(reason))}

<b>Positions still outside a terminal observability bucket after reconcile + quote pumps:</b>
{body}

📌 <b>Next steps:</b> Inspect logs for <code>ORB_0DTE_DURABILITY_BLOCKED_REASON</code>, <code>ORB_0DTE_MONITOR_ATTACHED</code>, and <code>ORB_0DTE_FIRST_QUOTE_HYDRATED</code> for these trade_ids.

🛈 <b>Observability only:</b> This message does not close positions, modify orders, or change strategy or durability state. Positions remain managed by the normal monitoring and exit paths.
"""
            success = await self._send_telegram_message(message, AlertLevel.WARNING)
            if success:
                log.info("0DTE durability terminal alert sent | rows=%d | run_id=%s", len(rows), run_id)
            return success
        except Exception as e:
            log.error(f"Error sending durability terminal alert: {e}")
            return False

    @staticmethod
    def _qg_reject_display_code(value: Any) -> Optional[str]:
        """Normalize path-opt QG tags for Telegram Reason (desk review).

        Two-part split after ``gate_stack:`` keeps ``{id}:{reason}`` in full
        (reasons have no extra colons). Examples:
          ``quality_reject:gate_stack:RSI:put_41.2<48``
            → ``RSI:put_41.2<48``
          ``gate_stack:VWAP:ext_2.09>15``
            → ``VWAP:ext_2.09>15``
        """
        raw = str(value or "").strip()
        if not raw:
            return None
        for prefix in (
            "quality_reject:",
            "qg_reject:",
            "exec_reject:",
            "gate_stack_reject:",
        ):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix) :].strip()
                break
        # Comma-joined failed_reasons — prefer first gate_stack tag.
        if "," in raw and "gate_stack:" in raw:
            for part in raw.split(","):
                part = part.strip()
                if part.startswith("gate_stack:"):
                    raw = part
                    break
        if not raw.startswith("gate_stack:"):
            return None
        rest = raw[len("gate_stack:") :].strip()
        gid = rest.split(":", 1)[0].strip()
        if gid in ("v25", "MACD_noise"):
            return "MACD_noise"
        return rest[:120] if rest else None

    @staticmethod
    def _execution_reject_display_code(value: Any) -> str:
        """Short alert label for noisy internal selector/executor reason codes.

        Cloud/executor logs may keep metric suffixes (e.g.
        ``contract_otm_too_deep:0.0388>0.0350``); Telegram shows the code only.
        Human prefixes from ``resolve_orb_0dte_post_selector_reject`` are stripped
        so ask/bid size rejects alert as bare codes (Aug 11 compact).

        Path-opt QG rejects keep ``{gate_id}:{reason_token}`` (see
        ``_qg_reject_display_code``) so desk review matches
        ``docs/0DTEQualityGates.md`` §3.2.
        """
        qg = PrimeAlertManager._qg_reject_display_code(value)
        if qg:
            return qg
        raw = str(value or "").strip()
        for prefix in (
            "Market quality reject:",
            "Execution blocked:",
            "Execution risk blocked:",
            "Broker submit failed:",
            "quality_reject:",
            "qg_reject:",
            "exec_reject:",
        ):
            if raw.lower().startswith(prefix.lower()):
                raw = raw[len(prefix) :].strip()
                break
        # Re-check after stripping quality_reject: / qg_reject: wrappers.
        qg2 = PrimeAlertManager._qg_reject_display_code(raw)
        if qg2:
            return qg2
        # "contract_otm_too_deep:0.0388>0.0350" → contract_otm_too_deep
        # Leave human phrases with spaces ("Premium filter: …") intact.
        # Do NOT truncate gate_id.reason forms that use dots (handled above).
        if ":" in raw:
            head = raw.split(":", 1)[0].strip()
            if head and " " not in head and all(c.isalnum() or c == "_" for c in head):
                raw = head
        aliases = {
            "v25": "MACD_noise",
            "option_premium_too_low": "premium_too_low",
            "option_premium_too_high": "premium_too_high",
            "premium_out_of_band": "premium_outside_range",  # legacy
            "no_affordable_contract_for_slot": "no_affordable_contract",
            "contract_too_expensive_for_slot": "too_expensive_for_slot",
            "option_too_expensive_for_budget": "too_expensive_for_budget",
            "option_low_open_interest": "low_open_interest",  # legacy
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _execution_chain_source_display(value: Any) -> str:
        """Short alert label for chain_source (legacy aliases → current names)."""
        raw = str(value or "").strip()
        aliases = {
            "degraded_opening_selector": "degraded_selector",
            "chain_manager_cache": "chain_cache",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _quote_source_display(value: Any) -> str:
        """Short alert label for exit Quote Source (legacy aliases → current names)."""
        raw = str(value or "").strip()
        aliases = {
            "fresh_chain_force_refresh": "fresh_chain_refresh",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _trendline_exec_context_line_items(row: Dict[str, Any]) -> List[str]:
        """Option-chain / rejection context — one key=value per line."""
        lines: List[str] = []
        for key in (
            "chain_source",
            "contract_source",
            "reject_reason",
            "selected_delta",
            "spread_pct",
            "max_spread_pct",
            "open_interest",
            "min_open_interest",
            "volume",
            "min_volume",
            "min_premium",
            "quote_age_seconds",
            "estimated_contract_cost",
            "slot_capital_target",
            "entry_budget",
            "available_cash",
            "premium_per_share",
        ):
            val = row.get(key)
            if val is None or val == "":
                continue
            if key == "reject_reason":
                # Skip when it only restates Reason / failure_reason_code (Aug 11 compact).
                rr_code = PrimeAlertManager._execution_reject_display_code(val)
                primary = PrimeAlertManager._execution_reject_display_code(
                    row.get("failure_reason_code") or row.get("failure_reason") or ""
                )
                if rr_code and primary and rr_code == primary:
                    continue
                if rr_code:
                    lines.append(f"{key}={rr_code}")
                continue
            if key == "chain_source":
                lines.append(
                    f"{key}={PrimeAlertManager._execution_chain_source_display(val)}"
                )
                continue
            if key == "selected_delta":
                try:
                    lines.append(f"δ={float(val):.3f}")
                except (TypeError, ValueError):
                    lines.append(f"δ={val}")
            elif key == "spread_pct":
                try:
                    lines.append(f"spread_pct={float(val):.2f}")
                except (TypeError, ValueError):
                    lines.append(f"spread_pct={val}")
            elif key == "max_spread_pct":
                try:
                    lines.append(f"max_spread_pct={float(val):.2f}")
                except (TypeError, ValueError):
                    lines.append(f"max_spread_pct={val}")
            elif key in ("open_interest", "volume", "min_open_interest", "min_volume"):
                lines.append(f"{key}={int(val)}")
            elif key == "min_premium":
                try:
                    lines.append(f"min_premium={float(val):.4f}")
                except (TypeError, ValueError):
                    lines.append(f"min_premium={val}")
            elif key == "quote_age_seconds":
                try:
                    lines.append(f"quote_age_seconds={float(val):.2f}")
                except (TypeError, ValueError):
                    lines.append(f"quote_age_seconds={val}")
            elif key in ("estimated_contract_cost", "slot_capital_target", "entry_budget", "available_cash"):
                try:
                    lines.append(f"{key}=${float(val):,.2f}")
                except (TypeError, ValueError):
                    lines.append(f"{key}={val}")
            elif key == "premium_per_share":
                try:
                    lines.append(f"premium=${float(val):.2f}/sh")
                except (TypeError, ValueError):
                    lines.append(f"premium={val}")
            else:
                lines.append(f"{key}={val}")
        health = str(row.get("option_health") or row.get("health_detail") or "").strip()
        if health:
            lines.append(health)
        liq_cap = PrimeAlertManager._liquidity_guard_cap_alert_line(row)
        if liq_cap:
            lines.append(liq_cap)
        return lines

    @staticmethod
    def _liquidity_guard_cap_alert_line(row: Dict[str, Any]) -> str:
        """Liq Cap line when Liquidity Guard sized down (ORB1 / TL / BO fill + exit)."""
        if not isinstance(row, dict):
            return ""
        er = row.get("execution_risk")
        if not isinstance(er, dict):
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            er = meta.get("execution_risk") if isinstance(meta, dict) else None
            if not isinstance(er, dict) and isinstance(meta, dict):
                norm = meta.get("normalized_options")
                if isinstance(norm, dict) and isinstance(norm.get("execution_risk"), dict):
                    er = norm.get("execution_risk")
        try:
            from modules.dte0_execution_risk_manager import (
                format_liquidity_guard_cap_alert_line,
            )

            line = format_liquidity_guard_cap_alert_line(
                er if isinstance(er, dict) else None
            )
        except Exception:
            return ""
        return str(line or "").strip()

    @staticmethod
    def _trendline_exec_health_suffix(row: Dict[str, Any]) -> str:
        """Compact option-chain / rejection context for Trendline execution rows."""
        items = PrimeAlertManager._trendline_exec_context_line_items(row)
        if not items:
            return ""
        return "\n          " + "\n          ".join(escape(str(x)) for x in items)

    @staticmethod
    def _format_trendline_execution_diag_ok_row(row: Dict[str, Any]) -> str:
        setup = escape(str(row.get("setup_type") or "—"))
        prem = float(row.get("entry_premium") or 0.0)
        stage = escape(str(row.get("diagnostic_stage") or "executed"))
        context = PrimeAlertManager._trendline_exec_context_line_items(row)
        context_block = ""
        if context:
            context_block = "\n" + "\n".join(f"          {escape(str(x))}" for x in context)
        return (
            f"          stage={stage}\n"
            f"          result=ok\n"
            f"          structure={setup}\n"
            f"          premium={_fmt_usd(prem)} | monitoring=active"
            f"{context_block}"
        )

    @staticmethod
    def _format_trendline_execution_diag_reject_row(row: Dict[str, Any]) -> str:
        stage = escape(str(row.get("failure_stage") or "execution"))
        code = escape(
            PrimeAlertManager._execution_reject_display_code(
                row.get("failure_reason_code")
                or (str(row.get("failure_reason") or "unknown").split("|")[0].strip())
                or "unknown"
            )
        )
        context = PrimeAlertManager._trendline_exec_context_line_items(row)
        context_block = ""
        if context:
            context_block = "\n" + "\n".join(f"          {escape(str(x))}" for x in context)
        return f"          stage={stage}\n          result=reject | {code}{context_block}"


    @staticmethod
    def _orb1_position_dict_to_execution_row(position: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize ORB1 watch fill dict into shared single-emit execution row."""
        if not isinstance(position, dict):
            return {}
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        lotto = position.get("lotto_contract") if isinstance(position.get("lotto_contract"), dict) else {}
        direction = str(position.get("direction") or "").strip().upper()
        option_side = str(position.get("option_side") or "").strip().upper()
        if option_side not in ("CALL", "PUT"):
            ot = str(lotto.get("option_type") or "").strip().upper()
            if ot in ("CALL", "PUT"):
                option_side = ot
            elif direction in ("LONG", "CALL"):
                option_side = "CALL"
            elif direction in ("SHORT", "PUT"):
                option_side = "PUT"
            else:
                option_side = "N/A"
        try:
            strike = float(position.get("strike") or lotto.get("strike") or 0.0)
        except (TypeError, ValueError):
            strike = 0.0
        if strike <= 0:
            for key in ("long_strike", "short_strike"):
                try:
                    strike = float(position.get(key) or 0.0)
                except (TypeError, ValueError):
                    strike = 0.0
                if strike > 0:
                    break
        try:
            delta = float(
                position.get("delta_at_entry")
                or position.get("selected_delta")
                or lotto.get("delta")
                or position.get("target_delta")
                or 0.0
            )
        except (TypeError, ValueError):
            delta = 0.0
        try:
            entry_premium = float(
                position.get("entry_premium")
                or position.get("entry_price")
                or lotto.get("mid_price")
                or lotto.get("last")
                or lotto.get("premium")
                or 0.0
            )
        except (TypeError, ValueError):
            entry_premium = 0.0
        try:
            qty = int(position.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        try:
            est_cost = float(
                position.get("estimated_total_cost")
                or position.get("capital_allocated")
                or position.get("total_cost")
                or (entry_premium * max(1, qty) * 100.0)
                or 0.0
            )
        except (TypeError, ValueError):
            est_cost = 0.0
        try:
            equity = float(
                position.get("account_equity_at_entry")
                or meta.get("account_equity_at_entry")
                or position.get("account_balance")
                or 0.0
            )
        except (TypeError, ValueError):
            equity = 0.0
        trigger = str(position.get("trigger_direction") or "").strip()
        if not trigger:
            if option_side == "PUT" or direction in ("SHORT", "PUT"):
                trigger = "below_orb_low"
            elif option_side == "CALL" or direction in ("LONG", "CALL"):
                trigger = "above_orb_high"
            else:
                trigger = "n/a"
        setup = str(
            position.get("setup_type")
            or position.get("entry_type")
            or "orb1_range_breakout"
        ).strip()
        chain = str(
            position.get("chain_source")
            or meta.get("entry_chain_source")
            or meta.get("chain_source")
            or ""
        ).strip()
        row = {
            "symbol": str(position.get("symbol") or "UNKNOWN"),
            "option_side": option_side,
            "setup_type": setup,
            "trigger_direction": trigger,
            "trade_id": str(position.get("trade_id") or position.get("position_id") or "n/a"),
            "quantity": qty,
            "strike": strike,
            "delta_at_entry": delta,
            "entry_premium": entry_premium,
            "estimated_total_cost": est_cost,
            "account_equity_at_entry": equity,
            "diagnostic_stage": str(position.get("diagnostic_stage") or "executed"),
            "execution_attempted": True,
            "direction": direction,
        }
        _exp = str(
            position.get("expiry_ymd")
            or meta.get("expiry_ymd")
            or meta.get("selected_expiry_ymd")
            or lotto.get("expiry_ymd")
            or lotto.get("expiry")
            or position.get("expiry")
            or ""
        ).strip()
        if _exp:
            row["expiry_ymd"] = _exp
        if chain:
            row["chain_source"] = chain
        for key in ("slot_capital_target", "entry_budget", "option_health", "health_detail"):
            if position.get(key) is not None:
                row[key] = position.get(key)
            elif meta.get(key) is not None:
                row[key] = meta.get(key)
        er = position.get("execution_risk")
        if not isinstance(er, dict):
            er = meta.get("execution_risk")
        if isinstance(er, dict):
            row["execution_risk"] = er
        return row

    # ORB1, ORB2, and Trendline send Trade Profile rejects and fills.
    # Capacity waits stay silent. dir_pct and pc_oi are not admit stages.
    _STRATEGY_EXECUTION_REJECT_ALERT_STAGES = {
        # post_executor = chain/MQ/delta after handoff · quality_reject = path-opt QG / reclaim
        "orb1": frozenset({"post_executor", "post_selector_execution", "quality_reject"}),
        "breakout": frozenset({"post_executor", "quality_reject", "trade_profile"}),
        "trendline": frozenset(
            {
                "post_executor",
                "quality_reject",
                "trade_profile",
            }
        ),
    }

    @staticmethod
    def _orb1_rejected_signal_to_execution_row(entry: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize ORB1 watch reject payload into shared single-emit failed row."""
        if not isinstance(entry, dict):
            return {}
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        code = str(entry.get("reason_code") or entry.get("reason") or "").strip()
        direction = str(
            entry.get("direction") or details.get("direction") or ""
        ).strip().upper()
        option_side = _orb_0dte_option_side_label(
            details.get("option_type")
            or details.get("option_side")
            or entry.get("option_side")
            or direction
        )
        if option_side == "—":
            option_side = "N/A"

        stage_in = str(entry.get("failure_stage") or "").strip()
        post_selector = PrimeAlertManager._orb_0dte_is_post_selector_reject(code, details)
        if stage_in in ("post_executor", "post_selector_execution"):
            stage = stage_in
            attempted = True
        elif post_selector:
            stage = "post_selector_execution"
            attempted = True
        else:
            # Pre-execution / quality-gate only — no single-emit Telegram (ORB2/TL policy).
            stage = "pre_execution"
            attempted = False

        reason_display = PrimeAlertManager._orb_0dte_reject_display_reason(entry)
        reason_code = (
            str(
                details.get("execution_block_reason")
                or details.get("reject_reason")
                or entry.get("reason_code")
                or code
                or "unknown"
            )
            .split("|")[0]
            .strip()
            or "unknown"
        )
        # "Market quality reject: ask_size_too_small" → ask_size_too_small
        if ":" in reason_code:
            left, right = reason_code.split(":", 1)
            if "reject" in left.lower() or "quality" in left.lower():
                reason_code = right.strip() or reason_code

        if option_side == "PUT" or direction in ("SHORT", "PUT"):
            trigger = "below_orb_low"
        elif option_side == "CALL" or direction in ("LONG", "CALL"):
            trigger = "above_orb_high"
        else:
            trigger = str(entry.get("trigger_direction") or details.get("trigger_direction") or "n/a")

        setup = str(
            entry.get("setup_type")
            or details.get("setup_type")
            or entry.get("entry_type")
            or "orb1_range_breakout"
        ).strip()

        row: Dict[str, Any] = {
            "symbol": str(entry.get("symbol") or "UNKNOWN"),
            "option_side": option_side,
            "setup_type": setup,
            "trigger_direction": trigger,
            "trade_id": str(entry.get("trade_id") or details.get("trade_id") or "n/a"),
            "failure_stage": stage,
            "failure_reason": str(reason_display or reason_code or "unknown")[:500],
            "failure_reason_code": reason_code,
            "execution_attempted": attempted,
            "direction": direction,
            "diagnostic_stage": stage,
        }
        for key in (
            "chain_source",
            "contract_source",
            "reject_reason",
            "selected_delta",
            "spread_pct",
            "max_spread_pct",
            "open_interest",
            "min_open_interest",
            "volume",
            "min_volume",
            "min_premium",
            "quote_age_seconds",
            "estimated_contract_cost",
            "slot_capital_target",
            "entry_budget",
            "available_cash",
            "premium_per_share",
            "option_health",
            "health_detail",
        ):
            if details.get(key) is not None and details.get(key) != "":
                row[key] = details.get(key)
            elif entry.get(key) is not None and entry.get(key) != "":
                row[key] = entry.get(key)
        if "reject_reason" in row:
            row["reject_reason"] = PrimeAlertManager._execution_reject_display_code(
                row.get("reject_reason")
            )
        elif reason_code:
            row["reject_reason"] = reason_code
        return row

    async def send_strategy_options_execution_alert(
        self,
        strategy_id: str,
        executed_rows: Optional[List[Dict[str, Any]]] = None,
        mode: str = "DEMO",
        failed_rows: Optional[List[Dict[str, Any]]] = None,
        *,
        single_emit: bool = True,
    ) -> bool:
        """
        Shared rolling single-emit entry alert for ORB1 / ORB2 / Trendline.

        One Telegram per position-open attempt (fill or execution reject).
        Strategy-specific title + optional Line Geometry (Trendline).
        """
        try:
            sid = _normalize_strategy_alert_id(strategy_id)
            allowed_reject_stages = self._STRATEGY_EXECUTION_REJECT_ALERT_STAGES.get(
                sid, frozenset({"post_executor"})
            )
            executed_rows = list(executed_rows or [])
            failed_rows = [
                r
                for r in (failed_rows or [])
                if isinstance(r, dict)
                and bool(r.get("execution_attempted"))
                and str(r.get("failure_stage") or "").strip() in allowed_reject_stages
            ]
            if not executed_rows and not failed_rows:
                return False

            use_single_emit = bool(single_emit) or (len(executed_rows) + len(failed_rows) == 1)
            if not use_single_emit:
                return False
            if len(executed_rows) + len(failed_rows) != 1:
                if executed_rows:
                    executed_rows = [executed_rows[0]]
                    failed_rows = []
                else:
                    failed_rows = [failed_rows[0]]

            include_geometry = sid == "trendline"

            if executed_rows:
                row = executed_rows[0]
                symbol_raw = str(row.get("symbol") or "UNKNOWN")
                option_side_raw = str(row.get("option_side") or "N/A")
                setup_display = escape(_strategy_setup_display(sid, row))
                trigger_direction = escape(str(row.get("trigger_direction") or "n/a"))
                trade_id = escape(_trade_id_text(row.get("trade_id") or "n/a"))
                quantity = int(row.get("quantity") or 1)
                strike = float(row.get("strike") or 0.0)
                delta_at_entry = float(row.get("delta_at_entry") or 0.0)
                entry_premium = float(row.get("entry_premium") or 0.0)
                estimated_total_cost = float(row.get("estimated_total_cost") or 0.0)
                entry_equity = float(row.get("account_equity_at_entry") or 0.0)
                capital_pct = self._deployed_capital_pct_of_equity(estimated_total_cost, entry_equity)
                deployed_line = self._format_exit_deployed_line(estimated_total_cost, capital_pct)
                direction_emoji = "🟢" if option_side_raw.upper() == "CALL" else "🔴"
                contract_line = escape(
                    self._format_option_contract_line(
                        quantity, symbol_raw, option_side_raw, entry_premium, lotto=False
                    )
                )
                health_block = self._trendline_exec_health_suffix(row)
                geometry_line = ""
                if include_geometry:
                    line_geometry_display = escape(
                        PrimeAlertManager._format_trendline_line_geometry_display(
                            str(row.get("line_geometry") or "")
                        )
                    )
                    geometry_line = f"\n          Line Geometry: {line_geometry_display}"
                trade_block = (
                    f"1) {direction_emoji} <b>{contract_line}</b>\n"
                    f"          {deployed_line}\n"
                    f"          {_fill_strike_delta_line(strike, delta_at_entry, row)}\n"
                    f"          Setup: {setup_display}\n"
                    f"          Trigger: {trigger_direction}"
                    f"{geometry_line}\n"
                    f"          Trade ID: {trade_id}"
                    f"{health_block}"
                )
                # No Symbol execution diagnostics — trade rows already carry chain/budget context.
                monitor_block = (
                    "\n\n🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~2s + shared backup)"
                )
                outcome_line = "💼 <b>Result:</b> Filled • execution"
                alert_level = AlertLevel.SUCCESS
                title = _strategy_execution_alert_title(sid, row)
            else:
                row = failed_rows[0]
                symbol = escape(str(row.get("symbol") or "UNKNOWN"))
                option_side = escape(str(row.get("option_side") or "N/A"))
                setup_display = escape(_strategy_setup_display(sid, row))
                stage = escape(str(row.get("failure_stage") or "execution"))
                # Prefer bare code (ask_size_too_small) over prefixed human summary.
                reason = escape(
                    self._execution_reject_display_code(
                        row.get("failure_reason_code")
                        or row.get("failure_reason")
                        or "unknown"
                    )
                )
                trade_id = escape(_trade_id_text(row.get("trade_id") or "n/a"))
                health_block = self._trendline_exec_health_suffix(row)
                trade_block = (
                    f"1) ⚠️ <b>{symbol} {option_side}</b>\n"
                    f"          Setup: {setup_display}\n"
                    f"          Stage: {stage}\n"
                    f"          Reason: {reason}\n"
                    f"          Trade ID: {trade_id}"
                    f"{health_block}"
                )
                monitor_block = ""
                outcome_line = "💼 <b>Result:</b> Rejected • execution"
                alert_level = AlertLevel.WARNING
                title = _strategy_execution_alert_title(sid, row)

            message = f"""====================================================================

{title}
{_alert_mode_header_line(mode)}

{outcome_line}

{trade_block}{monitor_block}"""
            tg_priority = (
                TELEGRAM_PRIORITY_FILL if executed_rows else TELEGRAM_PRIORITY_NORMAL
            )
            success = await self._send_telegram_message(
                message, alert_level, priority=tg_priority
            )
            sym = str((executed_rows or failed_rows or [{}])[0].get("symbol") or "?")
            pipe = {
                "orb1": "ORB1_0DTE_WATCH",
                "breakout": "BREAKOUT_PIPELINE",
                "trendline": "TRENDLINE_PIPELINE",
            }.get(sid, "STRATEGY_PIPELINE")
            if success:
                log.warning(
                    "%s | stage=execution_alert_dispatch | action=sent | "
                    "symbol=%s | single_emit=true | strategy=%s | filled=%s",
                    pipe,
                    sym,
                    sid,
                    str(bool(executed_rows)).lower(),
                )
            else:
                log.warning(
                    "%s | stage=execution_alert_dispatch | action=failed | "
                    "symbol=%s | single_emit=true | strategy=%s | filled=%s | reason=telegram_send_false",
                    pipe,
                    sym,
                    sid,
                    str(bool(executed_rows)).lower(),
                )
            return success
        except Exception as e:
            log.error("Error sending strategy execution alert (%s): %s", strategy_id, e)
            return False

    async def send_trendline_options_execution_alert(
        self,
        executed_rows: List[Dict[str, Any]],
        mode: str = "DEMO",
        failed_rows: Optional[List[Dict[str, Any]]] = None,
        *,
        single_emit: bool = False,
    ) -> bool:
        """
        Send Easy Trendline 0DTE options execution alert.

        When ``single_emit=True`` (intraday rolling path), sends one Telegram per
        execution attempt (position open filled or rejected after executor handoff).
        Batch mode preserved for multi-row summaries.
        """
        try:
            executed_rows = list(executed_rows or [])
            _tl_reject_stages = self._STRATEGY_EXECUTION_REJECT_ALERT_STAGES.get(
                "trendline", frozenset({"post_executor", "quality_reject"})
            )
            failed_rows = [
                r
                for r in (failed_rows or [])
                if isinstance(r, dict)
                and bool(r.get("execution_attempted"))
                and str(r.get("failure_stage") or "").strip() in _tl_reject_stages
            ]
            if not executed_rows and not failed_rows:
                return False

            use_single_emit = bool(single_emit) or (len(executed_rows) + len(failed_rows) == 1)
            if use_single_emit and (len(executed_rows) + len(failed_rows) == 1):
                return await self.send_strategy_options_execution_alert(
                    "trendline",
                    executed_rows=executed_rows,
                    mode=mode,
                    failed_rows=failed_rows,
                    single_emit=True,
                )

            total_cost = 0.0
            trade_lines: List[str] = []
            for idx, row in enumerate(executed_rows, 1):
                symbol_raw = str(row.get("symbol") or "UNKNOWN")
                option_side_raw = str(row.get("option_side") or "N/A")
                setup_type = escape(str(row.get("setup_type") or "n/a"))
                trigger_direction = escape(str(row.get("trigger_direction") or "n/a"))
                line_geometry_display = escape(
                    PrimeAlertManager._format_trendline_line_geometry_display(
                        str(row.get("line_geometry") or "")
                    )
                )
                trade_id = escape(_trade_id_text(row.get("trade_id") or "n/a"))
                quantity = int(row.get("quantity") or 1)
                strike = float(row.get("strike") or 0.0)
                delta_at_entry = float(row.get("delta_at_entry") or 0.0)
                entry_premium = float(row.get("entry_premium") or 0.0)
                estimated_total_cost = float(row.get("estimated_total_cost") or 0.0)
                entry_equity = float(row.get("account_equity_at_entry") or 0.0)
                capital_pct = self._deployed_capital_pct_of_equity(estimated_total_cost, entry_equity)
                deployed_line = self._format_exit_deployed_line(estimated_total_cost, capital_pct)
                total_cost += estimated_total_cost
                direction_emoji = "🟢" if option_side_raw.upper() == "CALL" else "🔴"
                contract_line = escape(
                    self._format_trendline_execution_contract_line(
                        quantity, symbol_raw, option_side_raw, entry_premium
                    )
                )
                trade_lines.append(
                    f"{idx}) {direction_emoji} <b>{contract_line}</b>\n"
                    f"          {deployed_line}\n"
                    f"          {_fill_strike_delta_line(strike, delta_at_entry, row)}\n"
                    f"          Setup: {setup_type}\n"
                    f"          Trigger: {trigger_direction}\n"
                    f"          Line Geometry: {line_geometry_display}\n"
                    f"          Trade ID: {trade_id}"
                )

            failed_lines: List[str] = []
            fail_buckets: Dict[str, List[str]] = {}
            for idx, row in enumerate(failed_rows, 1):
                symbol = escape(str(row.get("symbol") or "UNKNOWN"))
                option_side = escape(str(row.get("option_side") or "N/A"))
                setup_type = escape(str(row.get("setup_type") or "n/a"))
                stage = escape(str(row.get("failure_stage") or "execution"))
                reason = escape(str(row.get("failure_reason") or "unknown"))
                trade_id = escape(_trade_id_text(row.get("trade_id") or "n/a"))
                entry_mode = escape(str(row.get("entry_mode") or ""))
                emode = f"\n          <b>Entry mode:</b> {entry_mode}" if entry_mode else ""
                failed_lines.append(
                    f"{idx}) ⚠️ <b>{symbol} {option_side}</b>\n"
                    f"          <b>Setup:</b> {setup_type}\n"
                    f"          <b>Stage:</b> {stage}\n"
                    f"          <b>Reason:</b> {reason}\n"
                    f"          <b>Trade ID:</b> {trade_id}"
                    f"{emode}"
                )
                bucket_key = f"{row.get('failure_stage') or 'execution'}:{row.get('failure_reason') or 'unknown'}"
                fail_buckets.setdefault(bucket_key, [])
                sym_raw = str(row.get("symbol") or "UNKNOWN")
                if sym_raw not in fail_buckets[bucket_key]:
                    fail_buckets[bucket_key].append(sym_raw)

            fail_summary_lines: List[str] = []
            for key, syms in sorted(fail_buckets.items(), key=lambda x: len(x[1]), reverse=True)[:6]:
                stage_part, _, reason_part = key.partition(":")
                sample = ", ".join(escape(s) for s in syms[:4])
                more = f" … +{len(syms) - 4}" if len(syms) > 4 else ""
                fail_summary_lines.append(
                    f"          • <b>{escape(stage_part)}</b>: {len(syms)} — {escape(reason_part[:120])}"
                    + (f"\n            (e.g. {sample}{more})" if sample else "")
                )

            executed_count = len(executed_rows)
            failed_count = len(failed_rows)
            attempted_count = executed_count + failed_count
            executed_block = (
                chr(10).join(trade_lines) if trade_lines else "          <i>No fills this cycle</i>"
            )
            failed_block = ""
            if failed_lines:
                failed_block = (
                    f"\n\n⚠️ <b>Emitted but not filled ({failed_count}):</b>\n"
                    + chr(10).join(failed_lines)
                )
            fail_summary_block = ""
            if fail_summary_lines:
                fail_summary_block = (
                    "\n\n📉 <b>Failure breakdown (this cycle):</b>\n" + chr(10).join(fail_summary_lines)
                )

            outcome_line = (
                f"💼 <b>Attempted:</b> {attempted_count} • <b>Filled:</b> {executed_count} • <b>Failed:</b> {failed_count}"
            )
            capital_block = ""
            if executed_count > 0:
                capital_block = (
                    f"\n\n💰 <b>Capital Deployment (fills only):</b>\n"
                    f"          • <b>Estimated:</b> {_fmt_usd(total_cost)}"
                )
            monitor_block = ""
            if executed_count > 0:
                monitor_block = (
                    "\n\n🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~2s + shared backup)"
                )

            alert_level = AlertLevel.SUCCESS
            if failed_count > 0 and executed_count == 0:
                alert_level = AlertLevel.WARNING
            elif failed_count > 0:
                alert_level = AlertLevel.WARNING

            message = f"""====================================================================

{_trendline_execution_alert_title(rows=list(executed_rows) + list(failed_rows))}
{_alert_mode_header_line(mode)}

{outcome_line}

<b>Fills:</b>
{executed_block}{failed_block}{fail_summary_block}{capital_block}{monitor_block}"""

            tg_priority = (
                TELEGRAM_PRIORITY_FILL if executed_count > 0 else TELEGRAM_PRIORITY_NORMAL
            )
            success = await self._send_telegram_message(
                message, alert_level, priority=tg_priority
            )
            if success:
                log.info(
                    "Trendline execution alert sent - attempted=%d filled=%d failed=%d est_cost=%.2f",
                    attempted_count,
                    executed_count,
                    failed_count,
                    total_cost,
                )
            return success
        except Exception as e:
            log.error(f"Error sending Trendline execution alert: {e}")
            return False

    @staticmethod
    def _format_breakout_execution_diag_ok_row(row: Dict[str, Any]) -> str:
        """Breakout fill diagnostics — no structure line (ORB-range path)."""
        prem = float(row.get("entry_premium") or 0.0)
        stage = escape(str(row.get("diagnostic_stage") or "executed"))
        context = PrimeAlertManager._trendline_exec_context_line_items(row)
        context_block = ""
        if context:
            context_block = "\n" + "\n".join(f"          {escape(str(x))}" for x in context)
        return (
            f"          stage={stage}\n"
            f"          result=ok\n"
            f"          premium={_fmt_usd(prem)} | monitoring=active"
            f"{context_block}"
        )

    async def send_breakout_options_execution_alert(
        self,
        executed_rows: List[Dict[str, Any]],
        mode: str = "DEMO",
        failed_rows: Optional[List[Dict[str, Any]]] = None,
        *,
        single_emit: bool = True,
    ) -> bool:
        """ORB2 Breakout rolling entry — shared strategy single-emit contract."""
        return await self.send_strategy_options_execution_alert(
            "breakout",
            executed_rows=executed_rows,
            mode=mode,
            failed_rows=failed_rows,
            single_emit=single_emit,
        )


    @staticmethod
    def _options_resolve_delta_at_entry(position: Dict[str, Any]) -> Optional[float]:
        """Best-effort entry delta for exit Telegram (metadata, legs, lotto, execution payload)."""
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
        eng = osnap.get("engine") if isinstance(osnap.get("engine"), dict) else {}
        candidates: List[Any] = [
            norm.get("delta_at_entry"),
            eng.get("delta_at_entry"),
        ]
        for key in ("execution_payload", "signal_snapshot"):
            blob = meta.get(key) if isinstance(meta.get(key), dict) else {}
            candidates.extend(
                [
                    blob.get("delta_at_entry"),
                    blob.get("delta"),
                    blob.get("selected_delta"),
                    blob.get("representative_delta"),
                ]
            )
        legs = norm.get("legs")
        if isinstance(legs, list):
            for lg in legs:
                if isinstance(lg, dict):
                    candidates.append(lg.get("delta_at_entry"))
                    candidates.append(lg.get("delta"))
        lc = position.get("lotto_contract")
        if isinstance(lc, dict):
            candidates.append(lc.get("delta"))
        for raw in candidates:
            try:
                val = abs(float(raw))
            except (TypeError, ValueError):
                continue
            if val > 1e-6:
                return val
        return None

    @staticmethod
    def _options_resolve_delta_at_exit(
        position: Dict[str, Any],
        *,
        entry_delta: Optional[float] = None,
    ) -> Optional[float]:
        """Best-effort exit delta from stealth snapshot / exit diagnostics."""
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
        diag = osnap.get("exit_diagnostics") if isinstance(osnap.get("exit_diagnostics"), dict) else {}
        candidates: List[Any] = [
            diag.get("effective_delta_estimate"),
            diag.get("delta_estimate"),
            diag.get("delta_at_exit"),
            osnap.get("delta_at_exit"),
        ]
        eng = osnap.get("engine") if isinstance(osnap.get("engine"), dict) else {}
        if isinstance(eng, dict):
            candidates.append(eng.get("delta_at_exit"))
        for raw in candidates:
            try:
                val = abs(float(raw))
            except (TypeError, ValueError):
                continue
            if val > 1e-6:
                return val
        if entry_delta is not None and entry_delta > 1e-6:
            return float(entry_delta)
        return None

    @staticmethod
    def _options_exit_row_visuals(position: Dict[str, Any]) -> tuple[int, str, str, float, float, float, float]:
        """
        Map an options position dict to Trendline-style exit row fields.

        Returns:
            quantity, symbol, CALL/PUT, strike (long-leg reference), est_notional_dollars,
            delta_at_entry, delta_at_exit
        """
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        qty = int(position.get("quantity") or 1)
        symbol = str(position.get("symbol") or norm.get("underlying_symbol") or "UNKNOWN")
        est_notional = abs(float(position.get("entry_price") or 0.0)) * max(1, qty) * 100.0

        opt_side = "N/A"
        strike = 0.0
        d0_resolved = PrimeAlertManager._options_resolve_delta_at_entry(position)
        d0 = float(d0_resolved) if d0_resolved is not None else 0.0
        d1_resolved = PrimeAlertManager._options_resolve_delta_at_exit(position, entry_delta=d0_resolved)
        d1 = float(d1_resolved) if d1_resolved is not None else d0

        legs = norm.get("legs")
        if isinstance(legs, list) and legs:
            long_leg = next(
                (lg for lg in legs if isinstance(lg, dict) and str(lg.get("long_or_short") or "").lower() == "long"),
                None,
            )
            ref = long_leg if isinstance(long_leg, dict) else (legs[0] if isinstance(legs[0], dict) else {})
            strike = float(ref.get("strike") or 0.0)
            osrc = str(ref.get("option_side") or "").strip().lower()
            if osrc in ("call", "put"):
                opt_side = osrc.upper()
            if d0 <= 0.0:
                deltas = [
                    abs(float(x.get("delta_at_entry") or x.get("delta") or 0.0))
                    for x in legs
                    if isinstance(x, dict)
                ]
                deltas = [d for d in deltas if d > 1e-6]
                if deltas:
                    d0 = sum(deltas) / len(deltas)
                    if d1 <= 0.0:
                        d1 = d0

        pt = str(norm.get("position_type") or position.get("position_type") or "").lower()
        if opt_side == "N/A":
            if "put" in pt:
                opt_side = "PUT"
            elif "call" in pt:
                opt_side = "CALL"

        ds = position.get("debit_spread")
        if isinstance(ds, dict):
            ot = str(ds.get("option_type") or "put").lower()
            opt_side = "CALL" if ot == "call" else "PUT"
            if strike <= 0.0:
                strike = float(ds.get("long_strike") or ds.get("strike") or 0.0)

        cs = position.get("credit_spread")
        if isinstance(cs, dict):
            ot = str(cs.get("option_type") or "put").lower()
            opt_side = "CALL" if ot == "call" else "PUT"
            if strike <= 0.0:
                strike = float(cs.get("long_strike") or cs.get("strike") or 0.0)

        lc = position.get("lotto_contract")
        if isinstance(lc, dict):
            ot = str(lc.get("option_type") or "put").lower()
            opt_side = "CALL" if ot == "call" else "PUT"
            if strike <= 0.0:
                strike = float(lc.get("strike") or 0.0)

        if opt_side == "N/A":
            for raw in (
                position.get("option_side"),
                position.get("option_type"),
                meta.get("option_side"),
                meta.get("option_type"),
                (meta.get("execution_payload") or {}).get("option_side")
                if isinstance(meta.get("execution_payload"), dict)
                else None,
                (meta.get("execution_payload") or {}).get("option_type")
                if isinstance(meta.get("execution_payload"), dict)
                else None,
                (meta.get("signal_snapshot") or {}).get("option_side")
                if isinstance(meta.get("signal_snapshot"), dict)
                else None,
            ):
                side = str(raw or "").strip().lower()
                if side in ("call", "put"):
                    opt_side = side.upper()
                    break

        if opt_side == "N/A":
            tid = str(position.get("position_id") or position.get("trade_id") or "").lower()
            # Compact IDs: D-DELL-437.5p-749 / BO_AMD_... often encode side near strike.
            if re.search(r"\d(?:\.\d+)?p(?:-|_|$)", tid) or tid.endswith("p") or "-p-" in tid:
                opt_side = "PUT"
            elif re.search(r"\d(?:\.\d+)?c(?:-|_|$)", tid) or tid.endswith("c") or "-c-" in tid:
                opt_side = "CALL"

        return qty, symbol, opt_side, strike, est_notional, d0, d1

    @staticmethod
    def _options_exit_pnl_fraction(
        position: Dict[str, Any],
        *,
        exit_price: float,
    ) -> float:
        """Return P&L as a fraction of entry premium (per-share), consistent with ExitSignal."""
        entry_price = float(position.get("entry_price") or 0.0)
        if entry_price <= 0:
            return 0.0
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        structural = str(norm.get("position_type") or "").strip().lower()
        if not structural:
            structural = str(position.get("position_type") or "").strip().lower()
        if structural == "credit_spread" or position.get("position_type") == "credit_spread":
            return (entry_price - float(exit_price)) / entry_price
        return (float(exit_price) - entry_price) / entry_price

    @staticmethod
    def _options_exit_infer_per_share_entry(position: Dict[str, Any]) -> float:
        """Recover per-share entry when ledger entry_price is zero (demo + batch Telegram)."""
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
        for key in ("entry_debit", "entry_value"):
            try:
                v = float(osnap.get(key) or 0.0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        for key in ("entry_value", "debit_per_share", "open_effective_premium"):
            try:
                v = float(norm.get(key) or 0.0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        ds = position.get("debit_spread")
        if isinstance(ds, dict):
            try:
                dc = float(ds.get("debit_cost") or 0.0)
                if dc > 0:
                    return dc
            except (TypeError, ValueError):
                pass
            long_c = ds.get("long_contract")
            if isinstance(long_c, dict):
                for k in ("mid_price", "last", "premium"):
                    try:
                        v = float(long_c.get(k) or 0.0)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        continue
        cs = position.get("credit_spread")
        if isinstance(cs, dict):
            try:
                v = float(cs.get("credit_received") or 0.0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        lc = position.get("lotto_contract")
        if isinstance(lc, dict):
            for k in ("mid_price", "last", "premium", "mark"):
                try:
                    v = float(lc.get(k) or 0.0)
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    continue
        return 0.0

    @staticmethod
    def _options_exit_infer_per_share_exit(position: Dict[str, Any], explicit_exit: float) -> float:
        """Recover per-share exit mark for Telegram when current_value / explicit exit are zero."""
        try:
            ex = float(explicit_exit or 0.0)
        except (TypeError, ValueError):
            ex = 0.0
        if ex > 0:
            return ex
        try:
            cv = float(position.get("current_value") or 0.0)
        except (TypeError, ValueError):
            cv = 0.0
        if cv > 0:
            return cv
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
        for key in ("spread_liquidation_value", "current_effective_premium", "spread_mid_value"):
            try:
                v = float(osnap.get(key) or 0.0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        inferred = PrimeAlertManager._options_exit_infer_per_share_entry(position)
        return inferred if inferred > 0 else 0.0

    def _options_exit_alert_block_from_position(
        self,
        position: Dict[str, Any],
        *,
        idx: int,
        exit_reason: str,
        exit_price: float,
        include_account_balance: bool,
        realized_pnl_override: Optional[float] = None,
        pnl_pct_fraction_override: Optional[float] = None,
        delta_at_entry_override: Optional[float] = None,
        delta_at_exit_override: Optional[float] = None,
        account_balance_post_close: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Rich OPTION CLOSED row dict (shared by single + aggregated ORB1 Breakout exit Telegram)."""
        entry_price_raw = float(position.get("entry_price") or 0.0)
        entry_price_eff = entry_price_raw
        if entry_price_eff <= 0.0:
            inferred_e = self._options_exit_infer_per_share_entry(position)
            if inferred_e > 0.0:
                entry_price_eff = inferred_e

        try:
            exit_arg = float(exit_price or 0.0)
        except (TypeError, ValueError):
            exit_arg = 0.0
        exit_px_eff = self._options_exit_infer_per_share_exit(position, exit_arg)
        if exit_px_eff <= 0.0 and entry_price_eff > 0.0:
            exit_px_eff = entry_price_eff

        pos_for_pnl: Dict[str, Any] = dict(position)
        if entry_price_eff > 0.0:
            pos_for_pnl["entry_price"] = entry_price_eff

        qty, sym, opt_side, strike, est_notional, d0, d1 = self._options_exit_row_visuals(pos_for_pnl)
        if delta_at_entry_override is not None:
            d0 = float(delta_at_entry_override)
        if delta_at_exit_override is not None:
            d1 = float(delta_at_exit_override)
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        diag_delta = (
            (osnap.get("exit_diagnostics") or {}).get("delta_at_exit")
            if isinstance(osnap.get("exit_diagnostics"), dict)
            else None
        )
        exit_delta_live = bool(
            diag_delta is not None
            or (osnap.get("delta_at_exit") is not None)
            or (
                delta_at_exit_override is not None
                and abs(float(delta_at_exit_override) - float(d0)) > 1e-6
            )
        )
        if abs(float(d1) - float(d0)) < 1e-6 and not exit_delta_live:
            log.info(
                "OPTION_DELTA_REPORT_AUDIT | trade_id=%s | symbol=%s | entry_delta=%.4f | "
                "exit_delta=%.4f | delta_pnl=%.4f | verdict=exit_delta_entry_fallback | "
                "delta_pnl_is_not_option_pnl=true",
                str(position.get("position_id") or "N/A"),
                str(position.get("symbol") or "UNKNOWN"),
                float(d0),
                float(d1),
                float(d1 - d0),
            )
        entry_underlying = float(
            ((osnap.get("engine") or {}) if isinstance(osnap.get("engine"), dict) else {}).get("entry_underlying")
            or position.get("entry_underlying")
            or 0.0
        )
        try:
            from modules.underlying_freshness import resolve_exit_underlying_price

            exit_underlying = float(
                resolve_exit_underlying_price(
                    live_underlying=osnap.get("current_underlying_price"),
                    osnap=osnap,
                    position=position,
                    metadata=meta if isinstance(meta, dict) else None,
                    entry_underlying=entry_underlying,
                    symbol=str(position.get("symbol") or ""),
                )
                or 0.0
            )
        except Exception:
            exit_underlying = float(
                osnap.get("current_underlying_price") or position.get("underlying_price") or entry_underlying or 0.0
            )
        underlying_move_pct = (
            (exit_underlying - entry_underlying) / entry_underlying if entry_underlying > 0 else 0.0
        )
        entry_net_debit = float(osnap.get("entry_debit") or osnap.get("entry_value") or entry_price_eff or 0.0)
        exit_liq_value = float(
            osnap.get("spread_liquidation_value") or osnap.get("current_effective_premium") or exit_px_eff or 0.0
        )
        exit_mid_value = float(osnap.get("spread_mid_value") or osnap.get("current_effective_premium") or exit_px_eff or 0.0)
        if exit_liq_value <= 0.0 and exit_px_eff > 0.0:
            exit_liq_value = exit_px_eff
        if exit_mid_value <= 0.0 and exit_px_eff > 0.0:
            exit_mid_value = exit_px_eff
        if entry_net_debit <= 0.0 and entry_price_eff > 0.0:
            entry_net_debit = entry_price_eff
        max_pnl_pct = float(osnap.get("max_pnl_pct") or 0.0)
        try:
            from modules.profit_retention_telemetry import display_max_bid_pnl_fraction

            max_pnl_pct = display_max_bid_pnl_fraction(
                max_pnl_pct,
                engine_fraction=osnap.get("max_pnl_pct"),
                entry_premium=entry_price_eff,
                premium_hwm=osnap.get("premium_hwm"),
            )
        except (TypeError, ValueError, ImportError):
            pass
        pnl_frac = (
            float(pnl_pct_fraction_override)
            if pnl_pct_fraction_override is not None
            else self._options_exit_pnl_fraction(pos_for_pnl, exit_price=float(exit_px_eff))
        )
        current_pnl_pct_liq = float(osnap.get("current_pnl_pct_liquidation") or pnl_frac or 0.0)
        quote_source = str(osnap.get("last_premium_source") or osnap.get("degraded_data_source") or "unknown")
        quote_age_seconds = float(osnap.get("quote_age_seconds") or 0.0)
        mark_quality = str(osnap.get("mark_quality") or "unknown")
        mark_is_exit_grade = bool(osnap.get("mark_is_exit_grade"))

        realized = (
            float(realized_pnl_override)
            if realized_pnl_override is not None
            else float(position.get("realized_pnl") or 0.0)
        )
        if abs(realized) < 1e-9 and entry_price_eff > 0.0:
            qty_b = int(position.get("quantity") or qty or 1)
            mult = 100.0 * max(1, qty_b)
            meta2 = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
            norm2 = meta2.get("normalized_options") if isinstance(meta2.get("normalized_options"), dict) else {}
            structural = str(norm2.get("position_type") or "").strip().lower()
            if not structural:
                structural = str(position.get("position_type") or "").strip().lower()
            if structural == "credit_spread" or position.get("position_type") == "credit_spread":
                realized = (entry_price_eff - exit_px_eff) * mult
            else:
                realized = (exit_px_eff - entry_price_eff) * mult

        deployed_capital = self._orb_0dte_position_deployed_capital(position)
        if deployed_capital <= 0:
            deployed_capital = float(est_notional)
        capital_pct = self._options_exit_deployed_capital_pct(
            position,
            deployed=deployed_capital,
            realized_pnl=realized,
            account_balance_post_close=account_balance_post_close,
        )

        eng = osnap.get("engine") if isinstance(osnap.get("engine"), dict) else {}
        expiry_src = _collect_expiry_fields_for_exit_alert(
            meta if isinstance(meta, dict) else {},
            trade_id=position.get("position_id") or position.get("trade_id"),
        )
        # Prefer engine/osnap overlays when already stamped during live monitor.
        try:
            from modules.calendar_expiry_metadata import extract_expiry_fields_from_mapping

            for _src in (eng, osnap):
                if isinstance(_src, dict):
                    for _k, _v in extract_expiry_fields_from_mapping(_src).items():
                        if _v is not None and _v != "":
                            expiry_src[_k] = _v
        except Exception:
            for _src in (eng, osnap, meta, norm):
                if isinstance(_src, dict):
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
                        "expiration_ymd",
                    ):
                        if _ek not in expiry_src and _src.get(_ek) is not None and _src.get(_ek) != "":
                            expiry_src[_ek] = _src.get(_ek)
        out = {
            "idx": int(idx),
            "realized_pnl": realized,
            "pnl_pct": pnl_frac,
            "quantity": qty,
            "priority_rank": int(position.get("priority_rank") or 0),
            "symbol": sym,
            "option_side": opt_side,
            "strike": strike,
            "est_cost": est_notional,
            "deployed_capital": deployed_capital,
            "capital_pct": capital_pct,
            "delta0": d0,
            "delta1": d1,
            "delta_pnl": (d1 - d0),
            "exit_delta_live": bool(exit_delta_live),
            "entry_premium": float(entry_price_eff),
            "exit_premium": float(exit_px_eff),
            "entry_underlying": float(entry_underlying),
            "exit_underlying": float(exit_underlying),
            "underlying_move_pct": float(underlying_move_pct),
            "entry_net_debit": float(entry_net_debit),
            "exit_liquidation_value": float(exit_liq_value),
            "exit_mid_value": float(exit_mid_value),
            "max_pnl_pct": float(max_pnl_pct),
            "current_pnl_pct_liquidation": float(current_pnl_pct_liq),
            "quote_source": str(quote_source),
            "quote_age_seconds": float(quote_age_seconds),
            "mark_quality": str(mark_quality),
            "mark_is_exit_grade": bool(mark_is_exit_grade),
            "trade_id": str(position.get("position_id") or "N/A"),
            "reason": normalize_exit_reason(str(exit_reason)) or str(exit_reason),
            "include_account_balance": bool(include_account_balance),
            "strategy_id": _closed_position_batch_strategy_id(position),
            "strategy_group": _batch_close_strategy_group_html(position),
            "session_date": expiry_src.get("session_date"),
            "expiry_ymd": expiry_src.get("expiry_ymd") or expiry_src.get("selected_expiry_ymd") or expiry_src.get("expiration_ymd"),
            "calendar_dte": expiry_src.get("calendar_dte"),
            "is_0dte": expiry_src.get("is_0dte"),
            "expiry_selection_mode": expiry_src.get("expiry_selection_mode"),
            "requested_expiry_ymd": expiry_src.get("requested_expiry_ymd"),
            "selected_expiry_ymd": expiry_src.get("selected_expiry_ymd"),
            "fallback_days": expiry_src.get("fallback_days"),
            "fallback_reason": expiry_src.get("fallback_reason"),
            "dte": expiry_src.get("dte") if expiry_src.get("dte") is not None else expiry_src.get("calendar_dte"),
            "metadata": meta if isinstance(meta, dict) else {},
        }
        er = None
        if isinstance(meta, dict):
            er = meta.get("execution_risk")
            if not isinstance(er, dict):
                norm = meta.get("normalized_options")
                if isinstance(norm, dict):
                    er = norm.get("execution_risk")
        if isinstance(er, dict):
            out["execution_risk"] = er
        return out

    def _format_one_closed_trade_block(
        self,
        b: Dict[str, Any],
        *,
        numbered: bool,
        account_balance: Optional[float] = None,
    ) -> str:
        """One numbered (or single) closed-trade body — shared by single + batch exits."""
        idx = int(b["idx"])
        realized = float(b["realized_pnl"])
        pnl_pct = float(b["pnl_pct"])
        qty = int(b["quantity"])
        est_cost = float(b["est_cost"])
        deployed = float(b.get("deployed_capital") or est_cost)
        capital_pct = b.get("capital_pct")
        deployed_line = self._format_exit_deployed_line(deployed, capital_pct)
        entry_premium = float(b.get("entry_premium", 0.0) or 0.0)
        exit_premium = float(b.get("exit_premium", 0.0) or 0.0)
        entry_underlying = float(b.get("entry_underlying", 0.0) or 0.0)
        exit_underlying = float(b.get("exit_underlying", 0.0) or 0.0)
        underlying_move_pct = float(b.get("underlying_move_pct", 0.0) or 0.0)
        max_pnl_pct = float(b.get("max_pnl_pct", 0.0) or 0.0)
        try:
            from modules.profit_retention_telemetry import (
                display_max_bid_pnl_fraction,
                liquidatable_pnl_to_fraction,
                premium_hwm_from_exit_metadata,
            )

            engine_mfe = None
            md = b.get("metadata")
            if isinstance(md, dict):
                osnap_m = md.get("option_stealth")
                if isinstance(osnap_m, dict) and osnap_m.get("max_pnl_pct") is not None:
                    engine_mfe = float(osnap_m.get("max_pnl_pct"))
            converted = liquidatable_pnl_to_fraction(
                max_pnl_pct, engine_fraction=engine_mfe
            )
            if converted is not None:
                max_pnl_pct = float(converted)
            max_pnl_pct = display_max_bid_pnl_fraction(
                max_pnl_pct,
                engine_fraction=engine_mfe,
                entry_premium=entry_premium,
                premium_hwm=premium_hwm_from_exit_metadata(
                    md if isinstance(md, dict) else None
                ),
            )
        except (TypeError, ValueError, ImportError):
            pass
        current_pnl_pct_liq = float(b.get("current_pnl_pct_liquidation", 0.0) or 0.0)
        try:
            from modules.profit_retention_telemetry import reconcile_exit_liq_fraction

            reconciled = reconcile_exit_liq_fraction(
                current_pnl_pct_liq,
                entry_premium=entry_premium,
                exit_premium=exit_premium,
            )
            if reconciled is not None:
                current_pnl_pct_liq = float(reconciled)
        except (TypeError, ValueError, ImportError):
            pass
        quote_source = escape(
            PrimeAlertManager._quote_source_display(b.get("quote_source", "unknown"))
        )
        quote_age = float(b.get("quote_age_seconds", 0.0) or 0.0)
        mark_quality = escape(str(b.get("mark_quality", "unknown")))
        mark_is_exit_grade = bool(b.get("mark_is_exit_grade", False))
        tid = escape(_trade_id_text(b["trade_id"]))
        rsn = escape(normalize_exit_reason(str(b["reason"])))
        pnl_pct_display = pnl_pct * 100.0
        display_dollars = realized
        try:
            from modules.options_trade_economics import (
                net_result_pnl_dollars,
                net_result_pnl_fraction,
            )

            _md = b.get("metadata") if isinstance(b.get("metadata"), dict) else {}
            _no = _md.get("normalized_options") if isinstance(_md.get("normalized_options"), dict) else {}
            _blob = str(_no.get("position_type") or _md.get("position_type") or "").lower()
            _credit = "credit" in _blob
            _entry_econ = _md.get("trade_economics_entry") if isinstance(_md.get("trade_economics_entry"), dict) else {}
            _econ = _md.get("economic_summary") if isinstance(_md.get("economic_summary"), dict) else {}
            _econ_entry = _econ.get("entry") if isinstance(_econ.get("entry"), dict) else {}
            _econ_exit = _econ.get("exit") if isinstance(_econ.get("exit"), dict) else {}
            _open_fees = _entry_econ.get("opening_fees")
            if _open_fees is None:
                _open_fees = _econ_entry.get("opening_fees")
            _close_fees = _econ_exit.get("closing_fees")
            _cost = _entry_econ.get("total_cost_basis")
            if _cost is None:
                _cost = _econ_entry.get("total_cost_basis")
            if entry_premium > 0 and exit_premium > 0 and qty > 0:
                _net_frac = net_result_pnl_fraction(
                    entry_fill=float(entry_premium),
                    exit_fill=float(exit_premium),
                    quantity=int(qty),
                    is_credit_spread=_credit,
                    opening_fees=_open_fees,
                    closing_fees=_close_fees,
                    cost_basis=float(_cost) if _cost is not None else None,
                )
                if _net_frac is not None:
                    pnl_pct = float(_net_frac)
                    current_pnl_pct_liq = float(_net_frac)
                    pnl_pct_display = pnl_pct * 100.0
                    display_dollars = net_result_pnl_dollars(
                        entry_fill=float(entry_premium),
                        exit_fill=float(exit_premium),
                        quantity=int(qty),
                        is_credit_spread=_credit,
                        opening_fees=_open_fees,
                        closing_fees=_close_fees,
                    )
        except Exception:
            display_dollars = realized
        pnl_icon = "🔰" if display_dollars >= 0 else "📉"
        bal_line = ""
        if b.get("include_account_balance") and account_balance is not None:
            bal_line = f"\n          Account Balance: {_fmt_usd(account_balance)}"
        prefix = f"{idx}) " if numbered else ""
        leg_line = self._format_orb_0dte_closed_contract_line(
            int(b.get("priority_rank") or 0),
            qty,
            str(b["symbol"]),
            str(b["option_side"]),
            entry_premium,
        )
        exit_premium_line = self._format_trendline_closed_exit_premium_line(
            entry_premium, exit_premium
        )
        cal_dte = b.get("calendar_dte")
        if cal_dte is None:
            cal_dte = b.get("dte")
        exp_block = ""
        if b.get("expiry_ymd") or b.get("selected_expiry_ymd") or cal_dte is not None:
            exp_disp = escape(str(b.get("expiry_ymd") or b.get("selected_expiry_ymd") or ""))
            dte_disp = "n/a" if cal_dte is None else str(cal_dte)
            exp_block = f"\n          DTE: {dte_disp} • Expiry: {exp_disp}"
        return (
            f"{prefix}{pnl_icon} <b>{_fmt_pct_suffix(pnl_pct_display)} {_fmt_usd_signed(display_dollars)}</b>\n"
            f"          <b>{escape(leg_line)}</b>\n"
            f"          {deployed_line}\n"
            f"          {exit_premium_line}"
            f"{exp_block}\n"
            f"          Entry $: {_fmt_usd(entry_underlying)}\n"
            f"          Exit $: {_fmt_usd(exit_underlying)} ({_fmt_pct_suffix(underlying_move_pct * 100.0)})\n"
            f"          Max P&L (Bid): {_fmt_pct_suffix(max_pnl_pct * 100.0)}\n"
            f"          Exit P&L (liq): {_fmt_pct_suffix(current_pnl_pct_liq * 100.0)}\n"
            f"          Quote Source: {quote_source}\n"
            f"          Quote Age: {quote_age:.1f}s\n"
            f"          Mark Quality: {mark_quality}\n"
            f"          Exit Grade: {str(mark_is_exit_grade).lower()}\n"
            f"          Trade ID: {tid}\n"
            f"          Reason: {rsn}"
            f"{self._exit_alert_after_reason_tags(block=b, reason=b.get('reason'), metadata=b.get('metadata') if isinstance(b.get('metadata'), dict) else None, pnl_pct=pnl_pct)}"
            f"{bal_line}"
        )

    def _format_option_closed_blocks(
        self,
        *,
        heading_html: str,
        mode: str,
        blocks: List[Dict[str, Any]],
        account_balance: Optional[float] = None,
        summary_line_html: Optional[str] = None,
    ) -> str:
        """Shared OPTION(S) CLOSED layout (ORB1 / ORB2 / Trendline). Blank line between trades."""
        head_esc = _telegram_html_escape(heading_html)
        lines: List[str] = [
            "====================================================================",
            "",
            f"🔮 <b>{head_esc}</b>\n{_alert_mode_header_line(mode)}",
            "",
        ]
        if summary_line_html:
            lines.append(summary_line_html)
            lines.append("")
        numbered = len(blocks) > 1 or (
            bool(blocks) and int(blocks[0].get("idx") or 1) != 1
        )
        strategy_ids = {
            str(b.get("strategy_id") or "").strip()
            for b in blocks
            if str(b.get("strategy_id") or "").strip()
        }
        show_groups = len(strategy_ids) > 1
        prev_group = None
        for i, b in enumerate(blocks):
            group = str(b.get("strategy_group") or "").strip() if show_groups else ""
            if group and group != prev_group:
                if i > 0:
                    lines.append("")
                lines.append(group)
                lines.append("")
                prev_group = group
            elif i > 0:
                lines.append("")
            lines.append(
                self._format_one_closed_trade_block(
                    b, numbered=numbered, account_balance=account_balance
                )
            )
        if account_balance is not None and len(blocks) == 1 and not blocks[0].get("include_account_balance"):
            lines.append("")
            lines.append(f"          Account Balance: {_fmt_usd(account_balance)}")
        elif account_balance is not None and len(blocks) > 1:
            lines.append("")
            lines.append(f"          Account Balance: {_fmt_usd(account_balance)}")
        return "\n".join(lines)

    def _pack_batch_closed_messages(
        self,
        *,
        heading_html: str,
        mode: str,
        blocks: List[Dict[str, Any]],
        account_balance: Optional[float] = None,
        summary_line_html: Optional[str] = None,
        eod_footer: str = "",
        max_len: int = 0,
    ) -> List[str]:
        """Split a long batch close into continuation Telegrams (whole trades only)."""
        if not blocks:
            return []
        cap = int(max_len or (TELEGRAM_SAFE_MESSAGE_LEN - 120))
        cap = max(900, cap)
        n = len(blocks)

        def _render(start: int, end: int) -> str:
            if start == 0:
                summary = summary_line_html
            else:
                summary = f"💼 <b>Continued:</b> {start + 1}–{end} of {n}"
            bal = account_balance if end >= n else None
            msg = self._format_option_closed_blocks(
                heading_html=heading_html,
                mode=mode,
                blocks=blocks[start:end],
                account_balance=bal,
                summary_line_html=summary,
            )
            if end >= n and eod_footer:
                msg = f"{msg}{eod_footer}"
            return msg

        messages: List[str] = []
        start = 0
        while start < n:
            best_take = 1
            best_msg = _render(start, start + 1)
            for take in range(1, n - start + 1):
                msg = _render(start, start + take)
                if len(msg) <= cap:
                    best_take = take
                    best_msg = msg
                    continue
                if take == 1:
                    best_take = 1
                    best_msg = msg
                break
            messages.append(best_msg)
            start += best_take
            if len(messages) > 40:
                if start < n:
                    messages.append(_render(start, n))
                break
        return messages


    @staticmethod
    def _exit_alert_moon_line(*, pnl_pct: Any = None) -> str:
        """Optional ``Moon: ⭐️`` row when realized premium PnL > +200%."""
        try:
            pct = float(pnl_pct)
        except (TypeError, ValueError):
            return ""
        if pct > 2.0:
            return "          Moon: ⭐️"
        return ""

    @staticmethod
    def _exit_alert_after_reason_tags(
        *,
        metadata: Optional[Dict[str, Any]] = None,
        reason: Any = None,
        block: Optional[Dict[str, Any]] = None,
        pnl_pct: Any = None,
    ) -> str:
        """Optional Liq Cap then Moon after Reason. Leading newline if any tag applies."""
        row: Dict[str, Any] = {}
        if isinstance(block, dict):
            row.update(block)
        if isinstance(metadata, dict):
            row.setdefault("metadata", metadata)
            if not isinstance(row.get("execution_risk"), dict) and isinstance(
                metadata.get("execution_risk"), dict
            ):
                row["execution_risk"] = metadata["execution_risk"]
        if pnl_pct is None and isinstance(block, dict):
            pnl_pct = block.get("pnl_pct")
        parts: List[str] = []
        liq = PrimeAlertManager._liquidity_guard_cap_alert_line(row)
        if liq:
            parts.append(f"          {liq}")
        moon = PrimeAlertManager._exit_alert_moon_line(pnl_pct=pnl_pct).strip()
        if moon:
            parts.append(moon if moon.startswith(" ") else f"          {moon}")
        if not parts:
            return ""
        return "\n" + "\n".join(parts)

    async def send_strategy_position_exit_alert(
        self,
        strategy_id: str,
        *,
        symbol: str,
        trade_id: str,
        reason: str,
        realized_pnl: float,
        pnl_pct: float,
        account_balance: float,
        quantity: Optional[int] = None,
        option_side: Optional[str] = None,
        strike_price: Optional[float] = None,
        entry_underlying: Optional[float] = None,
        estimated_total_cost: Optional[float] = None,
        delta_at_entry: Optional[float] = None,
        delta_at_exit: Optional[float] = None,
        entry_premium: Optional[float] = None,
        exit_premium: Optional[float] = None,
        exit_underlying: Optional[float] = None,
        mode: str = "DEMO",
        account_equity_at_entry: Optional[float] = None,
        entry_chain_source: Optional[str] = None,
        mark_is_exit_grade: Optional[bool] = None,
        exit_premium_adjusted: Optional[bool] = None,
        exit_premium_adjustment_reason: Optional[str] = None,
        modeled_premium_at_exit: Optional[float] = None,
        max_pnl_pct: Optional[float] = None,
        current_pnl_pct_liquidation: Optional[float] = None,
        quote_source: Optional[str] = None,
        quote_age_seconds: Optional[float] = None,
        mark_quality: Optional[str] = None,
        trendline_slot: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        is_0dte: Optional[bool] = None,
        calendar_dte: Any = None,
    ) -> bool:
        """Shared position-closed Telegram for ORB1 / ORB2 / Trendline."""
        try:
            sid = _normalize_strategy_alert_id(strategy_id)
            dedupe_attr = _strategy_exit_dedupe_attr(sid)
            if not hasattr(self, dedupe_attr):
                setattr(self, dedupe_attr, set())
            dedupe_set = getattr(self, dedupe_attr)
            tid_alert = str(trade_id or "").strip()
            log_tag = {
                "orb1": "ORB_0DTE_OPTION_EXIT",
                "breakout": "BREAKOUT_OPTION_EXIT",
                "trendline": "TRENDLINE_OPTION_EXIT",
            }.get(sid, "STRATEGY_OPTION_EXIT")
            if tid_alert and tid_alert in dedupe_set:
                log.info(
                    "%s_ALERT_DEDUPED | symbol=%s | trade_id=%s | reason=telegram_already_sent",
                    log_tag,
                    str(symbol or ""),
                    tid_alert,
                )
                return True

            title_row: Dict[str, Any] = {}
            if isinstance(metadata, dict):
                title_row.update(metadata)
            if trendline_slot:
                title_row["trendline_slot"] = trendline_slot
            exit_title = _strategy_exit_alert_title(
                sid, title_row, is_0dte=is_0dte, calendar_dte=calendar_dte
            )
            qty = int(quantity) if quantity is not None else 0
            opt_side = str(option_side or "N/A").upper()
            est_cost_v = float(estimated_total_cost) if estimated_total_cost is not None else 0.0
            entry_prem_v = float(entry_premium) if entry_premium is not None else 0.0
            exit_prem_v = float(exit_premium) if exit_premium is not None else 0.0
            if entry_prem_v > 0 and qty > 0:
                is_credit = False
                if isinstance(metadata, dict):
                    no = metadata.get("normalized_options") if isinstance(metadata.get("normalized_options"), dict) else {}
                    blob = str(no.get("position_type") or metadata.get("position_type") or "").lower()
                    is_credit = "credit" in blob
                try:
                    from modules.options_trade_economics import (
                        net_result_pnl_dollars,
                        net_result_pnl_fraction,
                        resolve_true_to_live_exit_fill,
                    )

                    md = metadata if isinstance(metadata, dict) else {}
                    exit_prem_v = resolve_true_to_live_exit_fill(
                        exit_fill=exit_prem_v if exit_prem_v > 0 else float(exit_premium or 0.0),
                        metadata=md,
                        mode=str(mode or ""),
                    )
                    if exit_prem_v <= 0:
                        raise ValueError("no_exit_fill")
                    entry_econ = md.get("trade_economics_entry") if isinstance(md.get("trade_economics_entry"), dict) else {}
                    econ_sum = md.get("economic_summary") if isinstance(md.get("economic_summary"), dict) else {}
                    econ_entry = econ_sum.get("entry") if isinstance(econ_sum.get("entry"), dict) else {}
                    econ_exit = econ_sum.get("exit") if isinstance(econ_sum.get("exit"), dict) else {}
                    open_fees = entry_econ.get("opening_fees")
                    if open_fees is None:
                        open_fees = econ_entry.get("opening_fees")
                    close_fees = econ_exit.get("closing_fees")
                    cost_basis = entry_econ.get("total_cost_basis")
                    if cost_basis is None:
                        cost_basis = econ_entry.get("total_cost_basis")
                    net_dollars = net_result_pnl_dollars(
                        entry_fill=entry_prem_v,
                        exit_fill=exit_prem_v,
                        quantity=qty,
                        is_credit_spread=is_credit,
                        opening_fees=open_fees,
                        closing_fees=close_fees,
                    )
                    net_frac = net_result_pnl_fraction(
                        entry_fill=entry_prem_v,
                        exit_fill=exit_prem_v,
                        quantity=qty,
                        is_credit_spread=is_credit,
                        opening_fees=open_fees,
                        closing_fees=close_fees,
                        cost_basis=float(cost_basis) if cost_basis is not None else None,
                    )
                    realized_pnl = float(net_dollars)
                    if net_frac is not None:
                        pnl_pct = float(net_frac)
                except Exception:
                    pass
            pnl_pct_display = float(pnl_pct) * 100.0
            pnl_icon = "🔰" if realized_pnl >= 0 else "📉"
            entry_u = float(entry_underlying) if entry_underlying is not None else 0.0
            exit_u = float(exit_underlying) if exit_underlying is not None else 0.0
            if exit_u <= 0.0:
                try:
                    from modules.underlying_freshness import resolve_exit_underlying_price

                    exit_u = float(
                        resolve_exit_underlying_price(
                            live_underlying=exit_u,
                            metadata=metadata if isinstance(metadata, dict) else None,
                            entry_underlying=entry_u,
                            symbol=symbol,
                        )
                        or 0.0
                    )
                except Exception:
                    pass
            if entry_u <= 0.0 and exit_u > 0.0:
                # Prefer showing a real exit than blank entry when only exit recovered.
                entry_u = float(entry_u)
            underlying_move_pct = (
                ((exit_u - entry_u) / entry_u * 100.0) if entry_u > 0 and exit_u > 0 else 0.0
            )
            entry_equity = self._resolve_entry_account_equity(
                {},
                account_equity_at_entry=account_equity_at_entry,
                account_balance_post_close=float(account_balance),
                realized_pnl=float(realized_pnl),
            )
            capital_pct = self._deployed_capital_pct_of_equity(est_cost_v, entry_equity)
            deployed_line = self._format_exit_deployed_line(est_cost_v, capital_pct)
            reason_display = escape(normalize_exit_reason(str(reason)))
            tid_esc = escape(_trade_id_text(trade_id))
            leg_line = escape(
                self._format_option_contract_line(qty, str(symbol), opt_side, entry_prem_v)
            )
            exit_premium_line = self._format_trendline_closed_exit_premium_line(
                entry_prem_v, exit_prem_v
            )
            max_pnl_v = float(max_pnl_pct) if max_pnl_pct is not None else 0.0
            try:
                from modules.profit_retention_telemetry import (
                    display_max_bid_pnl_fraction,
                    liquidatable_pnl_to_fraction,
                    premium_hwm_from_exit_metadata,
                )

                engine_mfe = None
                if isinstance(metadata, dict):
                    osnap_m = metadata.get("option_stealth")
                    if isinstance(osnap_m, dict) and osnap_m.get("max_pnl_pct") is not None:
                        engine_mfe = float(osnap_m.get("max_pnl_pct"))
                converted = liquidatable_pnl_to_fraction(
                    max_pnl_v, engine_fraction=engine_mfe
                )
                if converted is not None:
                    max_pnl_v = float(converted)
                max_pnl_v = display_max_bid_pnl_fraction(
                    max_pnl_v,
                    engine_fraction=engine_mfe,
                    entry_premium=entry_prem_v,
                    premium_hwm=premium_hwm_from_exit_metadata(metadata),
                )
            except (TypeError, ValueError, ImportError):
                pass
            exit_pnl_liq_v = (
                float(current_pnl_pct_liquidation)
                if current_pnl_pct_liquidation is not None
                else float(pnl_pct)
            )
            try:
                from modules.profit_retention_telemetry import reconcile_exit_liq_fraction

                reconciled = reconcile_exit_liq_fraction(
                    exit_pnl_liq_v,
                    entry_premium=entry_prem_v,
                    exit_premium=exit_prem_v,
                )
                if reconciled is not None:
                    exit_pnl_liq_v = float(reconciled)
            except (TypeError, ValueError, ImportError):
                pass
            if entry_prem_v > 0 and exit_prem_v > 0:
                exit_pnl_liq_v = float(pnl_pct)
            quote_src_v = escape(
                PrimeAlertManager._quote_source_display(quote_source or "unknown")
            )
            quote_age_v = float(quote_age_seconds or 0.0)
            mark_quality_v = escape(str(mark_quality or "unknown"))
            detail_lines: List[str] = [
                f"          Max P&L (Bid): {_fmt_pct_suffix(max_pnl_v * 100.0)}",
                f"          Exit P&L (liq): {_fmt_pct_suffix(exit_pnl_liq_v * 100.0)}",
                f"          Quote Source: {quote_src_v}",
                f"          Quote Age: {quote_age_v:.1f}s",
                f"          Mark Quality: {mark_quality_v}",
            ]
            chain_src = PrimeAlertManager._execution_chain_source_display(
                entry_chain_source
            )
            if chain_src:
                detail_lines.append(f"          Entry Chain: {escape(chain_src)}")
            if mark_is_exit_grade is not None:
                detail_lines.append(
                    f"          Exit Grade: {str(bool(mark_is_exit_grade)).lower()}"
                )
            if exit_premium_adjusted:
                adj_reason = escape(str(exit_premium_adjustment_reason or "adjusted"))
                detail_lines.append(f"          P&amp;L Mark: conservative ({adj_reason})")
            if modeled_premium_at_exit is not None:
                try:
                    detail_lines.append(
                        f"          Modeled Exit Premium: {_fmt_usd(float(modeled_premium_at_exit))}"
                    )
                except (TypeError, ValueError):
                    pass
            detail_block = "\n".join(detail_lines)
            _expiry = _collect_expiry_fields_for_exit_alert(
                metadata if isinstance(metadata, dict) else {},
                is_0dte=is_0dte,
                calendar_dte=calendar_dte,
                trade_id=trade_id,
            )
            _cal_dte = _expiry.get("calendar_dte")
            if _cal_dte is None:
                _cal_dte = _expiry.get("dte")
            _exp = (
                _expiry.get("expiry_ymd")
                or _expiry.get("selected_expiry_ymd")
                or _expiry.get("expiration_ymd")
            )
            _exp_block = ""
            if _exp or _cal_dte is not None:
                _exp_block = (
                    f"          DTE: {'n/a' if _cal_dte is None else str(_cal_dte)} • "
                    f"Expiry: {escape(str(_exp or ''))}\n"
                )
            message = (
                "====================================================================\n"
                "\n"
                f"{exit_title}\n{_alert_mode_header_line(mode)}\n"
                "\n"
                f"{pnl_icon} <b>{_fmt_pct_suffix(pnl_pct_display)} {_fmt_usd_signed(realized_pnl)}</b>\n"
                f"          <b>{leg_line}</b>\n"
                f"          {deployed_line}\n"
                f"          {exit_premium_line}\n"
                f"{_exp_block}"
                f"          Entry $: {_fmt_usd(entry_u)}\n"
                f"          Exit $: {_fmt_usd(exit_u)} ({_fmt_pct_suffix(underlying_move_pct)})\n"
                f"{detail_block}\n"
                f"          Trade ID: {tid_esc}\n"
                f"          Reason: {reason_display}"
                f"{self._exit_alert_after_reason_tags(metadata=metadata if isinstance(metadata, dict) else None, reason=reason, pnl_pct=pnl_pct)}"
                f"\n          Account Balance: {_fmt_usd(account_balance)}"
            )

            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            strategy_label = _strategy_log_tag(sid)
            if success:
                if tid_alert:
                    dedupe_set.add(tid_alert)
                log.info(
                    "%s_ALERT_SENT | symbol=%s | trade_id=%s | pnl_dollar=%.2f | reason=%s",
                    log_tag,
                    symbol,
                    trade_id,
                    float(realized_pnl),
                    str(reason),
                )
                log_option_exit_diagnosis(
                    stage="telegram_sent",
                    strategy=strategy_label,
                    trade_id=str(trade_id or ""),
                    symbol=str(symbol or ""),
                    reason=str(reason or ""),
                    pnl_pct=float(pnl_pct),
                    pnl_dollar=float(realized_pnl),
                    alert_sent=True,
                )
            else:
                log_option_exit_diagnosis(
                    stage="telegram_failed",
                    strategy=strategy_label,
                    trade_id=str(trade_id or ""),
                    symbol=str(symbol or ""),
                    reason=str(reason or ""),
                    pnl_pct=float(pnl_pct),
                    pnl_dollar=float(realized_pnl),
                    alert_sent=False,
                )
            return success
        except Exception as e:
            log.error("Error sending strategy position exit alert (%s): %s", strategy_id, e)
            return False

    async def send_trendline_position_exit_alert(
        self,
        *,
        symbol: str,
        trade_id: str,
        reason: str,
        realized_pnl: float,
        pnl_pct: float,
        account_balance: float,
        quantity: Optional[int] = None,
        option_side: Optional[str] = None,
        strike_price: Optional[float] = None,
        entry_underlying: Optional[float] = None,
        estimated_total_cost: Optional[float] = None,
        delta_at_entry: Optional[float] = None,
        delta_at_exit: Optional[float] = None,
        entry_premium: Optional[float] = None,
        exit_premium: Optional[float] = None,
        exit_underlying: Optional[float] = None,
        mode: str = "DEMO",
        account_equity_at_entry: Optional[float] = None,
        entry_chain_source: Optional[str] = None,
        mark_is_exit_grade: Optional[bool] = None,
        exit_premium_adjusted: Optional[bool] = None,
        exit_premium_adjustment_reason: Optional[str] = None,
        modeled_premium_at_exit: Optional[float] = None,
        max_pnl_pct: Optional[float] = None,
        current_pnl_pct_liquidation: Optional[float] = None,
        quote_source: Optional[str] = None,
        quote_age_seconds: Optional[float] = None,
        mark_quality: Optional[str] = None,
        trendline_slot: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Trendline close — shared strategy exit contract."""
        return await self.send_strategy_position_exit_alert(
            "trendline",
            symbol=symbol,
            trade_id=trade_id,
            reason=reason,
            realized_pnl=realized_pnl,
            pnl_pct=pnl_pct,
            account_balance=account_balance,
            quantity=quantity,
            option_side=option_side,
            strike_price=strike_price,
            entry_underlying=entry_underlying,
            estimated_total_cost=estimated_total_cost,
            delta_at_entry=delta_at_entry,
            delta_at_exit=delta_at_exit,
            entry_premium=entry_premium,
            exit_premium=exit_premium,
            exit_underlying=exit_underlying,
            mode=mode,
            account_equity_at_entry=account_equity_at_entry,
            entry_chain_source=entry_chain_source,
            mark_is_exit_grade=mark_is_exit_grade,
            exit_premium_adjusted=exit_premium_adjusted,
            exit_premium_adjustment_reason=exit_premium_adjustment_reason,
            modeled_premium_at_exit=modeled_premium_at_exit,
            max_pnl_pct=max_pnl_pct,
            current_pnl_pct_liquidation=current_pnl_pct_liquidation,
            quote_source=quote_source,
            quote_age_seconds=quote_age_seconds,
            mark_quality=mark_quality,
            trendline_slot=trendline_slot,
            metadata=metadata,
        )


    @staticmethod
    def _position_entry_chain_source(position: Dict[str, Any]) -> Optional[str]:
        """Best-effort entry chain_source from closed-position metadata."""
        if not isinstance(position, dict):
            return None
        meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
        for key in ("entry_chain_source", "chain_source"):
            raw = meta.get(key)
            if raw:
                return str(raw)
        ep = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
        for key in ("chain_source", "chain_source_used", "entry_chain_source"):
            raw = ep.get(key)
            if raw:
                return str(raw)
        return None

    async def send_breakout_position_exit_alert(
        self,
        *,
        symbol: str,
        trade_id: str,
        reason: str,
        realized_pnl: float,
        pnl_pct: float,
        account_balance: float,
        quantity: Optional[int] = None,
        option_side: Optional[str] = None,
        strike_price: Optional[float] = None,
        entry_underlying: Optional[float] = None,
        estimated_total_cost: Optional[float] = None,
        delta_at_entry: Optional[float] = None,
        delta_at_exit: Optional[float] = None,
        entry_premium: Optional[float] = None,
        exit_premium: Optional[float] = None,
        exit_underlying: Optional[float] = None,
        mode: str = "DEMO",
        account_equity_at_entry: Optional[float] = None,
        entry_chain_source: Optional[str] = None,
        mark_is_exit_grade: Optional[bool] = None,
        exit_premium_adjusted: Optional[bool] = None,
        exit_premium_adjustment_reason: Optional[str] = None,
        modeled_premium_at_exit: Optional[float] = None,
        max_pnl_pct: Optional[float] = None,
        current_pnl_pct_liquidation: Optional[float] = None,
        quote_source: Optional[str] = None,
        quote_age_seconds: Optional[float] = None,
        mark_quality: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """ORB2 Breakout close — shared strategy exit contract."""
        return await self.send_strategy_position_exit_alert(
            "breakout",
            symbol=symbol,
            trade_id=trade_id,
            reason=reason,
            realized_pnl=realized_pnl,
            pnl_pct=pnl_pct,
            account_balance=account_balance,
            quantity=quantity,
            option_side=option_side,
            strike_price=strike_price,
            entry_underlying=entry_underlying,
            estimated_total_cost=estimated_total_cost,
            delta_at_entry=delta_at_entry,
            delta_at_exit=delta_at_exit,
            entry_premium=entry_premium,
            exit_premium=exit_premium,
            exit_underlying=exit_underlying,
            mode=mode,
            account_equity_at_entry=account_equity_at_entry,
            entry_chain_source=entry_chain_source,
            mark_is_exit_grade=mark_is_exit_grade,
            exit_premium_adjusted=exit_premium_adjusted,
            exit_premium_adjustment_reason=exit_premium_adjustment_reason,
            modeled_premium_at_exit=modeled_premium_at_exit,
            max_pnl_pct=max_pnl_pct,
            current_pnl_pct_liquidation=current_pnl_pct_liquidation,
            quote_source=quote_source,
            quote_age_seconds=quote_age_seconds,
            mark_quality=mark_quality,
            metadata=metadata,
        )


    async def send_options_position_exit_alert(
        self,
        position: Dict[str, Any],
        exit_price: float,
        exit_reason: str,
        holding_time_minutes: int = 0,
        mode: str = "DEMO",
        *,
        realized_pnl_override: Optional[float] = None,
        pnl_pct_fraction_override: Optional[float] = None,
        account_balance_override: Optional[float] = None,
        delta_at_entry_override: Optional[float] = None,
        delta_at_exit_override: Optional[float] = None,
    ) -> bool:
        """
        Send individual Easy ORB1 Breakout options exit alert (Trendline-style OPTION CLOSED layout).

        Optional overrides align Telegram rows with post-close accounting (demo mock + credit spreads).
        holding_time_minutes is accepted for API compatibility but is not shown in this layout.
        """
        try:
            bal = account_balance_override
            block = self._options_exit_alert_block_from_position(
                position,
                idx=1,
                exit_reason=str(exit_reason),
                exit_price=float(exit_price),
                include_account_balance=bal is not None,
                realized_pnl_override=realized_pnl_override,
                pnl_pct_fraction_override=pnl_pct_fraction_override,
                delta_at_entry_override=delta_at_entry_override,
                delta_at_exit_override=delta_at_exit_override,
                account_balance_post_close=bal,
            )
            tid_alert = str(block.get("trade_id") or position.get("position_id") or "").strip()
            # Breakout before Trendline — breakout fills reuse TL-compatible metadata and
            # must not be falsely claimed as trendline_owned (silent alert skip).
            if _position_dict_is_breakout_0dte(position):
                meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
                osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
                entry_equity = None
                raw_eq = meta.get("account_equity_at_entry")
                if raw_eq is not None:
                    try:
                        entry_equity = float(raw_eq)
                    except (TypeError, ValueError):
                        entry_equity = None
                mark_grade = block.get("mark_is_exit_grade")
                return await self.send_breakout_position_exit_alert(
                    symbol=str(block.get("symbol") or position.get("symbol") or ""),
                    trade_id=tid_alert or str(position.get("position_id") or ""),
                    reason=str(exit_reason),
                    realized_pnl=float(block.get("realized_pnl") or 0.0),
                    pnl_pct=float(block.get("pnl_pct") or 0.0),
                    account_balance=float(bal if bal is not None else 0.0),
                    quantity=int(block.get("quantity") or 0),
                    option_side=str(block.get("option_side") or ""),
                    strike_price=float(block.get("strike") or 0.0) or None,
                    entry_underlying=float(block.get("entry_underlying") or 0.0) or None,
                    estimated_total_cost=float(
                        block.get("deployed_capital") or block.get("est_cost") or 0.0
                    ),
                    delta_at_entry=float(block.get("delta0") or 0.0),
                    delta_at_exit=float(block.get("delta1") or block.get("delta0") or 0.0),
                    entry_premium=float(block.get("entry_premium") or 0.0) or None,
                    exit_premium=float(block.get("exit_premium") or exit_price or 0.0) or None,
                    exit_underlying=float(block.get("exit_underlying") or 0.0) or None,
                    mode=mode,
                    account_equity_at_entry=entry_equity,
                    entry_chain_source=self._position_entry_chain_source(position),
                    mark_is_exit_grade=bool(mark_grade) if mark_grade is not None else None,
                    max_pnl_pct=float(block.get("max_pnl_pct") or 0.0),
                    current_pnl_pct_liquidation=float(
                        block.get("current_pnl_pct_liquidation") or block.get("pnl_pct") or 0.0
                    ),
                    quote_source=str(block.get("quote_source") or osnap.get("last_premium_source") or "unknown"),
                    quote_age_seconds=float(block.get("quote_age_seconds") or 0.0),
                    mark_quality=str(block.get("mark_quality") or "unknown"),
                    metadata=meta if meta else None,
                )
            if _position_dict_is_trendline_0dte(position):
                log.info(
                    "ORB_0DTE_OPTION_EXIT_ALERT_SKIPPED | symbol=%s | trade_id=%s | reason=trendline_owned_path",
                    str(block.get("symbol") or ""),
                    tid_alert,
                )
                return True
            tl_sent = getattr(self, "_trendline_exit_alert_trade_ids", None) or set()
            if tid_alert and tid_alert in tl_sent:
                log.info(
                    "ORB_0DTE_OPTION_EXIT_ALERT_DEDUPED | symbol=%s | trade_id=%s | reason=trendline_alert_already_sent",
                    str(block.get("symbol") or ""),
                    tid_alert,
                )
                return True
            bo_sent = getattr(self, "_breakout_exit_alert_trade_ids", None) or set()
            if tid_alert and tid_alert in bo_sent:
                log.info(
                    "ORB_0DTE_OPTION_EXIT_ALERT_DEDUPED | symbol=%s | trade_id=%s | reason=breakout_alert_already_sent",
                    str(block.get("symbol") or ""),
                    tid_alert,
                )
                return True
            # Dedupes for ORB1 live inside send_strategy_position_exit_alert.
            meta = position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
            osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
            entry_equity = None
            raw_eq = meta.get("account_equity_at_entry")
            if raw_eq is not None:
                try:
                    entry_equity = float(raw_eq)
                except (TypeError, ValueError):
                    entry_equity = None
            mark_grade = block.get("mark_is_exit_grade")
            return await self.send_strategy_position_exit_alert(
                "orb1",
                symbol=str(block.get("symbol") or position.get("symbol") or ""),
                trade_id=tid_alert or str(position.get("position_id") or ""),
                reason=str(exit_reason),
                realized_pnl=float(block.get("realized_pnl") or 0.0),
                pnl_pct=float(block.get("pnl_pct") or 0.0),
                account_balance=float(bal if bal is not None else 0.0),
                quantity=int(block.get("quantity") or 0),
                option_side=str(block.get("option_side") or ""),
                strike_price=float(block.get("strike") or 0.0) or None,
                entry_underlying=float(block.get("entry_underlying") or 0.0) or None,
                estimated_total_cost=float(
                    block.get("deployed_capital") or block.get("est_cost") or 0.0
                ),
                delta_at_entry=float(block.get("delta0") or 0.0),
                delta_at_exit=float(block.get("delta1") or block.get("delta0") or 0.0),
                entry_premium=float(block.get("entry_premium") or 0.0) or None,
                exit_premium=float(block.get("exit_premium") or exit_price or 0.0) or None,
                exit_underlying=float(block.get("exit_underlying") or 0.0) or None,
                mode=mode,
                account_equity_at_entry=entry_equity,
                entry_chain_source=self._position_entry_chain_source(position),
                mark_is_exit_grade=bool(mark_grade) if mark_grade is not None else None,
                max_pnl_pct=float(block.get("max_pnl_pct") or 0.0),
                current_pnl_pct_liquidation=float(
                    block.get("current_pnl_pct_liquidation") or block.get("pnl_pct") or 0.0
                ),
                quote_source=str(block.get("quote_source") or osnap.get("last_premium_source") or "unknown"),
                quote_age_seconds=float(block.get("quote_age_seconds") or 0.0),
                mark_quality=str(block.get("mark_quality") or "unknown"),
                metadata=meta if meta else None,
            )

        except Exception as e:
            log.error(f"Error sending Options Position Exit alert: {e}")
            return False
    
    async def send_options_aggregated_exit_alert(
        self,
        closed_positions: List[Dict[str, Any]],
        exit_reason: str,
        mode: str = "DEMO",
        *,
        account_balance: Optional[float] = None,
        eod_flatten_status: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send one Batch Order Closed Telegram covering every strategy closed together.

        Used for: EOD flatten, emergency exits, batch exits (ORB1 + ORB2 + Trendline).
        Oversized books split into continuation messages instead of truncating.
        """
        try:
            if not closed_positions:
                return False

            ordered = sorted(
                [p for p in closed_positions if isinstance(p, dict)],
                key=_closed_position_batch_sort_key,
            )
            if not ordered:
                return False

            blocks: List[Dict[str, Any]] = []
            for i, pos in enumerate(ordered, 1):
                exit_price = float(pos.get("current_value") or pos.get("entry_price") or 0.0)
                blocks.append(
                    self._options_exit_alert_block_from_position(
                        pos,
                        idx=i,
                        exit_reason=normalize_exit_reason(str(exit_reason)) or str(exit_reason),
                        exit_price=exit_price,
                        include_account_balance=False,
                        account_balance_post_close=account_balance,
                    )
                )

            total_pnl = sum(float(b.get("realized_pnl") or 0.0) for b in blocks)
            summary = f"💼 <b>Closed:</b> {len(blocks)} • <b>Total PnL:</b> {_fmt_usd_signed(total_pnl)}"
            heading = "Batch Order Closed"
            footer = ""
            if isinstance(eod_flatten_status, dict) and eod_flatten_status:
                fs = eod_flatten_status
                footer = (
                    "\n\n📋 <b>EOD flatten</b> — "
                    f"closed={int(fs.get('positions_closed', 0))} | "
                    f"missing={int(fs.get('positions_missing', 0))} | "
                    f"failed={int(fs.get('positions_failed', 0))} | "
                    f"no_positions_found={str(bool(fs.get('no_positions_found', False))).lower()} | "
                    f"registry_orb_open={int(fs.get('registry_orb_open_count', 0))}"
                )

            parts = self._pack_batch_closed_messages(
                heading_html=heading,
                mode=mode,
                blocks=blocks,
                account_balance=account_balance,
                summary_line_html=summary,
                eod_footer=footer,
            )
            success = False
            for part in parts:
                if await self._send_telegram_message(part, AlertLevel.SUCCESS):
                    success = True

            if success:
                for b in blocks:
                    tid = str(b.get("trade_id") or "").strip()
                    if not tid or tid == "N/A":
                        continue
                    attr = _strategy_exit_dedupe_attr(b.get("strategy_id") or "orb1")
                    sent_ids = getattr(self, attr, None)
                    if isinstance(sent_ids, set):
                        sent_ids.add(tid)
                log.info(
                    "Batch Order Closed alert sent for %d positions in %d message(s) — %s",
                    len(blocks),
                    len(parts),
                    exit_reason,
                )
            if isinstance(eod_flatten_status, dict) and eod_flatten_status:
                fs = eod_flatten_status
                log.info(
                    "OPTIONS_AGGREGATED_EXIT_ALERT_STATUS | exit_reason=%s | telegram_sent=%s | positions_closed=%d | "
                    "positions_missing=%d | positions_failed=%d | no_positions_found=%s | registry_orb_open=%d",
                    str(exit_reason),
                    str(bool(success)).lower(),
                    int(fs.get("positions_closed", 0)),
                    int(fs.get("positions_missing", 0)),
                    int(fs.get("positions_failed", 0)),
                    str(bool(fs.get("no_positions_found", False))).lower(),
                    int(fs.get("registry_orb_open_count", 0)),
                )

            return bool(success)

        except Exception as e:
            log.error(f"Error sending Options Aggregated Exit alert: {e}")
            return False
    
    async def send_options_partial_profit_alert(self, position: Dict[str, Any],
                                               partial_details: Dict[str, Any],
                                               mode: str = "DEMO") -> bool:
        """
        Send Options Partial Profit alert when automated exit triggers (+60% sell 50%, +120% sell 25%)
        
        Args:
            position: OptionsPosition dict
            partial_details: Partial profit details (target_name, target_pct, partial_quantity, partial_profit)
            mode: Trading mode (DEMO or LIVE)
        
        Returns:
            True if alert sent successfully
        """
        try:
            from zoneinfo import ZoneInfo
            
            # Get current time
            pt_tz = ZoneInfo('America/Los_Angeles')
            et_tz = ZoneInfo('America/New_York')
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            
            pt_time = now_pt.strftime('%I:%M %p PT')
            et_time = now_et.strftime('%I:%M %p ET')
            
            symbol = position.get('symbol', 'UNKNOWN')
            position_id = _trade_id_text(position.get('position_id', 'N/A'))
            target_name = partial_details.get('target_name', 'unknown')
            target_pct = partial_details.get('target_pct', 0.0)
            partial_quantity = partial_details.get('partial_quantity', 0)
            remaining_quantity = partial_details.get('remaining_quantity', 0)
            partial_profit = partial_details.get('partial_profit', 0.0)
            
            # Format target name and header
            if target_name == 'first_target_60pct':
                target_label = "First Target (+60%)"
                action_label = "Sold 50%"
                header_text = "FIRST TARGET EXIT"
            elif target_name == 'second_target_120pct':
                target_label = "Second Target (+120%)"
                action_label = "Sold 25%"
                header_text = "SECOND TARGET EXIT"
            else:
                target_label = f"Target (+{target_pct*100:.0f}%)"
                action_label = "Partial Profit"
                header_text = "TARGET EXIT"
            
            message = f"""====================================================================

🔮 💠 <b>{ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} — {header_text}</b>
{_alert_mode_header_line(mode)}
          Time: {pt_time} ({et_time})

1) 💰 <b>{target_label}</b>
          {symbol} Options Position
          • <b>{action_label}:</b> {partial_quantity} contracts
          • <b>Remaining:</b> {remaining_quantity} contracts (runner)
          • <b>Profit Locked:</b> {_fmt_usd_signed(partial_profit)}
          
          <b>Trade ID:</b>
          {position_id}

📊 {ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} — automated exit (partial profit)
"""
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                log.info(f"Options Partial Profit alert sent for {symbol} - {target_label} - Profit: ${partial_profit:.2f}")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending Options Partial Profit alert: {e}")
            return False
    
    async def send_options_runner_exit_alert(self, position: Dict[str, Any],
                                            exit_signal: Dict[str, Any],
                                            mode: str = "DEMO") -> bool:
        """
        Send Options Runner Exit alert when runner exits (VWAP/ORB reclaim or time cutoff)
        
        Args:
            position: OptionsPosition dict
            exit_signal: ExitSignal dict with exit details
            mode: Trading mode (DEMO or LIVE)
        
        Returns:
            True if alert sent successfully
        """
        try:
            from zoneinfo import ZoneInfo
            
            # Get current time
            pt_tz = ZoneInfo('America/Los_Angeles')
            et_tz = ZoneInfo('America/New_York')
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            
            pt_time = now_pt.strftime('%I:%M %p PT')
            et_time = now_et.strftime('%I:%M %p ET')
            
            symbol = position.get('symbol', 'UNKNOWN')
            position_id = _trade_id_text(position.get('position_id', 'N/A'))
            exit_price = exit_signal.get('exit_price', 0.0)
            pnl_pct = exit_signal.get('pnl_pct', 0.0)
            pnl_dollars = exit_signal.get('pnl_dollar', 0.0)
            exit_reasons = exit_signal.get('details', {}).get('exit_reasons', [])
            
            # HTML escape exit reasons to prevent parsing errors (e.g., < characters)
            import html
            
            # Format exit reason (take first reason or default)
            if exit_reasons:
                # Remove "Runner exit: " prefix if present, then escape HTML
                first_reason = str(exit_reasons[0])
                if first_reason.startswith("Runner exit: "):
                    first_reason = first_reason.replace("Runner exit: ", "")
                reason_text = html.escape(first_reason)
            else:
                reason_text = "Runner exit triggered"
            
            # P&L emoji and strings (pnl_pct is fraction; $ sign matches loss/gain)
            pnl_pct_display = pnl_pct * 100
            pnl_emoji = "💰" if pnl_pct_display >= 0 else "📉"
            pnl_pct_str = _fmt_pct_suffix(pnl_pct_display)
            pnl_dollars_str = _fmt_usd_signed(pnl_dollars)
            
            message = f"""====================================================================

🔮 💠 <b>{ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} — RUNNER EXIT</b>
{_alert_mode_header_line(mode)}
          Time: {pt_time} ({et_time})

1) {pnl_emoji} <b>{pnl_pct_str}</b> {pnl_dollars_str}
          {symbol} Options Runner
          <b>Exit Price:</b> {_fmt_usd(exit_price)}
          <b>Reason:</b> {reason_text}
          
          <b>Trade ID:</b>
          {position_id}

📊 {ALERT_LABEL_EASY_ORB_0DTE_OPTIONS} — automated exit (runner)
"""
            
            success = await self._send_telegram_message(message, AlertLevel.SUCCESS)
            
            if success:
                log.info(f"Options Runner Exit alert sent for {symbol} - P&L: {pnl_pct_str} {pnl_dollars_str}")
            
            return success
            
        except Exception as e:
            log.error(f"Error sending Options Runner Exit alert: {e}")
            return False
    
    async def send_options_end_of_day_report(self, daily_stats: Dict[str, Any],
                                            weekly_stats: Dict[str, Any],
                                            all_time_stats: Optional[Dict[str, Any]] = None,
                                            account_balance: float = 1000.0,
                                            starting_balance: float = 1000.0,
                                            mode: str = "DEMO",
                                            eod_marker_key_prefix: str = "options_eod_sent") -> bool:
        """
        Send End-of-Day report for Easy 0DTE options trading
        
        GCS marker skips Cloud Scheduler retries after a successful send
        (job ``end-of-day-report`` → POST /api/end-of-day-report). The trading
        loop does not send this Telegram.
        
        Args:
            daily_stats: Today's options trading statistics
            weekly_stats: This week's options trading statistics
            all_time_stats: All-time options trading statistics (optional)
            account_balance: Current account balance
            starting_balance: Starting balance for % calculation
            mode: Trading mode (DEMO or LIVE)
        
        Returns:
            True if alert sent successfully
        """
        try:
            from zoneinfo import ZoneInfo
            
            # Rev 00260: GCS-based deduplication to prevent duplicate 0DTE EOD reports.
            # Check existence first; claim the marker only AFTER Telegram succeeds.
            # Claiming first used to swallow retries when Cloud Run died mid-send.
            from .eod_report_helpers import eod_marker_exists, pt_trading_date_iso, try_claim_eod_marker
            from .runtime_mode import alert_dedup_service_token

            today = pt_trading_date_iso()
            eod_sent_key = f"{eod_marker_key_prefix}_{alert_dedup_service_token()}_{today}_{mode.lower()}"
            gcs = None
            
            try:
                from .gcs_persistence import get_gcs_persistence
                gcs = get_gcs_persistence()
                
                if eod_marker_exists(gcs, eod_sent_key):
                    log.info(f"✅ 0DTE Options EOD report already sent today ({today}, {mode} mode) - only ONE report per day, skipping duplicate")
                    return True
                
            except Exception as gcs_error:
                log.warning(f"⚠️ GCS deduplication failed for 0DTE Options EOD report: {gcs_error}")
                log.warning(f"   Proceeding without GCS deduplication - potential for duplicate alerts")
            
            from .eod_report_helpers import pt_et_display_times

            pt_time, et_time = pt_et_display_times()
            
            # Calculate stats
            daily_closed = daily_stats.get('positions_closed', 0)
            daily_wins = daily_stats.get('winning_trades', 0)
            daily_losses = daily_stats.get('losing_trades', 0)
            daily_pnl = daily_stats.get('total_pnl', 0.0)
            daily_best = daily_stats.get('best_trade', 0.0)
            daily_worst = daily_stats.get('worst_trade', 0.0)
            
            weekly_closed = weekly_stats.get('positions_closed', 0)
            weekly_wins = weekly_stats.get('winning_trades', 0)
            weekly_losses = weekly_stats.get('losing_trades', 0)
            weekly_pnl = weekly_stats.get('total_pnl', 0.0)
            
            # Calculate rates
            daily_win_rate = (daily_wins / daily_closed * 100) if daily_closed > 0 else 0.0
            weekly_win_rate = (weekly_wins / weekly_closed * 100) if weekly_closed > 0 else 0.0
            
            # Calculate P&L percentages (today/week vs period-start balance; all-time vs seed)
            from .eod_report_helpers import (
                all_time_pnl_pct as _all_time_pnl_pct,
                period_pnl_pct as _period_pnl_pct,
                resolve_eod_account_balance as _resolve_eod_account_balance,
            )

            _all_time_pnl_for_balance = 0.0
            if all_time_stats:
                _all_time_pnl_for_balance = float(all_time_stats.get("total_pnl", 0.0) or 0.0)
            _eod_balance = _resolve_eod_account_balance(
                account_balance, starting_balance, _all_time_pnl_for_balance
            )
            daily_pnl_pct = _period_pnl_pct(daily_pnl, _eod_balance, fallback_base=starting_balance)
            weekly_pnl_pct = _period_pnl_pct(weekly_pnl, _eod_balance, fallback_base=starting_balance)
            
            # Format P&L
            daily_pnl_sign = "+" if daily_pnl >= 0 else ""
            weekly_pnl_sign = "+" if weekly_pnl >= 0 else ""
            
            # Calculate profit factors
            daily_total_wins = daily_stats.get('total_wins_sum', None)
            daily_total_losses = daily_stats.get('total_losses_sum', None)
            
            if daily_total_wins is None or daily_total_losses is None:
                if daily_wins > 0 and daily_losses > 0:
                    avg_win = (daily_pnl / daily_wins) if daily_wins > 0 else 0.0
                    estimated_wins_sum = avg_win * daily_wins
                    avg_loss = abs((daily_pnl - estimated_wins_sum) / daily_losses) if daily_losses > 0 else 0.0
                    daily_total_wins = avg_win * daily_wins
                    daily_total_losses = avg_loss * daily_losses
                elif daily_wins > 0:
                    daily_total_wins = daily_pnl
                    daily_total_losses = 0.0
                elif daily_losses > 0:
                    daily_total_wins = 0.0
                    daily_total_losses = abs(daily_pnl)
                else:
                    daily_total_wins = 0.0
                    daily_total_losses = 0.0
            
            daily_profit_factor = (daily_total_wins / daily_total_losses) if daily_total_losses > 0 else (daily_total_wins if daily_total_wins > 0 else (float('inf') if daily_total_wins > 0 and daily_total_losses == 0 else 0.0))
            
            weekly_total_wins = weekly_stats.get('total_wins_sum', None)
            weekly_total_losses = weekly_stats.get('total_losses_sum', None)
            
            if weekly_total_wins is None or weekly_total_losses is None:
                if weekly_wins > 0 and weekly_losses > 0:
                    weekly_avg_win = (weekly_pnl / weekly_wins) if weekly_wins > 0 else 0.0
                    estimated_wins_sum = weekly_avg_win * weekly_wins
                    weekly_avg_loss = abs((weekly_pnl - estimated_wins_sum) / weekly_losses) if weekly_losses > 0 else 0.0
                    weekly_total_wins = weekly_avg_win * weekly_wins
                    weekly_total_losses = weekly_avg_loss * weekly_losses
                elif weekly_wins > 0:
                    weekly_total_wins = weekly_pnl
                    weekly_total_losses = 0.0
                elif weekly_losses > 0:
                    weekly_total_wins = 0.0
                    weekly_total_losses = abs(weekly_pnl)
                else:
                    weekly_total_wins = 0.0
                    weekly_total_losses = 0.0
            
            weekly_profit_factor = (weekly_total_wins / weekly_total_losses) if weekly_total_losses > 0 else (weekly_total_wins if weekly_total_wins > 0 else (float('inf') if weekly_total_wins > 0 and weekly_total_losses == 0 else 0.0))
            
            # All-time stats
            all_time_total_trades = 0
            all_time_wins = 0
            all_time_losses = 0
            all_time_pnl = 0.0
            all_time_profit_factor = 0.0
            all_time_pnl_pct = 0.0
            
            if all_time_stats:
                all_time_total_trades = all_time_stats.get(
                    'total_trades', all_time_stats.get('positions_closed', 0)
                )
                all_time_wins = all_time_stats.get('winning_trades', 0)
                all_time_losses = all_time_stats.get('losing_trades', 0)
                all_time_pnl = all_time_stats.get('total_pnl', 0.0)
                
                all_time_wins_sum = all_time_stats.get('total_wins_sum', 0.0)
                all_time_losses_sum = all_time_stats.get('total_losses_sum', 0.0)
                
                if all_time_wins_sum > 0 and all_time_losses_sum > 0:
                    all_time_profit_factor = all_time_wins_sum / all_time_losses_sum
                elif all_time_wins_sum > 0 and all_time_losses_sum == 0:
                    all_time_profit_factor = float('inf')
                elif all_time_wins_sum == 0 and all_time_losses_sum > 0:
                    all_time_profit_factor = 0.0
                else:
                    if all_time_wins > 0 and all_time_losses > 0:
                        all_time_avg_win = (all_time_pnl / all_time_wins) if all_time_wins > 0 else 0.0
                        estimated_wins_sum = all_time_avg_win * all_time_wins
                        all_time_avg_loss = abs((all_time_pnl - estimated_wins_sum) / all_time_losses) if all_time_losses > 0 else 0.0
                        all_time_total_wins_sum = all_time_avg_win * all_time_wins
                        all_time_total_losses_sum = all_time_avg_loss * all_time_losses
                        all_time_profit_factor = (all_time_total_wins_sum / all_time_total_losses_sum) if all_time_total_losses_sum > 0 else 0.0
                    elif all_time_wins > 0:
                        all_time_profit_factor = float('inf')
                    else:
                        all_time_profit_factor = 0.0
                
                all_time_pnl_pct = _all_time_pnl_pct(all_time_pnl, starting_balance)
            
            all_time_win_rate = (all_time_wins / all_time_total_trades * 100) if all_time_total_trades > 0 else 0.0
            all_time_pnl_sign = "+" if all_time_pnl >= 0 else ""
            display_account_balance = _resolve_eod_account_balance(
                account_balance, starting_balance, all_time_pnl
            )
            
            # Calculate averages
            avg_win = (daily_total_wins / daily_wins) if daily_wins > 0 else 0.0
            avg_loss = -(daily_total_losses / daily_losses) if daily_losses > 0 else 0.0
            
            # Format profit factors
            daily_pf_str = _fmt_profit_factor(daily_profit_factor)
            weekly_pf_str = _fmt_profit_factor(weekly_profit_factor)
            all_time_pf_str = _fmt_profit_factor(all_time_profit_factor)
            
            # Get current date
            report_date = pt_trading_date_iso()
            
            if str(mode).upper() == "LIVE":
                options_header = f"🏦 <b>END-OF-DAY ORB 0DTE</b> | 💰 \n{_alert_mode_header_line(mode)}"
            else:
                options_header = f"🏦 <b>END-OF-DAY ORB 0DTE</b> | 🎮 🍒 \n{_alert_mode_header_line(mode)}"
            
            # Telegram HTML (parse_mode=HTML via _send_telegram_message)
            message = f"""====================================================================

{options_header}

📈 <b>P&L (TODAY):</b>
          <b>{_fmt_pct(daily_pnl_pct)}% {_fmt_usd_signed(daily_pnl)}</b>
          Win Rate: {daily_win_rate:.1f}% • Total Trades: {daily_closed}
          Wins: {daily_wins} • Losses: {daily_losses}
          Profit Factor: {daily_pf_str}
          Average Win: {_fmt_usd(avg_win)}
          Average Loss: {_fmt_usd_signed(avg_loss)}
          Best Trade: {_fmt_usd_signed(daily_best)}
          Worst Trade: {_fmt_usd_signed(daily_worst)}

🎖️⭐ <b>P&L (WEEK M-F):</b>
          <b>{_fmt_pct(weekly_pnl_pct)}% {_fmt_usd_signed(weekly_pnl)}</b>
          Win Rate: {weekly_win_rate:.1f}% • Total Trades: {weekly_closed}
          Profit Factor: {weekly_pf_str}

💎 <b>Account Balances (All Time):</b>
          <b>{_fmt_pct(all_time_pnl_pct)}% {_fmt_usd_signed(all_time_pnl)}</b>
          <b>{_fmt_usd(display_account_balance)}</b>
          Win Rate: {all_time_win_rate:.1f}% • Total Trades: {all_time_total_trades}
          Profit Factor: {all_time_pf_str}
          Wins: {all_time_wins} • Losses: {all_time_losses}

📅 <b>Report Date:</b> {report_date}
          Time: {pt_time} ({et_time})
"""
            
            # Send via Telegram
            success = await self._send_telegram_message(message, AlertLevel.INFO)
            
            if success:
                log.info(f"Options EOD Report sent ({mode} Mode) - Daily: {daily_pnl_sign}${daily_pnl:.2f} ({daily_pnl_sign}{daily_pnl_pct:.2f}%), Weekly: {weekly_pnl_sign}${weekly_pnl:.2f} ({weekly_pnl_sign}{weekly_pnl_pct:.2f}%)")
                try:
                    if gcs is not None and try_claim_eod_marker(gcs, eod_sent_key):
                        log.info(f"📝 Created GCS 0DTE Options EOD marker for {today} ({mode} mode)")
                except Exception as gcs_mark_err:
                    log.warning("⚠️ GCS EOD sent-marker write failed after Telegram: %s", gcs_mark_err)
            
            return success
            
        except Exception as e:
            log.error(f"Error sending Options EOD Report: {e}")
            return False
    
    async def send_scheduled_unified_0dte_eod(self, system) -> bool:
        """
        Combined 0DTE EOD for Cloud Scheduler — one Telegram for ORB + Trendline on unified ledger.
        Header remains END-OF-DAY ORB 0DTE (ORB SO ETF EOD is separate).
        """
        import os

        enable_0dte = os.getenv("ENABLE_0DTE_STRATEGY", "false").lower() == "true"
        enable_tl = os.getenv("ENABLE_TRENDLINE_STRATEGY", "false").lower() == "true"
        if not enable_0dte and not enable_tl:
            log.info("EOD_UNIFIED_0DTE | skip | both strategies disabled")
            return True

        from .eod_report_helpers import resolve_unified_0dte_eod_payload

        payload = resolve_unified_0dte_eod_payload(system)
        log.info("EOD_UNIFIED_0DTE | source=%s | mode=%s", payload.get("source"), payload.get("mode"))
        return await self.send_options_end_of_day_report(
            daily_stats=payload["daily_stats"],
            weekly_stats=payload["weekly_stats"],
            all_time_stats=payload.get("all_time_stats"),
            account_balance=payload["account_balance"],
            starting_balance=payload["starting_balance"],
            mode=payload["mode"],
            eod_marker_key_prefix="unified_0dte_eod_sent",
        )

    async def send_portfolio_protection_alert(
        self,
        trigger_id: str,
        reason: str,
        metrics: Optional[Dict[str, Any]] = None,
        mode: str = "DEMO",
        total_scanned: int = 0,
        orb_signals_count: int = 0,
        dte0_signals_qualified: int = 0,
        orb_symbols: Optional[List[str]] = None,
        dte0_symbols: Optional[List[str]] = None,
        post_close_result: Optional[Dict[str, Any]] = None,
        snapshot_used: bool = False,
        observe_only: bool = False,
    ) -> bool:
        """
        Regime Portfolio Protection alert @ 7:30 PT (Family A only).

        observe_only: trades continue; blocking: SO batch + 0DTE tier policy.
        """
        try:
            from .portfolio_protection import (
                canonical_pp_trigger_code,
                human_pp_trigger_label,
                pp_path_impact_text,
            )

            mode = _resolve_telegram_mode_label(mode)
            trigger_label = human_pp_trigger_label(trigger_id)
            trigger_code = canonical_pp_trigger_code(trigger_id)
            metrics = metrics or {}
            pct_below_vwap = metrics.get("pct_below_vwap")
            median_vol = metrics.get("median_volume_ratio")
            median_vwap = metrics.get("median_vwap_distance")
            composite_risk = metrics.get("composite_risk_score")

            metrics_text = f"""
📊 <b>Pool metrics:</b>
          • Oversold (RSI under 40): {metrics.get('pct_low_rsi', 0):.0f}%
          • Overbought (RSI above 80): {metrics.get('pct_high_rsi', 0):.0f}%
          • Weak volume (below 1.0x): {metrics.get('pct_weak_volume', 0):.0f}%"""
            if pct_below_vwap is not None:
                metrics_text += f"\n          • Below VWAP: {float(pct_below_vwap):.0f}%"

            if median_vol is not None:
                metrics_text += f"\n          • Median volume_ratio: {float(median_vol):.3f}"
            if median_vwap is not None:
                metrics_text += f"\n          • Median VWAP distance: {float(median_vwap):.2f}%"
            if composite_risk is not None:
                metrics_text += f"\n          • Composite risk score: {float(composite_risk):.3f}"
            metrics_text += f"""
          • Avg RSI: {metrics.get('avg_rsi', 0):.1f} | Avg vol: {metrics.get('avg_volume', 0):.2f}x
          • Avg MACD: {metrics.get('avg_macd_histogram', 0):.3f}
          • Avg VWAP dist: {metrics.get('avg_vwap_distance', 0):.2f}%"""

            list_note = ""
            if snapshot_used:
                list_note = "🧾 <b>Note:</b> Using last collected signal snapshot.\n"
            orb_list_text = ""
            if orb_symbols:
                orb_list_text = f"\n📋 <b>ORB pool (sample)</b>: {', '.join(orb_symbols)}"
            dte0_list_text = ""
            if dte0_symbols:
                dte0_list_text = f"\n📋 <b>0DTE pool (sample)</b>: {', '.join(dte0_symbols)}"

            post_close_text = ""
            if post_close_result and post_close_result.get("open_detected", 0) > 0:
                post_close_text = (
                    f"\n🔄 <b>Post-block safety close:</b> "
                    f"{post_close_result.get('open_detected', 0)} open → "
                    f"{post_close_result.get('closed', 0)} closed"
                )

            if observe_only:
                mode_line = (
                    "📟 <b>Mode:</b> <b>OBSERVE</b> — trades continue; trigger logged for review"
                )
            else:
                mode_line = (
                    "📟 <b>Mode:</b> <b>BLOCKING</b> — Regime Protection active"
                )

            path_impact = pp_path_impact_text(observe_only=observe_only)

            message = f"""====================================================================

{_portfolio_protection_alert_header(mode, observe_only=observe_only)}

{mode_line}
          Family: <b>Regime</b>
🔍 <b>Trigger:</b> {trigger_label}
          <code>{trigger_code}</code>

📌 <b>Reason:</b>
          {reason}
{metrics_text}
{list_note}
{path_impact}
{post_close_text}

📊 <b>Signals in pool:</b> ORB {orb_signals_count} | 0DTE {dte0_signals_qualified} | Scanned {total_scanned}
{orb_list_text}{dte0_list_text}

💡 Post-fill BAD DAY emergency exit remains active (15 min health checks).
"""
            success = await self.send_telegram_alert(message, AlertLevel.WARNING)
            if success:
                log.info(
                    "✅ Portfolio protection alert sent: %s | code=%s | observe_only=%s | grep=PORTFOLIO_PROTECTION_ALERT_SENT",
                    trigger_id,
                    trigger_code,
                    str(observe_only).lower(),
                )
            return success
        except Exception as e:
            log.error("Error sending portfolio protection alert: %s", e, exc_info=True)
            return False

    async def send_red_day_alert(
        self,
        reason: str,
        metrics: Optional[Dict[str, Any]] = None,
        mode: str = "DEMO",
        total_scanned: int = 0,
        orb_signals_count: int = 0,
        dte0_signals_qualified: int = 0,
        orb_symbols: Optional[List[str]] = None,
        dte0_symbols: Optional[List[str]] = None,
        snapshot_used: bool = False,
    ) -> bool:
        """
        Send Red Day alert when Red Day is detected during signal collection (Rev 00258)
        
        This alert replaces the signal collection alert when Red Day is detected.
        Shows Red Day reason, metrics, and impact on trading.
        
        Args:
            reason: Red Day detection reason (pattern description)
            metrics: Red Day metrics (pct_low_rsi, pct_high_rsi, pct_weak_volume, etc.)
            mode: Trading mode (DEMO/LIVE)
            total_scanned: Total symbols scanned
            orb_signals_count: Number of ORB signals collected
            dte0_signals_qualified: Number of 0DTE signals qualified
        
        Returns:
            True if alert sent successfully
        """
        try:
            from zoneinfo import ZoneInfo
            
            pt_tz = ZoneInfo('America/Los_Angeles')
            et_tz = ZoneInfo('America/New_York')
            now_pt = datetime.now(pt_tz)
            now_et = datetime.now(et_tz)
            
            pt_time = now_pt.strftime('%I:%M %p PT')
            et_time = now_et.strftime('%I:%M %p ET')
            
            mode_emoji = _telegram_mode_emoji()
            
            # Build metrics section
            metrics_text = ""
            if metrics:
                rs_cov = metrics.get('rs_coverage')
                vwap_cov = metrics.get('vwap_coverage')
                analyzed = metrics.get('signals_analyzed', total_scanned or 0)
                rs_cov_text = f"{rs_cov}/{analyzed}" if isinstance(rs_cov, int) and analyzed else "n/a"
                vwap_cov_text = f"{vwap_cov}/{analyzed}" if isinstance(vwap_cov, int) and analyzed else "n/a"
                metrics_text = f"""
📊 <b>Market Conditions:</b>
          • Oversold (RSI under 40): {metrics.get('pct_low_rsi', 0):.0f}%
          • Overbought (RSI above 80): {metrics.get('pct_high_rsi', 0):.0f}%
          • Weak Volume (below 1.0x): {metrics.get('pct_weak_volume', 0):.0f}%
          • Avg RSI: {metrics.get('avg_rsi', 0):.1f}
          • Avg Volume: {metrics.get('avg_volume', 0):.2f}x
          • Avg MACD Histogram: {metrics.get('avg_macd_histogram', 0):.3f}
          • Avg RS vs SPY: {metrics.get('avg_rs_vs_spy', 0):.2f} (coverage: {rs_cov_text})
          • Avg VWAP Distance: {metrics.get('avg_vwap_distance', 0):.2f}% (coverage: {vwap_cov_text})"""

            # Signal lists (for review when execution is cancelled)
            list_note = ""
            if snapshot_used:
                list_note = "🧾 <b>Note:</b> Using last collected signal snapshot (later scan returned 0 signals)."

            orb_list_text = ""
            if orb_symbols:
                orb_list_text = f"\n📋 <b>ORB Signals (symbols)</b>:\n          {', '.join(orb_symbols)}"

            dte0_list_text = ""
            if dte0_symbols:
                dte0_list_text = f"\n📋 <b>0DTE Signals (symbols)</b>:\n          {', '.join(dte0_symbols)}"
            
            message = f"""====================================================================

🚨 <b>RED DAY DETECTED</b>
{_alert_mode_header_line(mode)}
          Time: {pt_time} ({et_time})

🔍 <b>Red Day Reason:</b>
          {reason}
{metrics_text}

{list_note}
💰 <b>Action Taken:</b>
          • ORB trades: <b>BLOCKED</b> (capital preserved)
          • 0DTE options: <b>CONTINUING</b> (independent of Red Day)

📊 <b>Signal Collection:</b>
          • Symbols Scanned: {total_scanned}
          • ORB Signals: {orb_signals_count} (blocked from execution)
          • 0DTE Signals: {dte0_signals_qualified} (will process PUTs)
{orb_list_text}
{dte0_list_text}

💡 <b>Strategy Impact:</b>
          • ORB Strategy: Execution skipped to preserve capital
          • Easy 0DTE: Runs normally under 0DTE rules (no Red Day bias)

📊 <b>Next:</b> 0DTE options execution
          (Throughout trading day)
"""
            
            success = await self.send_telegram_alert(message, AlertLevel.WARNING)
            if success:
                log.info(f"✅ Red Day alert sent successfully: {reason}")
            return success
            
        except Exception as e:
            log.error(f"Error sending Red Day alert: {e}", exc_info=True)
            return False

# Global instance
_prime_alert_manager = None

def get_prime_alert_manager() -> PrimeAlertManager:
    """Get the prime alert manager instance"""
    global _prime_alert_manager
    if _prime_alert_manager is None:
        _prime_alert_manager = PrimeAlertManager()
    return _prime_alert_manager

log.info("Prime Alert Manager loaded successfully")
