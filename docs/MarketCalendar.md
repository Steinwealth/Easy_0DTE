# Market Calendar — Time, Sessions, Holidays, Low-Volume, Pre-Holiday, Election Day

**Last updated:** November 3, 2026 — Easy 0DTE is off on NYSE holidays, listed low-volume days, the previous market session (`PRE_HOLIDAY`), **and** U.S. presidential and midterm general Election Days (`ELECTION_DAY`). Election Day is the Tuesday after the first Monday in November, even years only. NYSE stays open. The Monday before and Wednesday after are **not** election skips. `PRE_HOLIDAY` is unchanged: the previous **market session** before a bank holiday or a standalone low-volume day — **not** before Election Day. A listed **Day Before …** skip (Wednesday before Thanksgiving) is already that session; it does not mint a second pre-holiday (2026-11-24 trades). Monday skips still disable the prior Friday (2026-09-04, 2026-10-09). December 2026: 12-23 / 12-24 / 12-25 skip; 12-22 and 12-28/29 trade; 12-30 / 12-31 skip; 2027-01-01 closed. Local until `deploy now`.  
**Scope:** every time-, calendar-, holiday- and session-aware behavior of the Easy 0DTE app: RESEARCH backend, Telegram, Cloud Scheduler, GCS markers, and the Desk UI.  
**Canonical skip gate:** `modules/dynamic_holiday_calculator.py` → `should_skip_trading(date)`  
**Canonical clocks:** `modules/session_clock.py` + `configs/Shared.env` §5  
**Frontend mirror:** `0. Easy 0DTE Desk UI/public/js/market-calendar.mjs`  
**Tests:** `tests/test_dynamic_holiday_calculator.py`, `tests/test_election_day_calendar.py` (RESEARCH) · `scripts/test-calendar-crosscheck.mjs`, `scripts/test-session-marquee.mjs`, `scripts/test-market-closed-banner.mjs`, `scripts/test-training.mjs` skip-cell cases (Desk UI)

The calendar is **computed**, not a hardcoded annual list. Every year (this year, ten years out, any year) comes from the same weekday rules plus Easter (Anonymous Gregorian algorithm, `calculate_easter`). `data/holidays_custom.json` (`HOLIDAYS_CUSTOM_PATH`) is reference notes for 2025–2026. It is not a skip gate: `should_skip_trading` never reads it, and `is_trading_day` / `is_market_open` / early-close hours also ignore it (§8).

---

## 1. Day classification

Every calendar date falls into exactly one class:

| Class | Market (NYSE) | App trading | Source |
|-------|---------------|-------------|--------|
| Weekend (Sat/Sun) | Closed | Off | weekday ≥ 5 (not a `should_skip_trading` reason) |
| `MARKET_CLOSED` | Closed | Off | NYSE holiday, including the weekend-observed weekday |
| `LOW_VOLUME` | Open | Off | Desk policy: historically thin / noisy session |
| `PRE_HOLIDAY` | Open | Off | Previous **market session** before a `MARKET_CLOSED` day or a **standalone** `LOW_VOLUME` day. Not minted from a listed **Day Before …** skip. **Not** minted from `ELECTION_DAY` |
| `ELECTION_DAY` | Open | Off | U.S. presidential or midterm general Election Day. Evaluated after existing skips. Never a `PRE_HOLIDAY` source |
| Normal session | Open (09:30–16:00 ET, or 09:30–13:00 ET on an early close) | On | none of the above |

`should_skip_trading(check_date) → (should_skip: bool, reason: str, name: str)` with `reason ∈ {"MARKET_CLOSED", "LOW_VOLUME", "PRE_HOLIDAY", "ELECTION_DAY", ""}`. Precedence: `MARKET_CLOSED` → `LOW_VOLUME` → `PRE_HOLIDAY` → `ELECTION_DAY` (a date that is itself a holiday skip is never relabeled as a pre-holiday or an election).

---

## 2. NYSE closures (`MARKET_CLOSED`)

`calculate_us_holidays(year)`:

| Holiday | Rule | Weekend observance |
|---------|------|--------------------|
| New Year's Day | Jan 1 | **Sunday → Monday only.** A Saturday Jan 1 is not observed (NYSE Rule 7.2); Friday Dec 31 stays open |
| Martin Luther King Jr. Day | 3rd Monday of January | — |
| Presidents' Day | 3rd Monday of February | — |
| Good Friday | Friday before Easter Sunday | — |
| Memorial Day | Last Monday of May | — |
| Juneteenth | June 19 | Saturday → Friday, Sunday → Monday |
| Independence Day | July 4 | Saturday → Friday, Sunday → Monday |
| Labor Day | 1st Monday of September | — |
| Thanksgiving Day | 4th Thursday of November | — |
| Christmas Day | Dec 25 | Saturday → Friday, Sunday → Monday |

Observed dates carry the name `"{Holiday} (Observed)"`. The weekend rule lives in `observe_nyse_weekend(date, observe_saturday=...)` driven by `_NYSE_OBSERVED_FIXED`.

**Federal fallback:** `_pyholidays_us_bank_holiday` consults `python-holidays` US for observed closures the calculator might miss. It ignores federal-only names (`columbus`, `indigenous`, `veterans`, `inauguration` — `_PYHOLIDAYS_NOT_NYSE`) and ignores a federal observed New Year's Day that lands on Dec 31, so those can never be labeled `MARKET_CLOSED`.

**Observed close beats low-volume:** if an observed NYSE closure falls on a weekday that is also a low-volume date (e.g. Friday Dec 24 when Christmas is Saturday), the day is `MARKET_CLOSED`.

---

## 3. Low-volume days (`LOW_VOLUME`)

`calculate_low_volume_holidays(year)`. Market open, app trading disabled. Each applies only on a weekday:

- Halloween (Oct 31)
- Indigenous Peoples' Day / Columbus Day (2nd Monday of October)
- Veterans Day (Nov 11)
- Day Before Thanksgiving (Wednesday before Thanksgiving). This **is** the previous session of Thanksgiving. It stays `LOW_VOLUME`. It does **not** generate another `PRE_HOLIDAY` (no Tuesday “day before Day Before Thanksgiving”).
- Black Friday (Friday after Thanksgiving)
- Christmas Eve (Dec 24)
- New Year's Eve (Dec 31)
- Day After Christmas (Dec 26), only when Dec 25 is also a weekday
- Day After New Year's (Jan 2), only when Jan 1 is also a weekday

Columbus/Indigenous Peoples' Day and Veterans Day are **not** NYSE closures.

---

## 3b. Election Day (`ELECTION_DAY`)

`calculate_election_day(year)` → Tuesday after the first Monday in November, even years only. Same class as the other app-off / NYSE-open skips (`LOW_VOLUME`, `PRE_HOLIDAY`): the cash session runs; Easy 0DTE does not.

- Odd year: no federal general election skip.
- `year % 4 == 0`: `U.S. Presidential Election Day`.
- Other even years: `U.S. Midterm Election Day`.

NYSE is open. Easy 0DTE is off. `should_skip_trading` evaluates this **after** existing holiday and `PRE_HOLIDAY` logic, and `_direct_skip_reason` does **not** include it, so the Monday before and Wednesday after stay whatever they already were (normally a trading day). No `PRE_ELECTION` / `POST_ELECTION`. Primaries, odd-year local elections, special elections, and Inauguration Day are not covered.

`get_holidays_for_year` lists NYSE closures, low-volume days, and early closes only. It does **not** list Election Day. Print `calculate_election_day(year)` or walk `should_skip_trading` for the skip.

Computed federal general Election Days (illustrative):

| Year | Date | Name |
|------|------|------|
| 2024 | Tue 11-05 | `U.S. Presidential Election Day` |
| 2026 | Tue 11-03 | `U.S. Midterm Election Day` |
| 2028 | Tue 11-07 | `U.S. Presidential Election Day` |
| 2030 | Tue 11-05 | `U.S. Midterm Election Day` |
| 2032 | Tue 11-02 | `U.S. Presidential Election Day` |

**Training Calendar:** an empty future Election Day is a muted closed cell — `🗳️ {name}` plus `Election Day · Easy 0DTE trading disabled` — the same pattern as Veterans Day and Thanksgiving. A posted session on a later-disabled Election Day (2026-11-03) keeps Pre/Post, the same as 10/9 `PRE_HOLIDAY`. Do not overlay the holiday reason on a day that already has book or log values.

---

## 4. Pre-holiday policy (`PRE_HOLIDAY`)

