"""Session memos for empty E*TRADE expiries and nearest listed dates.

Same-day 0DTE is always first. After a 10031 / empty day, remember it so the
next scan does not repeat the broker call. Nearest listed (>= today) is cached
so contract selection can jump to 2DTE / 3DTE / 4DTE / weekly without walking
empty calendar days.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_LOCK = threading.Lock()
# Trading session (US/Eastern date) that owns the memos. A new session
# drops yesterday so today's same-day 0DTE is never skipped.
_MEMO_SESSION = ""
# (symbol, expiry_iso) → monotonic deadline
_EMPTY: Dict[Tuple[str, str], float] = {}
# symbol → (session_today, nearest_iso, deadline)
_NEAREST: Dict[str, Tuple[str, str, float]] = {}
# symbol → (dates, deadline)
_EXPIRE_DATES: Dict[str, Tuple[List[str], float]] = {}


def _ensure_session_locked() -> str:
    """Caller holds ``_LOCK``. Wipe memos when the US/Eastern session date rolls."""
    global _MEMO_SESSION
    today = session_today_iso()
    if _MEMO_SESSION != today:
        _EMPTY.clear()
        _NEAREST.clear()
        _EXPIRE_DATES.clear()
        _MEMO_SESSION = today
    return today


def _ttl_sec(key: str, default: float) -> float:
    raw = os.getenv(key, str(default))
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def normalize_expiry_iso(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


def session_today_iso() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _now() -> float:
    return time.monotonic()


def _same_day_empty_ttl_default() -> float:
    """Mon–Wed keep the empty same-day memo for the session.

    Daily 0DTE almost never appears mid-morning on those days, so a 45-minute
    re-probe only repeats 10031 / SelectedED Friday. Thu–Fri keep 45 minutes
    in case a same-day listing shows up after the open.
    """
    if os.getenv("0DTE_EMPTY_SAME_DAY_TTL_SEC"):
        return _ttl_sec("0DTE_EMPTY_SAME_DAY_TTL_SEC", 2700.0)
    day = datetime.now(ZoneInfo("America/New_York")).date().weekday()
    return 14400.0 if day <= 2 else 2700.0


def remember_empty(symbol: str, expiry: Optional[str], *, same_day: Optional[bool] = None) -> None:
    sym = str(symbol or "").strip().upper()
    iso = normalize_expiry_iso(expiry)
    if not sym or not iso:
        return
    today = session_today_iso()
    if same_day is None:
        same_day = iso == today
    ttl = (
        _same_day_empty_ttl_default()
        if same_day
        else _ttl_sec("0DTE_EMPTY_EXPIRY_TTL_SEC", 14400.0)
    )
    with _LOCK:
        _ensure_session_locked()
        _EMPTY[(sym, iso)] = _now() + ttl


def is_empty(symbol: str, expiry: Optional[str]) -> bool:
    sym = str(symbol or "").strip().upper()
    iso = normalize_expiry_iso(expiry)
    if not sym or not iso:
        return False
    key = (sym, iso)
    with _LOCK:
        _ensure_session_locked()
        deadline = _EMPTY.get(key)
        if deadline is None:
            return False
        if deadline <= _now():
            _EMPTY.pop(key, None)
            return False
        return True


def forget_empty(symbol: str, expiry: Optional[str]) -> None:
    sym = str(symbol or "").strip().upper()
    iso = normalize_expiry_iso(expiry)
    if not sym or not iso:
        return
    with _LOCK:
        _ensure_session_locked()
        _EMPTY.pop((sym, iso), None)


def remember_nearest(symbol: str, expiry: Optional[str], session_today: Optional[str] = None) -> None:
    sym = str(symbol or "").strip().upper()
    iso = normalize_expiry_iso(expiry)
    today = normalize_expiry_iso(session_today) or session_today_iso()
    if not sym or not iso:
        return
    ttl = _ttl_sec("0DTE_NEAREST_EXPIRY_MEMO_TTL_SEC", 14400.0)
    with _LOCK:
        _ensure_session_locked()
        _NEAREST[sym] = (today, iso, _now() + ttl)


def nearest_for(symbol: str, session_today: Optional[str] = None) -> Optional[str]:
    sym = str(symbol or "").strip().upper()
    today = normalize_expiry_iso(session_today) or session_today_iso()
    if not sym:
        return None
    with _LOCK:
        _ensure_session_locked()
        row = _NEAREST.get(sym)
        if not row:
            return None
        memo_today, iso, deadline = row
        if deadline <= _now() or memo_today != today:
            _NEAREST.pop(sym, None)
            return None
        return iso


def remember_expire_dates(symbol: str, dates: List[str]) -> None:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    clean = sorted({normalize_expiry_iso(d) for d in dates if normalize_expiry_iso(d)})
    ttl = _ttl_sec("0DTE_EXPIRE_DATES_TTL_SEC", 14400.0)
    with _LOCK:
        _ensure_session_locked()
        _EXPIRE_DATES[sym] = (clean, _now() + ttl)


def expire_dates_for(symbol: str) -> Optional[List[str]]:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    with _LOCK:
        _ensure_session_locked()
        row = _EXPIRE_DATES.get(sym)
        if not row:
            return None
        dates, deadline = row
        if deadline <= _now():
            _EXPIRE_DATES.pop(sym, None)
            return None
        return list(dates)


def weekday_forward_candidates(today: date, max_days: int) -> List[str]:
    """Calendar days after today, weekdays only, within max_days."""
    out: List[str] = []
    limit = max(0, int(max_days or 0))
    for offset in range(1, limit + 1):
        cand = today + timedelta(days=offset)
        if cand.weekday() >= 5:
            continue
        out.append(cand.isoformat())
    return out


def listed_forward_candidates(
    today: date,
    listed: List[str],
    max_days: int,
) -> List[str]:
    """Earliest listed expiries after today within max_days (already sorted)."""
    limit = max(0, int(max_days or 0))
    out: List[str] = []
    for raw in listed or []:
        iso = normalize_expiry_iso(raw)
        if not iso:
            continue
        try:
            delta = (date.fromisoformat(iso) - today).days
        except ValueError:
            continue
        if 1 <= delta <= limit:
            out.append(iso)
    return sorted(set(out))


def intent_expiry_from_listed(
    today: date,
    listed: List[str],
    max_days: int,
    symbol: Optional[str] = None,
) -> Optional[str]:
    """Same-day if the broker lists it and it is not memo-empty; else nearest.

    ``symbol`` is optional. When set, days already remembered empty (same-day
    miss / 10031) are skipped so the next scan goes straight to the weekly.
    """
    today_iso = today.isoformat()
    clean = [normalize_expiry_iso(raw) for raw in (listed or [])]
    clean = [iso for iso in clean if iso]
    skip_empty = bool(str(symbol or "").strip())
    if today_iso in clean and not (skip_empty and is_empty(symbol, today_iso)):
        return today_iso
    forward = listed_forward_candidates(today, clean, max_days)
    if not skip_empty:
        return forward[0] if forward else None
    for iso in forward:
        if not is_empty(symbol, iso):
            return iso
    return None


def expiredate_resolve_first(today: Optional[date] = None) -> bool:
    """Mon–Thu: ask optionexpiredate before a same-day chain (skip 10031).

    Friday (and ``never``) keep same-day-first so true 0DTE days do not pay
    an extra expiredate call. ``always`` forces the list-first path.
    """
    mode = str(os.getenv("0DTE_EXPIREDATE_RESOLVE_FIRST", "weekday") or "").strip().lower()
    if mode in ("0", "false", "never", "off", "same_day", "same-day"):
        return False
    if mode in ("1", "true", "always", "on"):
        return True
    day = today or datetime.now(ZoneInfo("America/New_York")).date()
    return int(day.weekday()) <= 3


def reset_option_expiry_memo() -> None:
    global _MEMO_SESSION
    with _LOCK:
        _EMPTY.clear()
        _NEAREST.clear()
        _EXPIRE_DATES.clear()
        _MEMO_SESSION = ""
