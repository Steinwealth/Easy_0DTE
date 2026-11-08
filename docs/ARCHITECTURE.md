# System Architecture

**Last updated:** November 7, 2026  
**Serving:** `YOUR_SERVICE_NAME-00629-f7k` · Demo · 100% traffic · admit Wide · $100,000 sim ledger  
**Rollback of this serving image:** `YOUR_SERVICE_NAME-00628-pf4`.  
**On this image:** sequential OCC inner batches of 25 + `yield_lane_to_equity_batch`; OCC defer only while last exact reusable; leftover OCC skip while owned; this-tick bid pret + HOLD (`live_path_v2_pret_from_tick`, `math.isfinite`); discovery scan vs watch; notification-only Telegram; Bid vs Mark MFE; eval JSONL `path_v2_peak` / qty / fees; TTL not raised.  
**App:** Easy 0DTE Strategy, Research tree  
**Spec:** [../EXECUTIVE_SUMMARY.md](../EXECUTIVE_SUMMARY.md) · **Session order:** [ProcessFlow.md](ProcessFlow.md)

This file is the component map. Path geometry stays in the path guides. Admit bounds stay in [0.TradeProfiles.md](0.TradeProfiles.md). The exit catalog stays in [0DTEProfitCapture.md](0DTEProfitCapture.md).

---

## 1. Shape

One Cloud Run service runs the session. Three watches share one quote batch, one chain door, one Trade Profile, one options account, one monitor, and one end-of-day report.

```text
E*TRADE quotes and chains
        │
        ▼
Prime Trading System
  ORB#1 capture → TL0
  discovery 24 / 10s → ORB1 · Trendline · ORB2
        │
        ▼
Must Execute → one stamp → Trade Profile Wide
        │
        ▼
Risk ($600, 50 slots) → Order Execution
  Demo: MockOptionsExecutor at the ask
  Live: E*TRADE limit, one lift
        │
        ▼
Unified0DTEPositionMonitor (~2s)
  exact OCC bid → PATH_V2 exit
        │
        ├── Telegram
        ├── Desk snapshot → Firebase
        ├── GCS research_app/  (ledger, markers)
        └── Priority Optimizer + Profit Optimizer (training, off the exit check)
```

Market calendar sits in front of that picture. A skip day starts the process and stops before capture.

---

## 2. Lanes

| Lane | Structure | Clock (PT) | Hard stop |
|------|-----------|------------|-----------|
| ORB1 | ORB#1 high/low, 06:30–06:45 | Discovery from 06:45 | `DISCOVERY_ORB1_TL0_END_PT=07:25` |
| Trendline TL0 | Dual-side line on the ORB#1 box | With ORB1 | 07:25 |
| Trendline TL1–TL6 | Lines on the ORB#2 box | After the single TL1 build | 12:30, TL1–TL4 also age out at 3h |
| ORB2 | ORB#2 high/low from 07:30 | Unlocks when the box is frozen | 12:30 |

Shared discovery (`discovery_watch_batch`) takes the next 24 symbols every 10 seconds and always advances the cursor. A running watch task must not occupy the scan slot (`unified_discovery_scan_slot_free`). The loop yields up to 0.8s for the exit monitor, quotes the batch, yields up to 0.5s, then each lane compares that quote to its line. A slow exit quote does not skip the comparison.

The scan quote is the decision quote for 2 minutes. Yahoo 1-minute bars run after the order and only to timestamp Trendline A1/A2. They are not quotes.

Morning order inside the orchestrator:

1. Freeze ORB#1.
2. Yahoo timestamps, inject TL0.
3. Run TL0 and ORB1 on the shared scan until 07:25.
4. Arm TL1 once. Persist `tl1_build_pending`. No TL0 or ORB1 entries in the 07:25–07:30 gap.
5. Freeze ORB#2. Unlock ORB2. Yahoo timestamps. One TL1 build, with Trendline held. Then Trendline joins the scan.
6. If the process restarts before TL1 finishes, run TL1. Do not mark it complete.