**Policy:** the **previous trading session** immediately before every **bank holiday** (`MARKET_CLOSED`) and every **standalone** low-volume day is also disabled. That is the prior weekday (`_previous_weekday`), not the prior calendar date. A Monday skip therefore disables the preceding **Friday** (Sunday is not a session). If Thanksgiving ever fell on a Monday, the prior Friday would skip.

A listed skip whose name is **Day Before …** is already that previous session. `previous_skip_holiday_for_session` ignores it as a source, so it cannot mint “Day before Day Before Thanksgiving.” Christmas Eve is **not** named that way, so the weekday before Eve still skips.

| Skip day | Disabled pre-holiday session |
|----------|------------------------------|
| Monday holiday or Monday low-volume day | Friday before (never Sunday) |
| Tuesday–Friday standalone skip (Veterans Day, Christmas Eve, Good Friday, …) | The weekday before |
| Day Before Thanksgiving (Wednesday) | None extra — Wednesday *is* the session before Thanksgiving |
| A run of consecutive skip days (Thanksgiving Wed–Fri, Christmas Eve + Christmas) | Days inside the run stay skip days. Only a standalone event at the front of the run (Christmas Eve) adds one session before the run. |

**One session only, no chaining:** `PRE_HOLIDAY` never generates another `PRE_HOLIDAY`. Only a direct skip that is not already a named Day Before can create one. A Monday that is itself `PRE_HOLIDAY` (the session before a Tuesday skip) does **not** also turn the prior Friday off.

**2026 checks that match this rule:**

| Shape | Disabled | Still trades |
|-------|----------|--------------|
| 3-day weekend, Monday bank holiday | Fri 09-04 `PRE_HOLIDAY` + Mon 09-07 Labor Day | Thu 09-03 |
| 3-day weekend, Monday low-volume | Fri 10-09 `PRE_HOLIDAY` + Mon 10-12 Indigenous Peoples' / Columbus Day | Thu 10-08 |
| Midweek low-volume | Tue 11-10 `PRE_HOLIDAY` + Wed 11-11 Veterans Day | Mon 11-09 |
| Friday close (Good Friday) | Thu 04-02 `PRE_HOLIDAY` + Fri 04-03 | Wed 04-01 |
| Thanksgiving (Thu) + listed Wednesday + Black Friday | Wed 11-25 `LOW_VOLUME` + Thu 11-26 + Fri 11-27 | **Tue 11-24** (and Mon 11-23) |
| Christmas Eve + Christmas | Wed 12-23 `PRE_HOLIDAY` + Thu 12-24 Eve + Fri 12-25 Christmas | Tue 12-22 |

The 10.23 in-sample training calendar can still show 09-04 and 10-09 as historical fills. Those are replay book days. The live gate (`should_skip_trading`) will not open them.

