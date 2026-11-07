#!/usr/bin/env python3
"""
Dynamic Holiday Calculator
=========================

NYSE-style US market calendar for any year: closed holidays (with weekend
observance), low-volume skip days, and the prior weekday session (PRE_HOLIDAY).
Canonical skip gate: ``should_skip_trading``.
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

PRE_HOLIDAY_REASON = "PRE_HOLIDAY"
ELECTION_DAY_REASON = "ELECTION_DAY"

# python-holidays US() is federal, not NYSE. These stay open on the cash session.
_PYHOLIDAYS_NOT_NYSE = ("columbus", "indigenous", "veterans", "inauguration")

# Weekend-observed NYSE closures. New Year's is Sunday → Monday only
# (NYSE Rule 7.2: Saturday Jan 1 is not moved back to Friday Dec 31).
_NYSE_OBSERVED_FIXED = (
    (1, 1, "New Year's Day", False),
    (6, 19, "Juneteenth", True),
    (7, 4, "Independence Day", True),
    (12, 25, "Christmas Day", True),
)


def observe_nyse_weekend(check_date: date, *, observe_saturday: bool = True) -> date:
    """NYSE observed weekday: Saturday → Friday (when allowed), Sunday → Monday."""
    if observe_saturday and check_date.weekday() == 5:
        return check_date - timedelta(days=1)
    if check_date.weekday() == 6:
        return check_date + timedelta(days=1)
    return check_date


def calculate_easter(year: int) -> date:
    """Calculate Easter Sunday for a given year using the Anonymous Gregorian algorithm"""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = (h + l - 7 * m + 114) // 31
    p = (h + l - 7 * m + 114) % 31
    return date(year, n, p + 1)

def get_nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Get the nth occurrence of a weekday in a given month"""
    # weekday: 0=Monday, 1=Tuesday, ..., 6=Sunday
    first_day = date(year, month, 1)
    days_ahead = weekday - first_day.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first_weekday = first_day + timedelta(days=days_ahead)
    return first_weekday + timedelta(weeks=n-1)

def _fixed_nyse_holidays(year: int) -> List[Tuple[date, str]]:
    """Fixed NYSE closures for ``year``, plus weekend observed weekdays (may land in year-1)."""
    holidays: List[Tuple[date, str]] = []
    for month, day, name, observe_saturday in _NYSE_OBSERVED_FIXED:
        raw = date(year, month, day)
        holidays.append((raw, name))
        observed = observe_nyse_weekend(raw, observe_saturday=observe_saturday)
        if observed != raw:
            holidays.append((observed, f"{name} (Observed)"))
    return holidays


def calculate_us_holidays(year: int) -> List[Tuple[date, str]]:
    """Calculate NYSE-closed holidays for a given year, including weekend observances."""
    holidays = []

    holidays.extend(_fixed_nyse_holidays(year))
    # Next year's Sunday Jan 1 observes Monday the 2nd in that year; no Dec 31 close.
    for holiday_date, holiday_name in _fixed_nyse_holidays(year + 1):
        if holiday_date.year == year:
            holidays.append((holiday_date, holiday_name))
    
    # Variable holidays
    # Martin Luther King Jr. Day - 3rd Monday of January
    mlk_day = get_nth_weekday_of_month(year, 1, 0, 3)  # 0 = Monday
    holidays.append((mlk_day, "Martin Luther King Jr. Day"))
    
    # Presidents' Day - 3rd Monday of February
    presidents_day = get_nth_weekday_of_month(year, 2, 0, 3)  # 0 = Monday
    holidays.append((presidents_day, "Presidents' Day"))
    
    # Good Friday - Friday before Easter Sunday
    easter = calculate_easter(year)
    good_friday = easter - timedelta(days=2)
    holidays.append((good_friday, "Good Friday"))
    
    # Memorial Day - Last Monday of May
    # Find the last day of May, then work backwards to the last Monday
    last_day_of_may = date(year, 6, 1) - timedelta(days=1)
    days_back = last_day_of_may.weekday()  # 0=Monday, 6=Sunday
    memorial_day = last_day_of_may - timedelta(days=days_back)
    holidays.append((memorial_day, "Memorial Day"))
    
    # Labor Day - 1st Monday of September
    labor_day = get_nth_weekday_of_month(year, 9, 0, 1)  # 0 = Monday
    holidays.append((labor_day, "Labor Day"))
    
    # Thanksgiving Day - 4th Thursday of November
    thanksgiving = get_nth_weekday_of_month(year, 11, 3, 4)  # 3 = Thursday
    holidays.append((thanksgiving, "Thanksgiving Day"))

    unique: Dict[date, str] = {}
    for holiday_date, holiday_name in holidays:
        unique.setdefault(holiday_date, holiday_name)
    return sorted(unique.items())