ORB SO, validation-candle jobs, signal collection, and Portfolio Protection are env-off. See [§8](#8-paused).

---

## 3. Entry spine

Every fill uses the same steps. `prime_trading_system.py` owns the loop. The path executor owns the submit.

| Step | Owner | Result |
|------|--------|--------|
| Structure | Path watch | ORB box break or Trendline break, CALL or PUT |
| Must Execute | `easy0DTE/modules/single_tp_admit.py` | Feasibility: geometry, cooldown, book-full, one chain, data ready, Call SS, Put SS, one pick |
| Stamp | `central_tech/decision_stamp.py` | One dict and one `decision_digest`. Freeze.values, `po_profile_decision.keys`, and the digest are the same post-pick record. A source older than `max_age_by_source()` is empty |
| Trade Profile | `trade_profile_wide.json` | `tp_admit` or `tp_reject` |
| Size | `Unified0DTEPortfolioRiskManager` | $600 sum, 50-slot book, reservation |
| Order | `OptionsTradingExecutor` | The pick's strike. Demo sim or Live broker |
| Snapshot | Priority Optimizer handoff | Written on the accept that will execute. A reject does not write it |
| Enroll | `enqueue_0dte_pending_monitor` | Name joins the monitor roster |

`SINGLE_TP_ADMIT=true` is pinned on the service. The serving image has no flag-off quality-gate branch. Call SS and Put SS stay outside the profile.

Chain HTTP from discovery, a repair, or a last look goes through `fetch_0dte_intent_chain` → `OptionsChainManager` → `ETradeOptionsAPI`. Same-day expiry when the chain lists it, otherwise the nearest weekly. An open position refreshes that OCC's expiry. Prime Data Manager does not fetch chains.

---

## 4. Account and modes

`modules/runtime_mode.py` resolves `MODE`. Startup rejects a leftover `ETRADE_MODE` that disagrees.

| | Demo | Live |
|--|------|------|
| Data | E*TRADE production quotes and chains | Same |
| Submit | Blocked. `MockOptionsExecutor` fills at the ask | `ETradeBrokerAdapter.submit_option_order` |
| Open book | `MockOptionsExecutor.active_positions` | Broker portfolio via `BrokerPositionSynchronizer` |
| History | `research_app/mock_options_history.json` | `research_app/live_options_history.json` |
| Id prefix | `D-` | `L-` |

`unified_0dte_account.py` is the shared balance and registry for ORB1, Trendline, and ORB2. Caps are 50 on the book and on each path. The sum cap is $600. Demo cash is $100,000. GCS writes for the mock ledger run off the asyncio loop.

`BROKER=etrade`. Other broker tokens fail closed at startup. FINAL Live service `YOUR_SERVICE_NAME-live` is a different Cloud Run service and is not this architecture's deploy target.

---

## 5. Monitor and exit

`Unified0DTEPositionMonitor._monitor_loop` calls the same `maybe_tick` on the 2s/3s interval (or immediately on fill `request_wake`). The main loop starts that task and does not serialize a second tick while it is alive.

| Knob | Value |
|------|--------|
| Interval | `0DTE_POSITION_MONITOR_INTERVAL_SEC=2` |
| Wide book | 3s at `0DTE_MONITOR_WIDE_OPEN_COUNT=20` |
| Quote | Exact OCC, exit mark = bid. Leftover OCC skipped while owned (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`) because leftover shares `occ_fetch`. |
| Eval budget | 1200ms, 1800ms at 20 or more opens. A miss is a log. The name is still checked |
| Underlying | 2s `_ul_refresh_loop` skip-cache last (owned names, cap 25, oldest-first). Evaluate reads cache only: ≤4s usable, ≤8s last-good, older unavailable. Leave a slow/hung batch running (`ul_refresh_slow` / `ul_refresh_hung`, cancel=false); start the next oldest-first wave as soon as inflight completes. OCC inner batches of 25 run one HTTP at a time and yield `equity_batch` between waves. Sequentialization is architectural, not a broker concurrency proof. A new OCC fetch is deferred only while last exact is reusable. Evaluate last reusable OCC bids after the 1.5s slow window (no second HTTP). 16-tick 5s load: N=38/50 second-half miss ~42% / ~56%; with real PATH_V2 + JSONL late bid cov ~27%/30% — do not raise TTL |
| Path-chain refresh | Skipped when the last cycle exceeds 8000ms |
| Open-quote latency gate | 4000ms, background chains only |
| PATH_V2 pret | This-tick bid / entry − 1 (`live_path_v2_pret_from_tick`). Missing, ≤0, or non-finite bid → HOLD. Mark and `pnl_pct` are never pret. |
| Telegram | Notification-only queue (`ledger_alert_flush.py`); drops never rewrite ledger/fills/GCS. Desk Bid MFE% vs Mark MFE%. |

Profit Capture is `modules/path_v2_exit.py` with `OPTION_PC_ARCHITECTURE=path_v2`. The decision is bid versus entry. Booked dollars, Telegram, and the desk use `net_result_pnl_dollars` / `net_result_pnl_fraction`: `(exit − entry) × contracts × 100 − contracts × $1.30`.

At 12:50 the flatten closes what is left. It primes session tech on the remaining opens once, then monitoring persist stays on cache. The 12:55 report is one unified 0DTE Telegram.

Detail: [PositionMonitoring.md](PositionMonitoring.md) · [0DTEProfitCapture.md](0DTEProfitCapture.md).

---

## 6. Training persist

Two recorders sit beside the trade. Neither one submits an order.

**Priority Optimizer** writes the rows used to train Trade Profile 10.23. Those rows are time-correct without an extra chain or equity fetch:

- Session tech is sliced to bars whose minute has closed by the event. A future clock nulls RSI, MACD, Bollinger, and VWAP.
- The executed snapshot copies the handoff freeze. `po_profile_decision` matches that freeze.
- `first_red` ages `po_monitor_quote_at` from the OCC quote already on the tick.
- Greeks restamp from that quote.
- Chain volume is nulled when the chain is older than 60s or was not read. `*_now` stays empty when stale.
- Owned 5-minute volume and open interest come from OCC samples already stored on the trade.
- Writes are one lock per object name. The first body wins. A 412 on create-if-absent means the object exists.

**Profit Optimizer** appends the premium path in memory on the monitor tick and flushes JSONL on the persist worker, off `POSITION_EVAL_MS`. Path `timestamp` is the monitor/write clock. `broker_quote_timestamp` is the broker quote clock. `observation_timestamp` is write time. New recordings keep `path_v2_peak` / `path_pret` / `path_v2_reason`, `quote_age_ms`, `entry_premium`, contracts, and modeled $0.65×2 fees. `path_pret` is `live_path_v2_pret_from_tick`. Historical tapes may lack those keys or still carry `current_pnl_pct` pret. Replay may join a book `entry_premium` (`prep_replay_ticks`); it does not rewrite JSONL. RESEARCH `exit_premium` is not the booked fill — settlement uses book `exit_bid` + qty + fees (`net_result_pnl_dollars`). `PATH_END` is replay end-of-tape HOLD, not a live exit.

Wide collection is why the book is large. 10.23 is the profile those rows are for. It is not the evaluator on the service. Guides: [PriorityOptimizer.md](PriorityOptimizer.md) · [Data.md](Data.md) · [../profit_optimizer/README.md](../profit_optimizer/README.md).

---

## 7. Deployment

| Piece | Role |
|-------|------|
| Cloud Run `YOUR_SERVICE_NAME` | This process. Image `gcr.io/YOUR_GCP_PROJECT_ID/YOUR_SERVICE_NAME` |
| Cloud Run `YOUR_SERVICE_NAME-oauth` | Token renewal |
| GCS `YOUR_GCS_BUCKET` / `research_app/` | Ledger, ORB and TL markers, optimizer objects, desk snapshot |
| Secret Manager | OAuth, Telegram |
| Cloud Scheduler | Keep-alives, 5:30 morning, 12:55 EOD. Validation jobs paused |
| Firebase `easy-0dte` | Desk UI, static. Not in the trading image |
| Firebase OAuth web app | Token renewal UI, separate from the desk |

`/health` shares the trading loop. Deploys set an empty liveness probe so Cloud Run does not replace the instance when that HTTP check is slow. Startup is TCP on 8080. Scheduler may still GET `/health` or `/api/health` to wake a scaled-to-zero instance.

Deploy entry: `DEPLOY_NOW=YES ./scripts/deploy_demo_service.sh`. It merges env, keeps `MODE=demo`, keeps the Wide admit and the 50 / $600 / $100,000 pins, and refuses a seed flag and the Live service. Procedure: [Cloud.md](Cloud.md) · [Deploy.md](Deploy.md) · [Configs.md](Configs.md).

The desk reads snapshot objects the trading service writes. It polls; it does not compute P&L. Contract: [Frontend.md](Frontend.md).

---

## 8. Paused

Present in the repo, not on the running path:

| Switch | Effect when off |
|--------|-----------------|
| `ENABLE_ORB_STRATEGY=false` | No ETF orders |
| `ENABLE_VALIDATION_CANDLE=false` | No 7:00 candle job |
| `ENABLE_ORB_SIGNAL_COLLECTION=false` | No 7:15 batch list |
| `ENABLE_PORTFOLIO_PROTECTION=false` | No Red Day / Protection Day gate |
| `ENABLE_RED_DAY_TIER_GATE=false` | No tier block from that gate |
| `ORB_0DTE_DISCOVERY_MODE=watch` | Event-driven scan, not the 7:30 one-shot |

Re-enabling them is a rollback of the morning, not a second live book. The calendar skip is separate and is on.

---

## 9. Module map

| Concern | Where |
|---------|--------|
| Session loop, morning order, flatten | `modules/prime_trading_system.py` |
| Fair rotate | `modules/discovery_watch_batch.py` |
| Mode, GCS namespace | `modules/runtime_mode.py` |
| Shared book | `modules/unified_0dte_account.py` |
| Admit and pick | `easy0DTE/modules/single_tp_admit.py` |
| Profile JSON | `easy0DTE/configs/trade_profile_wide.json` (serving), `trade_profile_10.23.json` (training) |
| Chain door | `fetch_0dte_intent_chain`, `OptionsChainManager`, `ETradeOptionsAPI` |
| Equity spots and candles | Prime Data Manager |
| Orders | `OptionsTradingExecutor`, `MockOptionsExecutor`, `ETradeBrokerAdapter` |
| Open watch | `Unified0DTEPositionMonitor` |
| Exit | `modules/path_v2_exit.py` |
| Settlement | `modules/options_trade_economics.py` |
| Calendar | `modules/dynamic_holiday_calculator.py`, `modules/session_clock.py` |
| Owned underlying freshness | `modules/underlying_freshness.py` |
| Market API gate | `modules/broker_api_pacing.py` (`open_exact_occ` > `equity_batch`; `yield_lane_to_equity_batch`) |
| Alerts | Prime Alert Manager, [Alerts.md](Alerts.md) |
| Desk writer | Trading service snapshot under `desk_public/`, [Frontend.md](Frontend.md) |

Config files and merge order: [../configs/README.md](../configs/README.md).

---

## 10. Design rules

- One admit. Quality lives in the loaded Trade Profile, not in a second gate stack.
- One scored record. Freeze values, profile-decision keys, and the digest are the same post-pick stamp.
- One age table. Chain 60 s, quote 120 s, SPY 45 s, session tech 90 s. Scoring, hydrate, and chain `*_now` read it. A still-stale gated key waits after one refresh.
- One chain door. Discovery budgets calls. The exit check does not spend a chain fetch to fill a training cell.
- One monitor tick. Open OCC quotes outrank watch-universe work. One owned UL refresh on the 2s loop; skip if the last call is still running. Do not timeout-cancel a live batch at 4s. Inner OCC waves are sequential; yield the Market lane to `equity_batch` between them. Age from the stored clock. Path `timestamp` is the monitor clock. PATH_V2 `pret` is this tick's exact bid / entry − 1.
- Demo and Live share the pick. They differ at submit.
- The reported trade result is the fee-inclusive settlement.
- Deploy is explicit. The script will not reseed and will not target the Live service.
- A skip day is a calendar decision made before capture, using the previous weekday session when the next session is a holiday or a low-volume day.
