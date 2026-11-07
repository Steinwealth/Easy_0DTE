#!/usr/bin/env python3
"""Session clocks for all 0DTE paths — PT wall time from Shared.env.

Canonical keys (configs/Shared.env §5). Do not duplicate these in path files.

  MARKET_TIMEZONE_PT / MARKET_TIMEZONE_ET
  MORNING_ALERT_START_PT / _END_PT
  TOKEN_PREP_START_PT / _END_PT
  ORB_WINDOW_START / ORB_WINDOW_END           ORB#1 capture box
  CAPTURE_ORB2_START_PT / _END_PT             ORB#2 first-hour box (freeze at end)
  DISCOVERY_ORB1_TL0_START_PT / _END_PT       TL0 + ORB1 Breakout
  DISCOVERY_ORB2_TL_START_PT / _END_PT        TL1–TL3 + ORB2 Breakout (no-new-entries)
  EOD_FLATTEN_START_PT / _END_PT              flatten start (END = scheduled 1-min kick)
  EOD_REPORT_START_PT / _END_PT               unified 0DTE Telegram EOD

Wall clock matches ``prime_orb_strategy_manager._get_current_time_pt`` (Pacific).
Holidays / weekends stay in ``prime_market_manager`` + ``dynamic_holiday_calculator``.
This module only answers “is this PT time inside the configured window?”
"""

from __future__ import annotations

import os
from datetime import datetime, time
from typing import Optional, Tuple

import pytz

KEY_TZ_PT = "MARKET_TIMEZONE_PT"
KEY_TZ_ET = "MARKET_TIMEZONE_ET"
DEFAULT_TZ_PT = "America/Los_Angeles"
DEFAULT_TZ_ET = "America/New_York"

KEY_ALERT_START = "MORNING_ALERT_START_PT"
KEY_ALERT_END = "MORNING_ALERT_END_PT"
DEFAULT_ALERT_START = "05:30"
DEFAULT_ALERT_END = "05:35"

KEY_TOKEN_PREP_START = "TOKEN_PREP_START_PT"
KEY_TOKEN_PREP_END = "TOKEN_PREP_END_PT"
DEFAULT_TOKEN_PREP_START = "06:25"
DEFAULT_TOKEN_PREP_END = "06:26"

KEY_ORB1_CAP_START = "ORB_WINDOW_START"
KEY_ORB1_CAP_END = "ORB_WINDOW_END"
DEFAULT_ORB1_CAP_START = "06:30"
DEFAULT_ORB1_CAP_END = "06:45"

KEY_ORB2_CAP_START = "CAPTURE_ORB2_START_PT"
KEY_ORB2_CAP_END = "CAPTURE_ORB2_END_PT"
DEFAULT_ORB2_CAP_START = "06:30"
DEFAULT_ORB2_CAP_END = "07:30"

KEY_ORB1_TL0_START = "DISCOVERY_ORB1_TL0_START_PT"
KEY_ORB1_TL0_END = "DISCOVERY_ORB1_TL0_END_PT"
DEFAULT_ORB1_TL0_START = "06:45"
DEFAULT_ORB1_TL0_END = "07:25"

KEY_ORB2_TL_START = "DISCOVERY_ORB2_TL_START_PT"
KEY_ORB2_TL_END = "DISCOVERY_ORB2_TL_END_PT"
DEFAULT_ORB2_TL_START = "07:30"
DEFAULT_ORB2_TL_END = "12:30"

KEY_FLATTEN_START = "EOD_FLATTEN_START_PT"
KEY_FLATTEN_END = "EOD_FLATTEN_END_PT"
DEFAULT_FLATTEN_START = "12:50"
DEFAULT_FLATTEN_END = "12:51"

KEY_REPORT_START = "EOD_REPORT_START_PT"
KEY_REPORT_END = "EOD_REPORT_END_PT"
DEFAULT_REPORT_START = "12:55"
DEFAULT_REPORT_END = "12:56"

KEY_DISCOVERY_MODE = "ORB_0DTE_DISCOVERY_MODE"
DEFAULT_DISCOVERY_MODE = "watch"