def calculate_low_volume_holidays(year: int) -> List[Tuple[date, str]]:
    """
    Calculate non-bank holidays with typically low trading volume (Rev 00087)
    
    These are days when markets are OPEN but volume/momentum is typically low.
    Strategy should skip trading on these days to avoid low-quality setups.
    """
    low_volume_days = []
    
    # Halloween - October 31st (if it's a weekday)
    halloween = date(year, 10, 31)
    if halloween.weekday() < 5:  # Monday-Friday
        low_volume_days.append((halloween, "Halloween"))
    
    # Indigenous Peoples' / Columbus Day - second Monday in October
    columbus_day = get_nth_weekday_of_month(year, 10, 0, 2)  # 0 = Monday, 2nd occurrence
    low_volume_days.append((columbus_day, "Indigenous Peoples' Day / Columbus Day"))
    
    # Veterans Day - November 11 (market open but historically low volume)
    veterans_day = date(year, 11, 11)
    if veterans_day.weekday() < 5:
        low_volume_days.append((veterans_day, "Veterans Day"))
    
    # Wednesday before Thanksgiving: market is open, historically thin.
    # This *is* the previous session of Thanksgiving, so PRE_HOLIDAY must
    # not walk one more weekday back (no "day before Day Before Thanksgiving").
    thanksgiving = get_nth_weekday_of_month(year, 11, 3, 4)  # 4th Thursday
    day_before_thanksgiving = thanksgiving - timedelta(days=1)
    if day_before_thanksgiving.weekday() < 5:  # Should be Wednesday
        low_volume_days.append((day_before_thanksgiving, "Day Before Thanksgiving"))
    
    # Black Friday - Day after Thanksgiving (often early close)
    black_friday = thanksgiving + timedelta(days=1)
    if black_friday.weekday() < 5:  # Should be Friday
        low_volume_days.append((black_friday, "Black Friday"))
    
    # Christmas Eve - December 24th (if it's a weekday)
    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 5:  # Monday-Friday
        low_volume_days.append((christmas_eve, "Christmas Eve"))
    
    # New Year's Eve - December 31st (if it's a weekday)
    nye = date(year, 12, 31)
    if nye.weekday() < 5:  # Monday-Friday
        low_volume_days.append((nye, "New Year's Eve"))
    
    # Day after Christmas (if Dec 26 is a weekday and Dec 25 was a weekday)
    christmas = date(year, 12, 25)
    day_after_christmas = date(year, 12, 26)
    if christmas.weekday() < 5 and day_after_christmas.weekday() < 5:
        low_volume_days.append((day_after_christmas, "Day After Christmas"))
    
    # Day after New Year's (if Jan 2 is a weekday and Jan 1 was a weekday)
    new_years = date(year, 1, 1)
    day_after_ny = date(year, 1, 2)
    if new_years.weekday() < 5 and day_after_ny.weekday() < 5:
        low_volume_days.append((day_after_ny, "Day After New Year's"))
    
    return sorted(low_volume_days)

