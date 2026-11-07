# Process Flow — Easy 0DTE Strategy

**Last updated:** November 6, 2026 (local runtime pack not on serving: this-tick bid pret + HOLD, leftover OCC skip while owned, discovery scan vs watch, notification-only Telegram) · November 5, 2026 (serving `00625-wwv`: sequential OCC inner batches + UL lane yield, reusable-gated OCC defer, eval JSONL engine/qty/fee keys)  
**Serving:** `easy-etrade-strategy-00625-wwv` · `MODE=demo` · 100% traffic (local Nov 6 pack not on this revision)  
**Rollback of this serving image:** `00624-8bn`.  
**Admit:** Trade Profile Wide (`SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`)  
**Book:** $100,000 sim · 50 slots · $600 sum cap · seed flag off  
**Spec:** [../EXECUTIVE_SUMMARY.md](../EXECUTIVE_SUMMARY.md) · **Components:** [ARCHITECTURE.md](ARCHITECTURE.md)

This is the running day. Clocks are Pacific. The cash open is 06:30 PT (09:30 ET). Flatten is 12:50 PT. The end-of-day report is 12:55 PT.

The 7:00 validation candle, 7:15 signal collection, 7:30 ETF batch, and Portfolio Protection gate are off. They are listed once in [Paused morning](#paused-morning). They are not steps in this flow.

---

## The day

```text
1. Calendar          skip, or continue
2. Token + morning   05:30 Telegram
3. ORB#1             06:30–06:45 capture
4. TL0               as soon as ORB#1 is secure
5. ORB1 + TL0 scan   06:45–07:25, then both stop
6. Arm TL1           07:25–07:30, once
7. ORB#2             ≥07:30 freeze, unlock ORB2, build TL1 once, TL joins
8. Shared scan       until 12:30
9. Each break        Trade Profile → size → order → enroll
10. Monitor          ~2s until 12:50, Profit Capture on each open
11. Flatten          12:50
12. EOD              12:55 Telegram, GCS
```

On a skip day the flow ends at step 1 after the holiday Telegram. Capture does not run.

---

## Timeline

| Time PT | Time ET | What happens | Telegram |
|---------|---------|--------------|----------|
| Prior 21:00 | 00:00 | E*TRADE tokens expire | OAuth expired, when renewal is still due |
| 05:00 | 08:00 | Keep-alive starts | — |
| 05:30 | 08:30 | Calendar check, token check | Good morning, or holiday / pre-holiday / Election Day |
| 06:30–06:45 | 09:30–09:45 | ORB#1 high/low on `0dte_list.csv` | ORB1 capture complete |
| ≥06:45 | ≥09:45 | TL0 drawn on that box | Fills later, not a build alert |
| 06:45–07:25 | 09:45–10:25 | ORB1 and TL0 on the shared 24/10s scan | Fill or reject, per trade |
| 07:25–07:30 | 10:25–10:30 | TL1 armed once. No new ORB1 or TL0 entries | — |
| ≥07:30 | ≥10:30 | ORB#2 freeze, ORB2 unlock, one TL1 build, Trendline joins | ORB2 capture complete |
| until 12:30 | until 15:30 | ORB2 and Trendline on the same scan | Fill or reject, per trade |
| until 12:50 | until 15:50 | Monitor every open | Exit alerts |
| 12:50 | 15:50 | Flatten leftovers | Close alerts as needed |
| 12:55 | 15:55 | Unified report | `END-OF-DAY 0DTE` |

A normal day sends the morning alert, two capture alerts, one alert per fill, reject, or exit, and one EOD. A skip day sends one alert.

---

## 1. Calendar

`modules/dynamic_holiday_calculator.py` → `should_skip_trading`.

| Class | App |
|-------|-----|
| Weekend | Off |
| `MARKET_CLOSED` | Off. NYSE holiday, including the observed weekday |
| `LOW_VOLUME` | Off. Market is open. Desk policy skips the session |
| `PRE_HOLIDAY` | Off. The previous weekday session before a closed or low-volume day |
| `ELECTION_DAY` | Off. U.S. presidential or midterm general Election Day. NYSE is open. Monday and Wednesday around it trade |
| Ordinary session | On. 06:30–13:00 PT, or through 10:00 PT on an early close |

`PRE_HOLIDAY` is one session back. A Monday holiday disables Friday. It does not disable Sunday. A long weekend does not also disable the Thursday before that Friday. A listed Day Before … day (Wednesday before Thanksgiving) is already that session — do not disable the Tuesday before it. Christmas Eve and the weekday before it still skip. Election Day is **not** a `PRE_HOLIDAY` source. The full list is [MarketCalendar.md](MarketCalendar.md).

The 05:30 job sends the holiday / pre-holiday / Election Day Telegram and returns. Trading jobs that fire later see the same skip and do not open the book.

---

## 2. Token and morning

Demo and Live both use the production E*TRADE token. Renewal is the OAuth web app. After a new token, the service reloads it, proves a live quote, and attaches ORB, Trendline, and ORB2 if they were skipped at boot.

The 05:30 alert is the good-morning message when the calendar says trade. Keep-alives from 05:00 keep the instance warm. `/health` on those pings is a wake-up. It is not Cloud Run liveness.

---

## 3. ORB#1 and TL0

From 06:30 to 06:45 the app records the opening high and low for the 0DTE list. When that box is stored, TL0 is injected from Yahoo 1-minute timestamps. Yahoo does not supply the live price.

From 06:45 both watches read the shared scan:

1. The loop yields up to **0.8s** so the exit monitor can run.
2. It quotes the next **24** names.
3. It yields up to **0.5s**.
4. ORB1 and TL0 compare that quote to the box or the line.

The cursor always moves. A name that is slow to quote is not pinned. At **07:25** both watches turn off. Candidates that have not filled, including ones already confirmed, are expired so they cannot submit after the cutoff.

---

## 4. TL1 and ORB#2

Between 07:25 and 07:30 the app arms TL1 once and writes `tl1_build_pending`. That gap has no ORB1 or TL0 entries and no repeated arm.

At or after 07:30:

1. Freeze ORB#2 (a few seconds of OHLC).
2. Unlock ORB2 discovery immediately. ORB2 does not wait for TL1.
3. Load Yahoo timestamps and build TL1 once. Trendline signals wait during that build.
4. Let Trendline join the 24/10s scan.

If Cloud Run replaces the instance before TL1 finishes, the pending marker means the next process builds TL1. It does not stamp the build complete and skip it. A TL0-only hydrate does not consume that attempt.

TL1–TL4 drop off **3 hours** after `built_at`. TL0 is already gone at 07:25. TL2 and later need TL1 plus a reclaim inside the ORB#2 box. Activation for Trendline and ORB2 is **0.08%**. Scanning runs through **12:30**.

---

## 5. One signal

Same steps for ORB1, Trendline, and ORB2. CALL and PUT.

| # | Step | What the software does |
|---|------|------------------------|
| 1 | Structure | Box break or line break on this scan's quote |
| 2 | Book | If 50 slots are full, `max_open_positions_reached`. No chain call, no cooldown |
| 3 | Chain | One `fetch_0dte_intent_chain` for this name, inside the fetch budget |
| 4 | Data | Missing share or dir% holds the same break up to 90s, then retires it with no cooldown |
| 5 | Skip signals | Call SS and Put SS, hard, outside the profile |
| 6 | Pick | One contract from that chain: premium, delta, OTM, size, spread |
| 7 | Stamp | One dict, one digest. The profile, the freeze, `po_profile_decision`, the log, and the handoff read this dict. A source older than the age table is empty on the freeze; after one refresh the entry waits if it is still stale |
| 8 | Profile | Trade Profile Wide. Accept `tp_admit` or reject `tp_reject` |
| 9 | Size | $600 sum cap, reservation on the shared book |
| 10 | Order | Demo fills the sim at the ask. Live limits at the ask, then one lift of $0.05–$0.15 |
| 11 | Enroll | The fill joins this tick's monitor roster |

A profile reject stops at step 8. No entry snapshot, no order. A stamp mismatch rejects `data_unavailable:stamp_parity`. The scan quote remains valid for 2 minutes. Yahoo and the open-book path-chain stamp happen after the order, so they are not on the way into the profile.

The pick is the strike. A missing listing may move to the nearest strike within $1 only when the ask is still inside the price margin (10% of the ask, at least $0.05, at most $0.15). With the pick in hand there is no second chain fetch. Full contract: [0.OrderExecution.md](0.OrderExecution.md).

Wide's job on this deploy is to accept enough real trades to train 10.23. The 10.23 file is not loaded as `TRADE_PROFILE_VERSION`.

---

## 6. Monitor and exit

`Unified0DTEPositionMonitor.maybe_tick` is the only open-book evaluate. `_monitor_loop` owns the timer; fill enroll only flags + `request_wake`.

- Every **2s**, or every **3s** when 20 or more positions are open.
- Exact contract quote. Exit mark is the bid.
- Every open name is checked, including when the eval budget log fires.
- Underlying price comes from the 2s `_ul_refresh_loop` skip-cache last (owned names, cap 25). Evaluate then uses cache: ≤4s usable, ≤8s last-good, older unavailable. On `00625-wwv`: OCC inner batches of 25 run one HTTP at a time and yield `equity_batch` between waves; a new OCC fetch waits only while last exact is reusable. JSONL persist min gap is 5s; daily JSON 45s. New eval rows keep `path_v2_peak` / `path_pret` / `path_v2_reason`, quote age, contracts, and modeled fees.
- PATH_V2 decides the exit. Bid versus entry is the decision. **Local:** pret is this-tick bid / entry − 1 (`live_path_v2_pret_from_tick`); missing bid → STEP 0 HOLD; never mark / `pnl_pct`. Serving `00625-wwv` still assigned `path_pret = current_pnl_pct`. The dollars in the alert are fee-inclusive: `(exit − entry) × contracts × 100 − contracts × $1.30`. RESEARCH `exit_premium` is not the booked fill. `PATH_END` is replay end-of-tape HOLD, not a live exit.
- Demo closes on the sim ledger at the bid. Live sends the sell-to-close. **Local:** Telegram flush is notification-only (`ledger_alert_flush.py`); a drop never rewrites ledger, fills, or GCS.
- A same-side cooldown starts after the exit.

Training writes happen off that check: session-tech bars already closed, the handoff freeze copied onto the executed row (same keys and digest as `po_profile_decision`), `first_red` aged from the OCC quote on the tick. Path `timestamp` is the monitor clock; `broker_quote_timestamp` stays the broker field. A chain older than 60s is empty on the record (live exits do not read chain). No extra chain or equity fetch is added to fill a blank. [PositionMonitoring.md](PositionMonitoring.md) · [0DTEProfitCapture.md](0DTEProfitCapture.md) · [PriorityOptimizer.md](PriorityOptimizer.md).

Discovery keeps its 10-second cadence while positions are open. **Local:** a running watch must not occupy the scan slot (`unified_discovery_scan_slot_free`). Leftover OCC is skipped while any owned name is open (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`) because leftover shares `occ_fetch` with owned exact quotes. Background chain refresh stands down when the last cycle exceeds 8s, or when open-quote latency exceeds 4s. A break already being compared still runs. Slow-broker 5s + PATH_V2 at 38/50 is a safe-degradation test (late bid cov ~27%/30%) — TTL 4s/8s is not raised.

---

## 7. Flatten and EOD

At **12:50** the app market-closes whatever is still open, on all three paths, in one flatten. It primes session tech once on the names still open, then the persist path is cache-only.

At **12:55** the scheduler asks for the unified 0DTE report. The report is one Telegram. Ledgers and the daily marker are in GCS `research_app/`. A rerun does not duplicate the stored report.

---

## Scheduler

Enabled on a trading week:

| Job | Schedule (PT) | Role |
|-----|----------------|------|
| trading-hours-keepalive-1 | `*/3 5-7 * * 1-5` | Warm the instance |
| trading-hours-keepalive-2 | `*/5 7-10 * * 1-5` | Hold it through the morning |
| trading-hours-keepalive-3 | `*/5 10-14 * * 1-5` | Hold it through the flatten |
| oauth-market-open-alert | 05:30 | Morning or holiday Telegram |
| end-of-day-report | 12:55 | Unified EOD |

Paused with the old morning:

| Job | Schedule | State |
|-----|----------|--------|
| validation-candle-700 | 07:00 | Paused |
| prefetch-validation-715 | 07:15 | Paused |

Project and job commands: [CloudSecrets.md](CloudSecrets.md). Keep-alive HTTP is a wake ping.

---

## Paused morning

These env flags are false, and `ORB_0DTE_DISCOVERY_MODE=watch`:

- `ENABLE_ORB_STRATEGY` — ETF orders
- `ENABLE_VALIDATION_CANDLE` — 07:00 candle
- `ENABLE_ORB_SIGNAL_COLLECTION` — 07:15 list and 07:30 batch
- `ENABLE_PORTFOLIO_PROTECTION` and `ENABLE_RED_DAY_TIER_GATE` — Red Day tier

The calendar skip above is the session switch that is on. It is not that Red Day gate.

Turning the flags on and setting discovery to `batch` restores the older 7:15 / 7:30 morning. That is a rollback, described in [0DTEORB1Breakout.md](0DTEORB1Breakout.md) and [Configs.md](Configs.md). It is not this document's sequence.

---

## What to check after a session

| Check | Healthy look |
|-------|----------------|
| Calendar | Ordinary day traded, or one holiday Telegram and no captures |
| ORB#1 | Capture alert, then ORB1/TL0 fills only before 07:25 |
| ORB#2 | Capture alert, ORB2 fills without waiting on TL1, TL fills after TL1 |
| Admit | `tp_admit` / `tp_reject` from Wide. No second gate stack |
| Orders | Demo ids `D-…` on the sim ledger. Strike matches the profile pick |
| Monitor | Opens enrolled. Exit quote is the bid. Booked P&L includes $1.30 per contract round trip |
| EOD | One report. Book flat after 12:50 |
| Training | Snapshot clocks at or before the event. Wide rows kept for 10.23 |

Log the morning with the path guides' tokens. The February 2026 `PIPELINE | STEP` lines belong to the paused signal-collection morning.