def _cfg(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a config key without CONFIG_MISSING_KEY spam on alias probes."""
    raw = None
    try:
        from modules.config_loader import get_config_loader

        loader = get_config_loader()
        cfg = getattr(loader, "config", None) or {}
        if key in os.environ:
            raw = os.environ[key]
        elif key in cfg:
            raw = cfg[key]
        else:
            return default
    except Exception:
        raw = os.getenv(key)
        if raw is None:
            return default
    if raw is None:
        return default
    s = str(raw).strip()
    return s if s else default


def _cfg_first(default: str, *keys: str) -> str:
    for key in keys:
        raw = _cfg(key, None)
        if raw:
            return raw
    return default


def parse_hhmm(raw: Optional[str], fallback: time) -> time:
    if not raw:
        return fallback
    try:
        parts = str(raw).strip().split(":")
        return time(int(parts[0]) % 24, int(parts[1]) % 60 if len(parts) > 1 else 0)
    except Exception:
        return fallback


def tz_pt():
    return pytz.timezone(_cfg(KEY_TZ_PT, DEFAULT_TZ_PT) or DEFAULT_TZ_PT)


def tz_et():
    return pytz.timezone(_cfg(KEY_TZ_ET, DEFAULT_TZ_ET) or DEFAULT_TZ_ET)


def now_pt() -> datetime:
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    return now_utc.astimezone(tz_pt())


def now_pt_time() -> time:
    return now_pt().time()


def _window(
    start_key: str,
    end_key: str,
    start_default: str,
    end_default: str,
    *start_aliases: str,
    end_aliases: Tuple[str, ...] = (),
) -> Tuple[time, time]:
    start_fb = parse_hhmm(start_default, time(0, 0))
    end_fb = parse_hhmm(end_default, time(0, 0))
    start_s = _cfg_first(start_default, start_key, *start_aliases)
    end_s = _cfg_first(end_default, end_key, *end_aliases)
    return parse_hhmm(start_s, start_fb), parse_hhmm(end_s, end_fb)


def _in_window(now_pt: Optional[time], start: time, end: time, *, end_inclusive: bool = False) -> bool:
    if now_pt is None:
        now_pt = now_pt_time()
    if end_inclusive:
        return start <= now_pt <= end
    return start <= now_pt < end


def discovery_mode_is_watch() -> bool:
    return (_cfg(KEY_DISCOVERY_MODE, DEFAULT_DISCOVERY_MODE) or DEFAULT_DISCOVERY_MODE).lower() == "watch"


def morning_alert_window_pt() -> Tuple[time, time]:
    return _window(KEY_ALERT_START, KEY_ALERT_END, DEFAULT_ALERT_START, DEFAULT_ALERT_END)


def in_morning_alert_window(now_pt: Optional[time] = None) -> bool:
    start, end = morning_alert_window_pt()
    return _in_window(now_pt, start, end, end_inclusive=True)


def token_prep_window_pt() -> Tuple[time, time]:
    return _window(
        KEY_TOKEN_PREP_START,
        KEY_TOKEN_PREP_END,
        DEFAULT_TOKEN_PREP_START,
        DEFAULT_TOKEN_PREP_END,
    )


def in_token_prep_window(now_pt: Optional[time] = None) -> bool:
    start, end = token_prep_window_pt()
    return _in_window(now_pt, start, end)


def orb1_capture_window_pt() -> Tuple[time, time]:
    return _window(
        KEY_ORB1_CAP_START,
        KEY_ORB1_CAP_END,
        DEFAULT_ORB1_CAP_START,
        DEFAULT_ORB1_CAP_END,
        "CAPTURE_ORB1_START_PT",
        end_aliases=("CAPTURE_ORB1_END_PT",),
    )


def orb2_capture_window_pt() -> Tuple[time, time]:
    start_default = DEFAULT_ORB2_CAP_START
    try:
        start_default = fmt_hhmm(orb1_capture_window_pt()[0])
    except Exception:
        pass
    return _window(
        KEY_ORB2_CAP_START,
        KEY_ORB2_CAP_END,
        start_default,
        DEFAULT_ORB2_CAP_END,
        KEY_ORB1_CAP_START,
        end_aliases=(KEY_ORB2_TL_START, "TRENDLINE_BUILD_TIME_PT"),
    )


def orb2_capture_ready(now_pt: Optional[time] = None) -> bool:
    """True at/after ORB#2 freeze clock (capture-box end, typically 07:30)."""
    _, end = orb2_capture_window_pt()
    if now_pt is None:
        now_pt = now_pt_time()
    return now_pt >= end


def orb1_tl0_window_pt() -> Tuple[time, time]:
    return _window(
        KEY_ORB1_TL0_START,
        KEY_ORB1_TL0_END,
        DEFAULT_ORB1_TL0_START,
        DEFAULT_ORB1_TL0_END,
        "ORB_0DTE_DISCOVERY_START_PT",
        end_aliases=("ORB_0DTE_NO_NEW_ENTRIES_AFTER_PT", "TRENDLINE_TL0_NO_NEW_ENTRIES_AFTER_PT"),
    )


def orb1_tl0_session_open(now_pt: Optional[time] = None) -> bool:
    start, end = orb1_tl0_window_pt()
    return _in_window(now_pt, start, end)


def past_orb1_tl0_cutoff(now_pt: Optional[time] = None) -> bool:
    _, end = orb1_tl0_window_pt()
    if now_pt is None:
        now_pt = now_pt_time()
    return now_pt >= end


def orb2_tl_window_pt() -> Tuple[time, time]:
    return _window(
        KEY_ORB2_TL_START,
        KEY_ORB2_TL_END,
        DEFAULT_ORB2_TL_START,
        DEFAULT_ORB2_TL_END,
        "TRENDLINE_BUILD_TIME_PT",
        end_aliases=(
            "TRENDLINE_NO_NEW_ENTRIES_AFTER_PT",
            "BREAKOUT_NO_NEW_ENTRIES_AFTER_PT",
            "TRENDLINE_EXPIRATION_TIME_PT",
        ),
    )


def orb2_tl_session_open(now_pt: Optional[time] = None) -> bool:
    start, end = orb2_tl_window_pt()
    return _in_window(now_pt, start, end)


def past_orb2_tl_cutoff(now_pt: Optional[time] = None) -> bool:
    _, end = orb2_tl_window_pt()
    if now_pt is None:
        now_pt = now_pt_time()
    return now_pt >= end


def eod_flatten_window_pt() -> Tuple[time, time]:
    return _window(
        KEY_FLATTEN_START,
        KEY_FLATTEN_END,
        DEFAULT_FLATTEN_START,
        DEFAULT_FLATTEN_END,
        "SO_ETF_EOD_CLOSE_START_PT",
        end_aliases=("SO_ETF_EOD_CLOSE_END_PT",),
    )


def in_eod_flatten_window(now_pt: Optional[time] = None) -> bool:
    start, end = eod_flatten_window_pt()
    return _in_window(now_pt, start, end)


def eod_flatten_due(now_pt: Optional[time] = None) -> bool:
    """True from EOD_FLATTEN_START_PT through the rest of the PT calendar day.

    The 1-minute ``in_eod_flatten_window`` is only the scheduled kickoff. After a
    Cloud Run replacement the new instance must keep flattening leftover opens
    (including through the 12:55 EOD report) instead of waiting for tomorrow.
    """
    start, _end = eod_flatten_window_pt()
    if now_pt is None:
        now_pt = now_pt_time()
    return now_pt >= start


def eod_report_window_pt() -> Tuple[time, time]:
    return _window(
        KEY_REPORT_START,
        KEY_REPORT_END,
        DEFAULT_REPORT_START,
        DEFAULT_REPORT_END,
    )


def in_eod_report_window(now_pt: Optional[time] = None) -> bool:
    start, end = eod_report_window_pt()
    return _in_window(now_pt, start, end)


def eod_report_due(now_pt: Optional[time] = None) -> bool:
    """True from EOD_REPORT_START_PT through the rest of the PT calendar day.

    Documents when Cloud Scheduler ``end-of-day-report`` is expected. The trading
    loop does not send 🏦 END-OF-DAY 0DTE; only ``POST /api/end-of-day-report`` does.
    """
    start, _end = eod_report_window_pt()
    if now_pt is None:
        now_pt = now_pt_time()
    return now_pt >= start


def pt_bound_as_et_hhmm(bound: time, on_dt: datetime) -> Tuple[int, int]:
    """Convert a PT clock bound to ET on the given calendar day (handles DST)."""
    pt = tz_pt()
    et = tz_et()
    if on_dt.tzinfo is None:
        local = pt.localize(datetime(on_dt.year, on_dt.month, on_dt.day, bound.hour, bound.minute))
    else:
        local = on_dt.astimezone(pt).replace(
            hour=bound.hour, minute=bound.minute, second=0, microsecond=0
        )
    converted = local.astimezone(et)
    return converted.hour, converted.minute


def time_to_hour(bound: time) -> float:
    """PT clock as a float hour (12:30 → 12.5)."""
    return (
        float(bound.hour)
        + float(bound.minute) / 60.0
        + float(bound.second) / 3600.0
    )


def orb2_tl_end_hour() -> float:
    """DISCOVERY_ORB2_TL_END_PT as a float hour (default 12.5)."""
    return time_to_hour(orb2_tl_window_pt()[1])


def eod_flatten_start_hour() -> float:
    """EOD_FLATTEN_START_PT as a float hour (default 12 + 50/60)."""
    return time_to_hour(eod_flatten_window_pt()[0])


def fmt_hhmm(bound: time) -> str:
    return f"{bound.hour:02d}:{bound.minute:02d}"


def window_label(start: time, end: time) -> str:
    return f"{fmt_hhmm(start)}-{fmt_hhmm(end)}"


def orb1_capture_label() -> str:
    start, end = orb1_capture_window_pt()
    return window_label(start, end)


def orb2_capture_label() -> str:
    start, end = orb2_capture_window_pt()
    return window_label(start, end)