def calculate_early_close_days(year: int) -> List[Tuple[date, str, str]]:
    """Calculate early close days for a given year"""
    early_close_days = []
    
    # Independence Day Eve - July 3rd (if July 4th is not a weekend)
    july_4th = date(year, 7, 4)
    if july_4th.weekday() == 0:  # Monday
        # If July 4th is Monday, July 3rd (Sunday) is early close
        early_close_days.append((date(year, 7, 3), "Independence Day Eve", "13:00"))
    elif july_4th.weekday() in [1, 2, 3, 4]:  # Tuesday through Friday
        # If July 4th is a weekday, July 3rd is early close
        early_close_days.append((date(year, 7, 3), "Independence Day Eve", "13:00"))
    
    # Black Friday - Day after Thanksgiving
    thanksgiving = get_nth_weekday_of_month(year, 11, 3, 4)  # 4th Thursday
    black_friday = thanksgiving + timedelta(days=1)
    early_close_days.append((black_friday, "Black Friday", "13:00"))
    
    # Christmas Eve - December 24th (if December 25th is not a weekend)
    christmas = date(year, 12, 25)
    if christmas.weekday() == 0:  # Monday
        # If Christmas is Monday, December 24th (Sunday) is early close
        early_close_days.append((date(year, 12, 24), "Christmas Eve", "13:00"))
    elif christmas.weekday() in [1, 2, 3, 4]:  # Tuesday through Friday
        # If Christmas is a weekday, December 24th is early close
        early_close_days.append((date(year, 12, 24), "Christmas Eve", "13:00"))
    
    return sorted(early_close_days)

def get_holidays_for_year(year: int) -> Dict:
    """Get all holidays and early close days for a specific year (Rev 00087)"""
    holidays = calculate_us_holidays(year)
    early_close = calculate_early_close_days(year)
    low_volume = calculate_low_volume_holidays(year)
    
    return {
        "year": year,
        "holidays": [(d.strftime("%Y-%m-%d"), name) for d, name in holidays],
        "early_close_days": [(d.strftime("%Y-%m-%d"), name, time) for d, name, time in early_close],
        "low_volume_holidays": [(d.strftime("%Y-%m-%d"), name) for d, name in low_volume],
        "total_holidays": len(holidays),
        "total_early_close_days": len(early_close),
        "total_low_volume_holidays": len(low_volume)
    }

def is_low_volume_holiday(check_date: date = None) -> Tuple[bool, str]:
    """
    Check if a given date is a low-volume holiday (Rev 00087)
    
    Args:
        check_date: Date to check (default: today)
        
    Returns:
        Tuple of (is_holiday, holiday_name)
    """
    if check_date is None:
        check_date = date.today()
    
    year = check_date.year
    low_volume_days = calculate_low_volume_holidays(year)
    
    for holiday_date, name in low_volume_days:
        if holiday_date == check_date:
            return (True, name)
    
    return (False, "")

def _pyholidays_us_bank_holiday(check_date: date) -> Tuple[bool, str]:
    """
    US bank holiday via pyholidays (observed dates, e.g. July 3 when July 4 is Saturday).
    Returns (is_closed, holiday_name). Empty name when not a pyholidays US holiday.
    """
    if check_date.weekday() >= 5:
        return (False, "")
    try:
        import holidays as pyholidays
    except ImportError:
        return (False, "")
    try:
        us = pyholidays.US(years=check_date.year)
        name = us.get(check_date)
        if not name:
            return (False, "")
        lowered = str(name).lower()
        if any(token in lowered for token in _PYHOLIDAYS_NOT_NYSE):
            return (False, "")
        # Federal calendar observes Saturday Jan 1 on Friday Dec 31; NYSE does not.
        if (
            check_date.month == 12
            and check_date.day == 31
            and "new year" in lowered
        ):
            return (False, "")
        return (True, str(name))
    except Exception:
        pass
    return (False, "")