**Lookup:** `previous_skip_holiday_for_session(date)` looks 1–4 calendar days ahead for a direct skip day whose previous weekday is this date. It uses `_direct_skip_reason` only, so `ELECTION_DAY` cannot mint a preceding Monday skip. `calculate_pre_holiday_skip_sessions(year)` lists a full year (including sessions in late December created by next year's skip days).

**Name:** `"Trading Session Before {holiday name} ({holiday ISO date}, {holiday reason})"`.

---

## 5. Early closes (hours only)

`calculate_early_close_days(year)` → `(date, name, "13:00")`. NYSE closes at **13:00 ET / 10:00 PT**. Early close is an hours rule, not a skip reason.

| Day | Applies when |
|-----|--------------|
| Independence Day Eve | July 3, when July 4 is Tuesday–Friday (a Monday July 4 also emits Sunday July 3, which is not a session) |
| Black Friday | Every year |
| Christmas Eve | Dec 24, when Christmas is Tuesday–Friday (a Monday Christmas also emits Sunday Dec 24, which is not a session) |

Black Friday and weekday Christmas Eve are `LOW_VOLUME` skip days, so the app never trades them. If either ever became a trading day, the 12:50 PT flatten would run after that day's 10:00 PT close. The Desk UI treats the market as closed from 10:00 PT on early-close days (§10).

---

## 6. Time zones and DST

- Market time is `America/New_York`; operator and session-clock time is `America/Los_Angeles` (`MARKET_TIMEZONE_ET` / `MARKET_TIMEZONE_PT`, defaults in `session_clock.py`).
- Both zones change DST on the same dates, so PT session walls (06:30–13:00 PT ≡ 09:30–16:00 ET) are stable year-round. All zone math uses `zoneinfo`; no fixed UTC offsets.
- The container runs with `TZ=America/New_York` (`Shared.env`), so `date.today()` in the trading loop is the ET date.
- Cloud Scheduler jobs are pinned to an IANA zone, so they follow DST automatically.

---

## 7. Session clocks

Source: `Shared.env` §5, read by `session_clock.py` (defaults shown). Windows are start-inclusive / end-exclusive except the morning alert window (end inclusive).

| Clock | PT | ET | Key / owner |
|-------|----|----|-------------|
| Good Morning / Holiday Telegram | 05:30–05:35 | 08:30–08:35 | `MORNING_ALERT_START_PT` / `_END_PT`; Cloud Scheduler `oauth-market-open-alert` |
| Token prep (live SPY probe) | 06:25–06:26 | 09:25–09:26 | `TOKEN_PREP_START_PT` / `_END_PT`; trading loop |
| Cash open | 06:30 | 09:30 | `prime_market_manager.is_market_open` |
| ORB#1 capture | 06:30–06:45 | 09:30–09:45 | `ORB_WINDOW_START` / `ORB_WINDOW_END` |
| ORB#2 capture box | 06:30–07:30 | 09:30–10:30 | `CAPTURE_ORB2_START_PT` / `_END_PT` |
| ORB1 / TL0 discovery | 06:45–07:25 | 09:45–10:25 | `DISCOVERY_ORB1_TL0_*`; no new ORB1/TL0 at or after 07:25 |
| ORB2 / TL1–TL3 discovery | 07:30–12:30 | 10:30–15:30 | `DISCOVERY_ORB2_TL_*`; no new entries after 12:30 |
| Flatten leftover 0DTE | 12:50 onward | 15:50 onward | `EOD_FLATTEN_START_PT`; retries until the book is empty |
| Unified EOD Telegram | 12:55–12:56 | 15:55–15:56 | `EOD_REPORT_*`; Cloud Scheduler `end-of-day-report` only |
| Cash close | 13:00 | 16:00 | RTH end (10:00 PT on early-close days) |

---

## 8. Backend wiring

### Trading loop (`prime_trading_system`)

1. On the first pass of each new date (`date.today()`, ET), it resets `_holiday_checked_date` / `_holiday_skip_today` and calls `should_skip_trading(today)` once.
2. If skip: logs the reason and name, sets `_holiday_skip_today = True`, and each pass sleeps 60 s and `continue`s. Nothing after the gate runs that day: no token prep, no ORB capture, no discovery, no entries, no flatten.
3. If not skip: token prep, then `market_manager.is_market_open()` gates all session work (weekday + 09:30–16:00 ET, early-close aware).
4. In the 05:30–05:35 PT window the loop only logs that the morning alert is handled by Cloud Scheduler; it never sends Telegram.

### `is_market_open` (`prime_market_manager`)

`is_trading_day()` and the current ET time inside RTH, using the early-close time on early-close days. Cached per UTC minute. `is_trading_day()` is false on weekends and on every date `should_skip_trading` skips, so the hours gate and the trading-loop skip cannot disagree. `data/holidays_custom.json` is still loaded for `get_holiday_info` notes only. Leftover Hijri / Muslim holiday loaders in this module are not a skip gate (`hijri-converter` is not installed).

### Cloud Scheduler

| Job | Schedule | Target |
|-----|----------|--------|
| `oauth-market-open-alert` | `30 8 * * 1-5` America/New_York (05:30 PT) | `POST /api/alerts/market-open` → `send_oauth_morning_alert` |
| `end-of-day-report` | 12:55 PT weekdays | `POST /api/end-of-day-report` |

Weekends never fire the morning job (`1-5`).

---

## 9. Morning / Holiday Telegram

**Sender:** Cloud Scheduler `oauth-market-open-alert` only, via `PrimeAlertManager.send_oauth_morning_alert`.

Order of checks:

1. Outside 05:30–05:35 PT → rejected, nothing sent.
2. Weekend (ET date) → nothing sent.
3. `should_skip_trading(ET date)` true → if the day's marker exists, skip; else `send_holiday_alert(name, reason)` **instead of** Good Morning, then write the marker.
4. Otherwise → if the marker exists, skip; else production OAuth token check + live SPY probe → Good Morning, then write the marker.

**Dedup marker:** one per mode, service and PT date: `{ns}/alert_markers/morning_alert_sent_{mode}_{service}_{YYYY-MM-DD}.txt` (`{ns}` = `research_app` for the Demo service), plus an in-process cache. Written only after a successful send. 60-day GCS lifecycle.

**Holiday alert copy per kind** (`_holiday_alert_copy`):

| Reason | Emoji | Title | Why line |
|--------|-------|-------|----------|
| `MARKET_CLOSED` | 🏖️ | `Holiday! - {name}` | U.S. stock market is closed today. Trading disabled to preserve capital quality. |
| `LOW_VOLUME` | 🎃 | `Holiday! - {name}` | Market is open, but volume is typically low on this holiday. Trading disabled to preserve capital quality. |
| `PRE_HOLIDAY` | 🛡️ | `Pre-Holiday Risk Session! - Day before {holiday}` | This is the final trading session before a holiday or low-volume market event. Trading disabled to avoid unreliable pre-holiday price action. |
| `ELECTION_DAY` | 🗳️ | `Election Day! - {name}` | NYSE is open. Easy 0DTE trading is disabled on U.S. federal Election Day. |

Body:

```
{emoji} {title}
          {Weekday, Month DD, YYYY}   (ET date)

🎭 No Trading Today! ☁️🏖️🏝️⛱️🌤️☁️☁️
🚫 Status: Trading DISABLED today.
💡 Why: {why line}
✅ System Status: Normal
🔍 Next Trading: System will resume at next normal trading day
🌐 Dashboard: https://easy-0dte.web.app
```

For `PRE_HOLIDAY`, `{holiday}` is the upcoming skip day's name only (`_pre_holiday_display_name` strips `Trading Session Before` and the `(YYYY-MM-DD, REASON)` tail). Desk HOLIDAY marquee uses 🏖️ for the same day; Telegram keeps 🛡️. No end-of-day trade report content is produced on skip days because nothing trades. The Good Morning token-renewal link stays on the OAuth site.

---

## 10. Desk UI (frontend) calendar

`public/js/market-calendar.mjs` re-implements §2–§5 in JavaScript (`holidaysForYear`, `holidayOnYmd`, `isEarlyCloseYmd`) with the same names and reasons, and the same one-session `PRE_HOLIDAY` rule. Pre-holiday entries carry `upcoming = {ymd, name, reason}` for the skip day they precede. Dates are PT calendar days.

| Function | Returns / rule |
|----------|----------------|
| `marketDayStatus(ymd)` | `{weekend, nyseClosed, tradingDisabled, reason, name, earlyClose}`. `nyseClosed` = weekend or `MARKET_CLOSED`; `tradingDisabled` = weekend or any skip reason; `reason` = `WEEKEND` or the skip reason; `earlyClose` only on open days |
| `isMarketClosedAt(nowMs)` | True when the market itself is closed: weekend, `MARKET_CLOSED`, before 06:30 PT, at or after 13:00 PT, or at or after 10:00 PT on early-close days. `LOW_VOLUME` / `PRE_HOLIDAY` / `ELECTION_DAY` days follow normal RTH (the market is open) |
| `resolveMarketClosed(nowMs, state)` | Uses a Cloud Run flag (`market_session.market_open` + `as_of`, or top-level `market_open` + `updated_at`) when present and ≤ 5 min old (`MARKET_FLAG_MAX_AGE_MS`); otherwise `isMarketClosedAt` (`source: 'calendar'`) |
| `isSessionBellYmd(ymd)` | False on weekends and every skip day (`MARKET_CLOSED`, `LOW_VOLUME`, `PRE_HOLIDAY`, `ELECTION_DAY`): no Market open/close bells, no session, Day P&L stays on the last real session |
| `discoverySession(nowMs)` | Marquee mode, in order: `HOLIDAY` (any skip day, all day) → `CLOSED` (weekend, outside RTH, after an early close) → `WAITING` (06:30 PT until ORB#1 capture lands) → `DISCOVERY` |

**HOLIDAY marquee** (`formatHolidayMarquee`):

| Reason | Emoji | Subject | Why line |
|--------|-------|---------|----------|
| `MARKET_CLOSED` | 🏖️ | `It's {name}` | U.S. stock market is closed today. Trading disabled to preserve capital quality. |
| `LOW_VOLUME` | 🎃 | `It's {name}` | Market is open, but volume is typically low on this holiday. Trading disabled to preserve capital quality. |
| `PRE_HOLIDAY` | 🏖️ | `Day before {upcoming name}` | Trading disabled today: day before {upcoming name} ({its date}), pre-holiday low-volume risk session. |
| `ELECTION_DAY` | 🗳️ | `It's {name}` | NYSE is open. Easy 0DTE trading is disabled on U.S. federal Election Day. |

Full text: `{emoji} Today is a Holiday! - {subject}! {emoji} {Weekday, Month DD, YYYY} 🎭 No Trading Today! … 🚫 Status: Trading DISABLED today. 💡 Why?: {why} ✅ System Status: Normal 🔍 Next Trading: System will resume at next normal trading day`. Without `upcoming`, the name comes from the `Trading Session Before …` entry and no date is shown.

**Training Calendar** (`public/js/training.mjs` `buildMonth`): every weekday uses `marketDayStatus`. `MARKET_CLOSED` is always a muted closed cell. `LOW_VOLUME` / `PRE_HOLIDAY` / `ELECTION_DAY` render as a muted closed cell **only when that date has no replay book row and no log values** — holiday name + why (Veterans Day, Thanksgiving, later elections). A skip day that already has session data stays a result cell (10/9 `PRE_HOLIDAY`, 11/3 `ELECTION_DAY`). Full overlay spec: [Frontend.md](Frontend.md) Training overlay.

**ClosedBar** (Market Closed overlay): shown exactly when `resolveMarketClosed(...).closed` is true — after the close, overnight, weekends, `MARKET_CLOSED` holidays, and early-close afternoons. Never shown while the market is open, including on `LOW_VOLUME`, `PRE_HOLIDAY`, and `ELECTION_DAY` days, where the HOLIDAY marquee explains that trading is off. Rechecked every 30 s and on each marquee paint. Layout and assets: [Frontend.md](Frontend.md).

**Cross-check:** `scripts/test-calendar-crosscheck.mjs` (in `npm test`) runs the RESEARCH calculator read-only via `python3` for every day 2024–2050 and asserts that `marketDayStatus` matches `should_skip_trading` (skip, reason, name) and that `isEarlyCloseYmd` matches `calculate_early_close_days`. Because the trading loop and the morning Telegram both use `should_skip_trading`, a passing cross-check means the desk, the trading gate and Telegram agree. Missing RESEARCH module or `python3` is a **fail**, not a skip.

---

## 11. Illustration: computed 2026 weekday skips

Output of `should_skip_trading` for 2026 weekdays, shown to illustrate the rules (any year can be printed the same way). Early closes in 2026: Black Friday and Christmas Eve (both skip days); no July 3 early close because July 4 is a Saturday and July 3 is the observed closure.

| Date | Reason | Name (shortened for pre-holiday) |
|------|--------|------|
| Thu 01-01 | `MARKET_CLOSED` | New Year's Day |
| Fri 01-02 | `LOW_VOLUME` | Day After New Year's |
| Fri 01-16 | `PRE_HOLIDAY` | Before Martin Luther King Jr. Day |
| Mon 01-19 | `MARKET_CLOSED` | Martin Luther King Jr. Day |
| Fri 02-13 | `PRE_HOLIDAY` | Before Presidents' Day |
| Mon 02-16 | `MARKET_CLOSED` | Presidents' Day |
| Thu 04-02 | `PRE_HOLIDAY` | Before Good Friday |
| Fri 04-03 | `MARKET_CLOSED` | Good Friday |
| Fri 05-22 | `PRE_HOLIDAY` | Before Memorial Day |
| Mon 05-25 | `MARKET_CLOSED` | Memorial Day |
| Thu 06-18 | `PRE_HOLIDAY` | Before Juneteenth |
| Fri 06-19 | `MARKET_CLOSED` | Juneteenth |
| Thu 07-02 | `PRE_HOLIDAY` | Before Independence Day (Observed) |
| Fri 07-03 | `MARKET_CLOSED` | Independence Day (Observed) |
| Fri 09-04 | `PRE_HOLIDAY` | Before Labor Day |
| Mon 09-07 | `MARKET_CLOSED` | Labor Day |
| Fri 10-09 | `PRE_HOLIDAY` | Before Indigenous Peoples' Day / Columbus Day |
| Mon 10-12 | `LOW_VOLUME` | Indigenous Peoples' Day / Columbus Day |
| Mon 11-02 | (trades) | Day before the midterm; not a `PRE_HOLIDAY` |
| Tue 11-03 | `ELECTION_DAY` | U.S. Midterm Election Day |
| Wed 11-04 | (trades) | Day after the midterm |
| Tue 11-10 | `PRE_HOLIDAY` | Before Veterans Day |
| Wed 11-11 | `LOW_VOLUME` | Veterans Day |
| Wed 11-25 | `LOW_VOLUME` | Day Before Thanksgiving (session before Thanksgiving) |
| Thu 11-26 | `MARKET_CLOSED` | Thanksgiving Day |
| Fri 11-27 | `LOW_VOLUME` | Black Friday |
| Wed 12-23 | `PRE_HOLIDAY` | Before Christmas Eve |
| Thu 12-24 | `LOW_VOLUME` | Christmas Eve (NYSE early close 13:00 ET; app off) |
| Fri 12-25 | `MARKET_CLOSED` | Christmas Day |
| Mon 12-28 | (trades) | Day After Christmas is only when Dec 26 is a weekday |
| Wed 12-30 | `PRE_HOLIDAY` | Before New Year's Eve |
| Thu 12-31 | `LOW_VOLUME` | New Year's Eve |

Tue 12-22 trades (no day-before-Eve-before). Fri 2027-01-01 is `MARKET_CLOSED` New Year's Day; session before it is already 12-31.

Later even years use the same Election Day rule (Tue after first Monday in November). 2028-11-07 is `U.S. Presidential Election Day`; 2028-11-06 and 2028-11-08 trade.

---

## 12. Operator checks

- [ ] Normal weekday 05:30 PT: one Good Morning (token + live probe).
- [ ] Skip weekday 05:30 PT: one Holiday / Pre-Holiday Telegram with the right emoji; no fills that day.
- [ ] Weekend: no morning job, no session bells, Discovery marquee CLOSED, ClosedBar shown.
- [ ] Friday before a Monday skip day: Friday is `PRE_HOLIDAY`; the Thursday before it trades. 2026: 09-04 before Labor Day, 10-09 before Indigenous Peoples' / Columbus Day.
- [ ] Christmas Eve and the session before it are both off (2026: 12-24 and 12-23). Tue 12-22 trades. Mon 12-28 trades (Dec 26 is Saturday).
- [ ] New Year's Eve and the session before it are both off (2026: 12-31 and 12-30). Fri 2027-01-01 is `MARKET_CLOSED`.
- [ ] Thanksgiving week: Wednesday / Thursday / Friday off; **Tuesday trades** (no double day-before). If Thanksgiving were a Monday, Friday before it would skip.
- [ ] Election Day (even-year Tuesday after first Monday in November): `ELECTION_DAY`; Monday and Wednesday around it trade unless another existing rule applies. 2026: 11-03 skip; 11-02 and 11-04 trade. 2028: 11-07 skip; 11-06 and 11-08 trade.
- [ ] Training Calendar: empty future Election Days show `🗳️` name + `Election Day · Easy 0DTE trading disabled` (same closed-cell pattern as Veterans / Thanksgiving). Posted 10/9 and 11/3 keep Pre/Post.
- [ ] A 2/3/4-day skip run does not walk past a listed Day Before … day. Christmas Eve still takes the weekday before Eve.
- [ ] Desk: `npm test` passes `test-calendar-crosscheck`.
- [ ] Print any year (Election Day is **not** inside `get_holidays_for_year`): `python3 -c "from datetime import date; from modules.dynamic_holiday_calculator import get_holidays_for_year, calculate_pre_holiday_skip_sessions, calculate_election_day, should_skip_trading; print(get_holidays_for_year(2036)); print(calculate_pre_holiday_skip_sessions(2036)); print(calculate_election_day(2036)); print(should_skip_trading(date(2036,11,4)))"`