def _direct_skip_reason(check_date: date) -> Tuple[bool, str, str]:
    """Return direct holiday/low-volume skip status, without previous-session logic."""
    year = check_date.year

    bank_holidays = calculate_us_holidays(year)
    for holiday_date, name in bank_holidays:
        if holiday_date == check_date:
            return (True, "MARKET_CLOSED", name)

    # Observed NYSE closes (e.g. Friday when Christmas is Saturday) beat
    # Christmas Eve / New Year's Eve low-volume labels on the same weekday.
    py_closed, py_name = _pyholidays_us_bank_holiday(check_date)
    if py_closed:
        return (True, "MARKET_CLOSED", py_name)

    low_volume_days = calculate_low_volume_holidays(year)
    for holiday_date, name in low_volume_days:
        if holiday_date == check_date:
            return (True, "LOW_VOLUME", name)

    return (False, "", "")


def _previous_weekday(check_date: date) -> date:
    prev_day = check_date - timedelta(days=1)
    while prev_day.weekday() >= 5:
        prev_day -= timedelta(days=1)
    return prev_day


def _is_named_day_before(name: str) -> bool:
    """A listed skip that is already 'the session before' another event.

    ``Day Before Thanksgiving`` is the previous session of Thanksgiving.
    Attaching PRE_HOLIDAY to it would disable Tuesday — a double day-before.
    Christmas Eve is not named this way, so the weekday before Eve still skips.
    """
    return str(name).strip().lower().startswith("day before")


def previous_skip_holiday_for_session(check_date: date) -> Optional[Tuple[date, str, str]]:
    """
    Return the skipped holiday/low-volume day immediately after this trading session.

    Example: if a low-volume holiday is Monday, the prior Friday is disabled too.
    If Christmas Eve is the skipped day, Dec 23 is disabled. Weekend dates are
    never returned as prior sessions.
    """
    if check_date.weekday() >= 5:
        return None

    # Look far enough to cross a long weekend plus observed holiday, without
    # accidentally blocking an unrelated normal week.
    for offset in range(1, 5):
        candidate = check_date + timedelta(days=offset)
        should_skip, reason, name = _direct_skip_reason(candidate)
        if not should_skip:
            continue
        # Already the session before a holiday — do not mint another PRE_HOLIDAY.
        if _is_named_day_before(name):
            continue
        if _previous_weekday(candidate) == check_date:
            return (candidate, reason, name)
    return None


def calculate_pre_holiday_skip_sessions(year: int) -> List[Tuple[date, str, date, str]]:
    """Calculate weekday sessions to skip because they precede a skipped holiday."""
    sessions: Dict[date, Tuple[str, date, str]] = {}
    for candidate_year in (year, year + 1):
        for holiday_date, holiday_name in calculate_us_holidays(candidate_year):
            if holiday_date.weekday() >= 5:
                continue
            prior_session = _previous_weekday(holiday_date)
            if prior_session.year == year:
                sessions[prior_session] = (holiday_name, holiday_date, "MARKET_CLOSED")
        for holiday_date, holiday_name in calculate_low_volume_holidays(candidate_year):
            if _is_named_day_before(holiday_name):
                continue
            prior_session = _previous_weekday(holiday_date)
            if prior_session.year == year:
                sessions.setdefault(prior_session, (holiday_name, holiday_date, "LOW_VOLUME"))
        year_start = date(candidate_year, 1, 1)
        year_end = date(candidate_year + 1, 1, 1)
        day_count = (year_end - year_start).days
        for day_offset in range(day_count):
            probe = year_start + timedelta(days=day_offset)
            py_closed, py_name = _pyholidays_us_bank_holiday(probe)
            if py_closed:
                prior_session = _previous_weekday(probe)
                if prior_session.year == year:
                    sessions.setdefault(prior_session, (py_name, probe, "MARKET_CLOSED"))

    return sorted(
        (session_date, holiday_name, holiday_date, holiday_reason)
        for session_date, (holiday_name, holiday_date, holiday_reason) in sessions.items()
    )


def calculate_election_day(year: int) -> Optional[Tuple[date, str]]:
    """U.S. federal general Election Day: Tuesday after the first Monday in November.

    Even years only. ``year % 4 == 0`` is presidential; other even years are midterm.
    Odd years return None. Not a PRE_HOLIDAY source.
    """
    if year % 2 != 0:
        return None
    november_first = date(year, 11, 1)
    days_to_monday = (0 - november_first.weekday()) % 7
    first_monday = november_first + timedelta(days=days_to_monday)
    election = first_monday + timedelta(days=1)
    name = (
        "U.S. Presidential Election Day"
        if year % 4 == 0
        else "U.S. Midterm Election Day"
    )
    return (election, name)


def _election_skip_reason(check_date: date) -> Tuple[bool, str, str]:
    found = calculate_election_day(check_date.year)
    if not found:
        return (False, "", "")
    election_date, name = found
    if election_date == check_date:
        return (True, ELECTION_DAY_REASON, name)
    return (False, "", "")


def should_skip_trading(check_date: date = None) -> Tuple[bool, str, str]:
    """
    Determine if trading should be skipped on a given date (Rev 00087)
    
    Checks bank holidays (market closed) AND low-volume holidays (market open, low momentum).
    Uses the dynamic calculator first, then pyholidays for observed bank holidays the
    calculator may miss (e.g. Independence Day observed on July 3 when July 4 is Saturday).
    Election Day is evaluated last and is never a PRE_HOLIDAY source.
    
    Args:
        check_date: Date to check (default: today)
        
    Returns:
        Tuple of (should_skip, reason, holiday_name)
        reason: "MARKET_CLOSED", "LOW_VOLUME", "PRE_HOLIDAY", "ELECTION_DAY", or ""
    """
    if check_date is None:
        check_date = date.today()
    
    direct_skip, direct_reason, direct_name = _direct_skip_reason(check_date)
    if direct_skip:
        return (True, direct_reason, direct_name)

    prior_to = previous_skip_holiday_for_session(check_date)
    if prior_to:
        holiday_date, holiday_reason, holiday_name = prior_to
        return (
            True,
            PRE_HOLIDAY_REASON,
            f"Trading Session Before {holiday_name} ({holiday_date.isoformat()}, {holiday_reason})",
        )

    election_skip, election_reason, election_name = _election_skip_reason(check_date)
    if election_skip:
        return (True, election_reason, election_name)
    
    return (False, "", "")

if __name__ == "__main__":
    # Test the calculator
    current_year = datetime.now().year
    print(f"Testing holiday calculator for {current_year}...")
    
    result = get_holidays_for_year(current_year)
    print(f"\nBank Holidays (Market Closed) for {current_year}:")
    for date_str, name in result["holidays"]:
        print(f"  {date_str}: {name}")
    
    print(f"\nLow-Volume Holidays (Skip Trading) for {current_year}:")
    for date_str, name in result["low_volume_holidays"]:
        print(f"  {date_str}: {name}")
    
    print(f"\nEarly Close Days for {current_year}:")
    for date_str, name, time in result["early_close_days"]:
        print(f"  {date_str}: {name} ({time})")
    
    print(f"\nSummary:")
    print(f"  Bank Holidays: {result['total_holidays']}")
    print(f"  Low-Volume Holidays: {result['total_low_volume_holidays']}")
    print(f"  Early Close Days: {result['total_early_close_days']}")
    print(f"  Total Days to Skip: {result['total_holidays'] + result['total_low_volume_holidays']}")
    
    # Test today
    print(f"\nToday's Check ({date.today()}):")
    should_skip, reason, holiday_name = should_skip_trading()
    if should_skip:
        print(f"  ⛔ SKIP TRADING: {holiday_name} ({reason})")
    else:
        print(f"  ✅ NORMAL TRADING DAY")
