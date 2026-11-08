# Position Monitoring — Go-To Architecture Guide

**Filename:** `PositionMonitoring.md`  
**Last Updated:** November 7, 2026 — serving `00629-f7k` ships: `live_path_v2_pret_from_tick` (this-tick bid only; missing bid → pret None → STEP 0 HOLD; never mark / `pnl_pct`); leftover OCC skip while owned (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`); discovery scan slot must not wait on a running watch (`unified_discovery_scan_slot_free`); Telegram flush is notification-only (`ledger_alert_flush.py`, FIFO 32 / 3 attempts / 8s, drops never touch ledger/fills/GCS); desk Bid MFE% vs Mark MFE%. 5s + PATH_V2 + recording at 38/50 is a safe-degradation test (late bid cov ~27%/30%, fill→first eval ~14.4s, no mark-as-pret / stale exits / duplicate STC) — TTL 4s/8s not raised. Sequential inner OCC is architectural, not a broker concurrency proof. Serving `00629-f7k` (Nov 6 runtime pack + Nov 5 UL/OCC). Rollback `00628-pf4`. November 5, 2026 — serving `00625-wwv`: no UL/OCC cancel or `to_thread` stack; OCC apply-on-complete; sequential inner OCC batches of 25 with a lane yield to `equity_batch` between waves; OCC defers a *new* fetch only while last exact is reusable (`OCC_DEFER_FOR_UL_MAX_S=12`); every eval JSONL row keeps `path_v2_peak` / `path_pret` / `path_v2_reason`, quote clocks, `entry_premium`, contracts, and $0.65×2 fees. Rollback is `00624-8bn`. On `00624-8bn`: every PATH_V2 evaluation writes exactly one compact JSONL row from `_0dte_run_stealth_exit_decision` (`evaluated=true`, keyed by `trade_id` + `monitor_tick_id`) with `eval_now` / `eval_age_s` (the clock passed to `evaluate_path_v2_tick`) plus `prem_vel_10`. Path `timestamp` is the monitor/write clock; `broker_quote_timestamp` stays the broker field; `observation_timestamp` is write time. Replay uses `eval_now` / `eval_age_s` (historical rows without `eval_now` keep `timestamp`) and sorts by parsed `eval_now` epoch, then `monitor_tick_id`. One evaluate per `(trade_id, monitor_tick_id)`: HOLD stores `out=None` so Trendline/ORB reuse that tick (`PATH_V2_EVAL_DUP_BLOCKED`); `PATH_V2_EVAL_COUNT` increments after evaluate, before the JSONL write. Dedicated `_monitor_loop` calls that same `maybe_tick`; `_ul_refresh_loop` (2s) calls `refresh_underlying_spot_cache_async` so evaluate only reads cache. In-tick owned UL join runs only when the UL task is down. JSONL persist is 5s; daily JSON stays 45s. `_monitor_loop` always pauses (min 0.2s) and an empty book clears `_0dte_force_monitor_tick`. Watchdog restarts that loop; it does not evaluate under a second tick id. Tick ids are `u0dte_<ms>` only — a whole-second leftover is reminted. Each evaluate tick resets UL labels (`begin_underlying_tick`). `_LINE_COUNTS` is locked and cleared after a GCS JSONL download. Restart rebuild keeps the real entry time and does not reset a live HWM (`_rebuild_path_v2_restore`). Serving Demo **`YOUR_SERVICE_NAME-00629-f7k`** ships the Nov 4 cadence, the Nov 5 UL/OCC pack, and the Nov 6 runtime pack: leave the UL fetch running (`ul_refresh_slow` / `ul_refresh_hung`, cancel=false), in-tick join only if the loop is down, PATH_V2 pret keys recorded (`path_pret` is `live_path_v2_pret_from_tick`, this-tick bid only), 20% snapshot does not wait 3s for hub, sequential OCC inner batches + UL lane yield. STEP 2 Save stays ON CALL-only. Monitor 2s (3s at ≥20 opens); every open exit-checked; flight JSONL is the only in-trade persist. **In `00624-8bn` (persist clocks from `00619-xr6`):** owned underlyings skip_cache-fetch a live E\*TRADE last (oldest first, cap 25); JSONL keeps UL provenance so replay cannot treat a frozen last as a 0s quote. Priority Optimizer persist (off the exit check) is time-correct from that same tick: session tech uses bars closed by the event; executed copies the handoff freeze (same keys and digest as `po_profile_decision`); `first_red` ages the live OCC quote already on the tick (`po_monitor_quote_at`); mid-trade greeks restamp from that OCC quote; stale or unread chain volumes are nulled (`*_now` empty when stale); flatten primes remaining opens once; no extra chain or equity fetch. Discovery equity (24 names / 10s) does not wait on exit-quote latency; path-chain refresh skips only when the last cycle exceeds **8000ms**, and `force_live` when the bag is older than 60s. Protect / premium-stop / BE floors are **exit decisions**, not fills. Profit Capture and monitor **decisions** stay **bid vs entry, no fees** (`pct` fill-gross). Booked / Telegram / desk `$` is fee-inclusive settlement — [0DTEProfitCapture.md §3.11](0DTEProfitCapture.md#311-fills-and-net-pnl-true-to-live).  
**Desk:** RESEARCH Demo Cloud Run **`YOUR_SERVICE_NAME-00629-f7k`**. Sim ledger **$100,000** (seed flag off). FINAL Live is not the work target. Rollback: `00628-pf4`.  
**Document role:** **Canonical guide** for **open-position monitoring** across **Demo Mode** and **Live Mode**: app structure, registries, cadence, exact-contract quotes, evaluate → latch → close → alert → **post-exit side cooldown**, and the code/functions that own each step.  
**Scope:** ORB1 Breakout · Trendline 0DTE · Breakout 0DTE (**Demo + Live**). Equity ORB SO monitoring is **out of scope** (see [0DTEORB1Breakout.md Part II](0DTEORB1Breakout.md#part-ii--orb-so-demo-mode-historical--retired-in-live-app)).  
**Public document.** Account IDs and tokens → operator private secrets (not in this repository). Project URLs/revisions → operator cloud inventory (not in this repository).  
**Sibling guides:** The open that enrolls here → **[0.OrderExecution.md](0.OrderExecution.md)**. Exit **reasons / ladders / economics** → **[0DTEProfitCapture.md](0DTEProfitCapture.md)**. Path entry → [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTETrendline.md](0DTETrendline.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md). Pre-fill risk (incl. **1h reject/exit cooldown**) → [0DTERisk.md §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit). Brokers / adapter → [Brokers.md](Brokers.md). System map → [ARCHITECTURE.md](ARCHITECTURE.md). Live cutover → [LiveModeReadiness.md](LiveModeReadiness.md).

**How to read this guide**

| Layer | Sections | Purpose |
|-------|----------|---------|
| **Mode ownership** | [§1 Mission](#1-architecture-mission) · [§2 Demo vs Live](#2-demo-mode-vs-live-mode) | What is shared vs mode-specific truth, writers, close, ledger |
| **Cadence** | [At a glance](#at-a-glance) · [§3 Sole cadence knob](#3-sole-cadence-knob) | What “~2s monitoring” means and what must not stretch it |
| **Identity & roster** | [§4 Registry lifecycle](#4-open-position-registry-lifecycle) | Fill → pending → monitored → clear/quarantine (Demo + Live) |
| **Tick spine** | [§5 Unified monitor](#5-unified-monitor-tick) · [§6 Exact quotes](#6-live-exact-contract-quotes) | One `maybe_tick` · one quote batch · evaluate |
| **Contention** | [§7 Request priority](#7-contended-request-lanes--priority) | Open OCC vs Trendline/Breakout watch vs PO shadow vs chain bridge |
| **Exit spine** | [§8 Evaluate → close](#8-evaluate--latch--close--alert) | Premium snapshot · stealth · latch · mode close · Telegram · **post-exit 1h side cooldown** |
| **Code map** | [§9 Module & function catalog](#9-module--function-catalog) | File → class/function → Demo/Live role |
| **Ops** | [§10 Config](#10-configuration) · [§11 Grep tokens](#11-forensic-log-tokens) · [§12 Failure modes](#12-known-failure-modes--p0-fixes) | Knobs, greps, regressions to never reintroduce |

---

## At a glance

| Contract | Value |
|----------|--------|
| **Sole cadence knob** | `0DTE_POSITION_MONITOR_INTERVAL_SEC=2` (floor **0.5s**) |
| **Canonical scheduler** | Dedicated `_monitor_loop` always pauses after `maybe_tick` (min 0.2s; idle still schedules the next interval). An empty book clears `_0dte_force_monitor_tick` so a wake / pending registration / watchdog cannot spin the loop. Main loop starts that task and does not serialize a second `maybe_tick` while it is alive. A live monitor task makes `watchdog_should_run` false — the 30s watchdog wakes or restarts the loop, it does not evaluate under a second tick id. Fill enroll still sets `_0dte_force_monitor_tick` only (no second full-book tick, no `main_loop_tail`). |
| **Quote mode** | `0DTE_EXIT_QUOTE_MODE=exact_contract_batch` — exact OCC / E\*TRADE colon wire first |
| **Valuation side** | Bid (`0DTE_LONG_EXIT_PRICE_FIELD=bid`) — true-to-live STC economics |
| **Priority while open** | Owned exact OCC quotes own the Market Quote budget for the **full open duration** |
| **Who yields** | Trendline / Breakout universe watches · full-chain bridge · **closed** shadow OCC on the **owned** batch (`0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0`). **Local:** leftover OCC is skipped while any owned name is open (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`) because leftover shares `occ_fetch` inflight with owned exact quotes. Serving `00625-wwv` still quotes leftover after evaluate on a single-flight task. |
| **Wide book** | At `0DTE_MONITOR_WIDE_OPEN_COUNT=20` the tick is `0DTE_MONITOR_WIDE_INTERVAL_SEC=3`. Every PATH_V2 eval writes one JSONL row on that tick (`evaluated=true`, `eval_now` / `eval_age_s`); it is not a separate 2s sample clock. |
| **Eval budget** | `0DTE_MONITOR_EVAL_BUDGET_MS=1200` · `0DTE_MONITOR_EVAL_BUDGET_MS_OPEN20=1800` when 20+ opens (cap 2200ms). A hit is logged. **Every open name is still exit-checked** on that tick, and the flight recorder writes that same tick. |
| **Underlying on evaluate** | Cache only: `fresh_batch` \| `shared_cache` \| `last_good` \| `unavailable`. Age wins: ≤**4000ms** usable · ≤**8000ms** last_good/`stale` · >**8000ms** always unavailable. No evaluate-path `get_quote`. Each tick calls `begin_underlying_tick` so labels do not carry over. A **2s** `_ul_refresh_loop` calls existing `refresh_underlying_spot_cache_async`. **On `00625-wwv`:** does **not** `wait_for`-cancel the batch (4s `ul_refresh_slow` / 30s `ul_refresh_hung`, cancel=false) and will not start a second HTTP while the first `to_thread` is alive; in-tick join only when that loop task is down. OCC apply-on-complete; after the 1.5s slow window evaluate last reusable exact bids without cancelling the worker. Inner OCC batches of 25 run **one HTTP at a time** and `yield_lane_to_equity_batch` between waves. A new OCC fetch is deferred only while last exact is reusable (`OCC_DEFER_FOR_UL_MAX_S=12`). Open exact batch 25. Executed `maybe_tick` + shared Market lane load test: `tests/test_full_monitor_shared_lane.py` (20/38/50, 16 ticks at 5s RTT). Cap 25 + 5s RTT still cannot keep 38/50 names under the 8s last_good cutoff (second-half UL miss ~42% / ~56% on the stub; with real PATH_V2 + JSONL, late bid cov ~27%/30%, fill→first eval ~14.4s) — TTL not raised. Sequential OCC is architectural, not a broker concurrency proof. At 0.45s RTT the same monitor is 0% after warmup. Discovery/last cache is not restamped as `fresh_batch`. Rollback `00624-8bn` still 4s-cancels UL and 1.5s-cancels OCC. |
| **Fair owned eval** | `await run_fair_owned_eval_tick` (P0–P3) with `asyncio.sleep(0)` between names, then `_position_still_open` so an EOD flatten during the yield cannot exit twice. `shared_cache=0%` with unique underlyings is correct (first consumer = `fresh_batch`). |
| **PO persist** | Same owned tick → compact `premium.jsonl` (`PREMIUM_JSONL_KEYS`), including UL provenance and `eval_now` / `eval_age_s`. `PROFIT_OPTIMIZER_JSONL_MIN_WRITE_GAP_SEC=5` appends per-trade files; `PROFIT_OPTIMIZER_PERSIST_MIN_WRITE_GAP_SEC=45` rewrites the daily metrics JSON. JSONL row counts stay in memory (`path_jsonl._LINE_COUNTS`). Restart rebuilds PATH_V2 (`_rebuild_path_v2_restore` → `attach_jsonl_paths` + `_restore_trade_from_dict` + tracker HWM). Daily JSON is metrics-only (≤32 MiB). `POSITION_EVAL_MS` must not include GCS wall time. |
| **Discovery while open** | Next **24** / **10s**. That floor stays. Cursor always advances. A slow quote batch does not cancel the rotation. **Local:** a running watch task must not occupy the scan slot (`unified_discovery_scan_slot_free`). |
| **Dedup** | Unified ticks skip only **same `tick_id`**. Every mint (main, fallback, watchdog, Trendline/ORB fast loop) is `canonical_monitor_tick_id` → `u0dte_<ms>`. Only an existing `u0dte_<ms>` id is reused; a whole-second leftover gets a new millisecond id. |
| **Demo open truth** | `MockOptionsExecutor.active_positions` (`D-…` trade ids) via `OptionsTradingExecutor.get_open_positions()` |
| **Live open truth** | Broker portfolio → `BrokerPositionSynchronizer` → `options_executor.positions` (`L-…`) |
| **Shared evaluate path** | Exact OCC → PremiumEngine → stealth `evaluate` → `exit_latch` (both modes) |
| **Close diverge** | Demo → mock STC @ reconstructed executable **bid** + net fees, GCS `mock_options_history.json`; Live → broker STC fill + modeled fees, `live_options_history.json` |

```text
Fill (ORB / Trendline / Breakout)
  → enqueue_0dte_pending_monitor(trade_id, …)
  → set `_0dte_force_monitor_tick` + `request_wake` (fill_enroll flag-only; no second full-book maybe_tick)
  → `_monitor_loop` → maybe_tick(~2s; 3s at ≥20 opens; immediate when force)
       → [Live] BrokerPositionSynchronizer.synchronize()
       → [Demo] audit_demo_dual_book_drift + mock open roster
       → rebuild roster + ActiveOptionContractRegistry
       → build_prioritized_exact_quote_plan (open first; closed shadow 0)
       → fetch_exact_contract_quotes (sequential inner batches of 25; yield equity_batch between waves)
       → owned-miss isolated recovery (1-symbol each)
       → evaluate reads `resolve_underlying_observation` (2s `_ul_refresh_loop` owns skip_cache last; in-tick join only if that task is down)
       → monitor_0dte_options_positions → await run_fair_owned_eval_tick (yield between names)
            → first_seen → OPTION_POSITION_MONITOR_START
            → resolve_underlying_observation (age first: ≤4s / ≤8s / >8s)
            → PremiumEngine.evaluate → frozen PremiumSnapshot
            → stealth.evaluate (local: pret = this-tick bid/entry−1; missing bid → HOLD) → ExitDecision (dispatch_exit_decision in try/except)
            → in-memory compact premium_path + enqueue po-persist (JSONL 5s; daily JSON 45s, off this thread)
            → HOLD | latch SELL/FORCE_SELL → close → enqueue notification-only Telegram flush (local; ledger/fills untouched)
```

---

## 1. Architecture mission

Open-position monitoring exists to keep **exit marks true to live executable bids** at a **stable ~2s cadence**, and a **live underlying last** on the same tick, so profit-capture rules (premium stops and UL-gated STEP 9 / 12 / 17 / 29 / 30) can fire on **fresh, identity-matched** contract quotes and a broker last — not mid-only, synthetic, cached, or a restamped frozen spot.

**Demo and Live share this mission.** Mode only changes **where open inventory is stored**, **who may mutate the registry**, and **how a close is settled and persisted**. Quote identity, premium snapshot, stealth thresholds, and latch semantics are intentionally identical so Demo soak evidence transfers to Live.

**Non-goals of this layer:**

- Extra broker fetches for session tech / Trade Profile keys on the owned tick (hub + tape already on the snap). Owned equity last is the 2s `_ul_refresh_loop` skip_cache pass (cap 25), not a per-position `get_quote`. Evaluate does not join that fetch while the loop is alive.
- Cancelling the 10s / 24-name discovery rotation while the book is open (universe **equity polls** yield; the discovery cursor still advances — see [§7](#7-contended-request-lanes--priority))
- Full options-chain hydration every tick (bridge only when exact OCC is incomplete; **path chain-skew live refresh is discovery-async**)
- A second Profit-Optimizer broker fetch (PO consumes the same quote batch)
- A second rolling premium-velocity / continuation score on the monitor path (PATH_V2 already copies `prem_vel_10` onto the JSONL row; extra continuation scores stay offline from `premium_path`)
- Equity ORB SO position monitoring (separate mock trading ledger)

**Compact exit-research path (Oct 27 2026; UL provenance Oct 30; eval clock Nov 4; engine+qty local Nov 5):** There is no second monitor persist. Every PATH_V2 evaluation writes exactly one row from `_0dte_run_stealth_exit_decision` (`evaluated=true` bypasses the 2s sample throttle), keyed by `(trade_id, monitor_tick_id)`, and carries `eval_now` / `eval_age_s` (the clock passed to `evaluate_path_v2_tick`) plus the engine's `prem_vel_10`. **Local until next deploy:** the same compact row also keeps `path_v2_peak` / `path_pret` / `path_v2_reason`, `quote_age_ms`, `entry_premium`, `contracts` / `quantity`, and modeled `opening_fees` / `closing_fees` / `round_trip_fees` (`stamp_eval_record_fields`). `actual_exit_*` copies only when metadata already has a fill — never invented from the current bid. Historical tapes before this pack do not have those keys; `prep_replay_ticks(..., entry_premium=)` may join a book entry at replay without rewriting JSONL. HOLD stores `out=None` in `_owned_tick_eval`; later Trendline/ORB calls on the same tick reuse that result (`reuse_owned_tick_eval`, `PATH_V2_EVAL_DUP_BLOCKED`) and do not re-evaluate. Write-dedupe (`_already_wrote_eval`) is not an evaluation skip. `PATH_V2_EVAL_COUNT` increments after evaluate, before the JSONL write. Path `timestamp` stays the monitor/write clock; replay uses `eval_now` / `eval_age_s` and sorts by parsed epoch. Each live and shadow `premium_path` sample is the compact JSONL row (`PREMIUM_JSONL_KEYS`): exact-quote depth (`bid_size` / `ask_size`), `last`, `cumulative_volume` (broker `volume` / `totalVolume`; missing stays `null`), timestamps (`timestamp` = monitor/write clock; `broker_quote_timestamp` = broker quote clock; `observation_timestamp` = write time; `eval_now` = evaluate clock), trade-state (entry, HWM, MFE %, giveback, seconds since entry), plus **cache-only** session tech and Trade Profile review keys (RSI/MACD/VWAP/PVE3/`volume_3m`/`volume_ratio`/ATR/BB/`distance_from_vwap_atr`/`otm_pct`/shares/tape `dir_pct_change_5m`/underlying L1). UL last is the 2s `_ul_refresh_loop` skip_cache pass (in-tick join only if that task is down); provenance rides the same row (`underlying_source`, `underlying_age_ms`, `underlying_observation_timestamp`, `underlying_stale`, `underlying_quote_age_sec`) so replay cannot treat a frozen last as a 0s quote. Stamp: `record_path_v2_eval_row` → `observe_owned_path_tick` → `stamp_underlying_provenance` → `stamp_live_rsi` → `stamp_owned_monitor_review_keys`. Session-tech / TP review stamps do not add a second OCC or equity `get_quote`. Shadow rows add actual-exit anchors; `live_hwm_at_exit` / `shadow_hwm_since_exit` on the trade summary. The exit row references the deciding `monitor_tick_id` and its `eval_now`. Priority Optimizer snapshots stay event-based (handoff / 20p / exit) — not polled every 2s. Closed-trade `exit_bid` / `exit_premium` is the frozen `entry_option_symbol` bid (`resolve_frozen_contract_exit_bid`; log `PATH_V2_EXIT_BID_MISMATCH` when claimed diverges > 25%). Key list: [Data.md §7](Data.md#7-hold--monitor--path-refresh) · [profit_optimizer/README.md](../profit_optimizer/README.md#owned-tick-stamp-open-position-review-keys).

**One owner per concern:**

| Concern | Owner |
|---------|--------|
| Scheduler / cadence | `_monitor_loop` → `Unified0DTEPositionMonitor.maybe_tick` (`ensure_background_tasks` / `request_wake`) |
| Position membership (Demo) | `MockOptionsExecutor.active_positions` (via `get_open_positions`) |
| Position membership (Live) | `BrokerPositionSynchronizer` (sole writer of `executor.positions`) |
| Frozen quote wire / OCC identity | `ActiveOptionContractRegistry` + `exact_contract_option_quotes` |
| Quote lane priority | `open_position_quote_orchestration` |
| Premium / PnL mark | `premium_engine.evaluate` → frozen `PremiumSnapshot` (bid mark; **dollars net of fees** for display; **pct fill-gross / bid vs entry, no fees** for HWM, stops, ARM, PX2) |
| Underlying cache | `_ul_refresh_loop` → `refresh_underlying_spot_cache_async` (2s skip_cache owned last, cap 25) → `resolve_underlying_observation` (age first; never evaluate `get_quote`) |
| Fair owned eval | `await owned_eval_scheduler.run_fair_owned_eval_tick` (yield between names) |
| PO persist | `TradeFlightRecorder._schedule_persist` / `po-persist` (JSONL **5s** / daily JSON **45s**; `_LINE_COUNTS` locked; not on owned eval) |
| HOLD / SELL / FORCE_SELL | stealth `evaluate()` → `ExitDecision` |
| Breach persistence | `exit_latch` |
| Close (Demo) | mock `close_position` / `record_external_closed_position` + `persist_demo_close_from_position` |
| Close (Live) | broker STC via adapter → fill confirm → `reconcile_confirmed_close` → remove only when broker flat |
| Telegram exit ensure | path alert helpers (ORB ensure / Trendline / Breakout) |
| Post-exit re-entry RM | Same-side **1h cooldown** (ORB1 / BO / TL slot+side) — [§8.4](#84-same-side-1h-cooldown-after-exit) · [0DTERisk §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit) |

---

## 2. Demo Mode vs Live Mode {#2-demo-mode-vs-live-mode}

`MODE=demo` vs `MODE=live` (see `modules/runtime_mode.py`). Both modes use **live E\*TRADE market data** for chains and exact OCC quotes when `REQUIRE_LIVE_OPTION_DATA=true`. Only **fills, cash, and closed ledgers** are simulated in Demo.

RESEARCH and FINAL share this monitor spine. Live open truth is always the broker options book (`ETRADE_0DTE_ACCOUNT_ID`; operator private secrets (not in this repository)). Run only one app Live on that account. Cap stays `0DTE_MAX_POSITION_SIZE_SUM=600` until the desk changes that env key.

### 2.1 Ownership matrix

| Concern | Demo Mode | Live Mode |
|---------|-----------|-----------|
| **Open-book truth** | `MockOptionsExecutor.active_positions` keyed by `D-…` | `OptionsTradingExecutor.positions` keyed by `L-…`, repaired from broker |
| **Roster API** | `executor.get_open_positions()` → `mock.get_open_positions()` when `_use_mock_fills()` | `executor.get_open_positions()` → non-`closed` rows in `self.positions` |
| **Who may create opens** | Mock fill path inside executor / Trendline executor | **Only** `BrokerPositionSynchronizer.register_confirmed_fill` after broker fill confirm |
| **Who may mutate qty / HWM** | Mock book; premium patch via synchronizer routes to **mock** rows | Synchronizer `_live_write_token` only (`apply_premium_state_update`, qty repair) |
| **Pre-tick broker sync** | `synchronize()` → `skipped=True, reason=demo_mode` | `adapter.get_positions()` → repair matrix every tick |
| **Evaluate / latch** | Same | Same; latch may **defer broker dispatch** when sync gate / ownership stale |
| **Close execution** | Simulated STC @ executable bid (`resolve_true_to_live_exit_fill`) → mock close; never a protect-floor stamp | Real STC via `BrokerAdapter.submit_close_order` + `reconcile_order` (`average_fill_price` when `broker_fill_confirmed`) |
| **Registry remove** | Remove from `active_positions` on mock close / ghost purge | Remove **only after** broker reports qty 0 / missing (broker-flat) |
| **Closed ledger** | `research_app/mock_options_history.json` (GCS) | `research_app/live_options_history.json` (GCS) |
| **Cash / sizing** | Unified Demo account / mock balance | Broker buying power via adapter balance |

### 2.2 Topology

```text
                    ┌─────────────────────────────────────┐
                    │  _monitor_loop → maybe_tick            │
                    │  (~2s · exact OCC · cache-only UL)     │
                    └───────────────┬─────────────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              │                                           │
              ▼ MODE=demo                                 ▼ MODE=live
   MockOptionsExecutor                         BrokerPositionSynchronizer
   active_positions (D-…)                      synchronize() ← get_positions()
        │                                           │
        │ get_open_positions                        ▼
        └──────────► OptionsTradingExecutor    executor.positions (L-…)
                     (demo_mode=True)               │
                                                    │ get_open_positions
                                                    ▼
                                             evaluate / latch / STC
```

### 2.3 Demo Mode deep dive

**Truth:** [`easy0DTE/modules/mock_options_executor.py`](../easy0DTE/modules/mock_options_executor.py) — `MockOptionsExecutor.active_positions`.

| Function / attribute | Role |
|----------------------|------|
| `active_positions` | Open Demo inventory |
| `get_open_positions()` | Purge closed IDs from active; return open mock positions |
| `close_position(...)` | Simulated close + move to closed list |
| `record_external_closed_position(...)` | Ledger close when open row already gone / ghost purge |
| GCS load/save | Merge closed; **never** rehydrate IDs already closed |

**Executor routing** ([`options_trading_executor.py`](../easy0DTE/modules/options_trading_executor.py)):

| Method | Demo behavior |
|--------|---------------|
| `_use_mock_fills()` | `demo_mode and mock_executor` |
| `get_open_positions()` | Delegates to mock |
| `get_position(id)` | Mock first, then `self.positions` |
| Open / close execute | Mock fills; broker submit/close **blocked** on adapter (`demo_mode_broker_*_blocked`) |

**Dual-book hazard:** Live-shaped `executor.positions` can retain ghost open rows while mock is truth. Every roster collect runs:

```text
audit_demo_dual_book_drift(executor)   # modules/unified_0dte_monitor_registry.py
  → logs DEMO_REGISTRY_DRIFT when non-closed executor.positions ⊄ mock.active_positions
  → never constructs a cold MockOptionsExecutor (GCS RMW footgun)
```

**Cold-mock rule:** [`persist_demo_close_from_position`](../modules/live_options_ledger.py) **skips** if `mock_executor` is missing (`DEMO_OPTIONS_LEDGER | persist_skipped | reason=missing_mock_executor`). Constructing a second in-memory mock against GCS can wipe active opens.

**Demo ghosts:** `_demo_0dte_handle_persistent_quote_ghost` force-ledger-closes and purges when a position repeatedly gets **no** leg quote — `ORB_0DTE_GHOST_POSITION_PURGED` / `DEMO_0DTE_GHOST_PURGED`.

**Seed:** `DEMO_SIM_LEDGER_SEED_ON_STARTUP` → `modules/demo_sim_ledger_seed.py` (one boot only, then remove the env var). Oct 29: `00616-w4l` wrote **$100,000** / 0 trades; `00617-992` removed the flag. Pre-wipe backup: `gs://YOUR_GCS_BUCKET/backups/research_app_pre_reseed_20261029/`.

### 2.4 Live Mode deep dive

**Truth:** Broker portfolio is authoritative. Local book is `options_executor.positions`, mutated **only** by [`BrokerPositionSynchronizer`](../modules/broker_position_synchronizer.py).

| Method | Role |
|--------|------|
| `synchronize()` | Fetch adapter positions → qty/avg repair → local-only quarantine → broker-flat remove → broker-only import |
| `register_confirmed_fill(ConfirmedOpenFact)` | Create `L-…` row after broker open fill confirm |
| `reconcile_confirmed_close(ConfirmedCloseFact)` | Mark awaiting broker flat; **do not** delete until flat |
| `apply_premium_state_update(...)` | HWM / unrealized patch under write token |
| `put_position` / `remove_position` | Sole-writer primitives |
| `recover_on_startup()` | Startup reconcile |
| `should_skip_normal_exits_for_stale_registry(system)` | Ownership gate helper |

**`get_positions` contract** ([`broker_adapter.py`](../modules/broker_adapter.py) · [Brokers.md](Brokers.md)):

| Outcome | Adapter | Synchronizer |
|---------|---------|--------------|
| True flat | `[]` | May apply broker-flat repairs |
| Fetch / auth / missing method / stub | raise `broker_positions_unavailable…` | `sync_unavailable` — **keep local**, do not false-flat |
| Option row | `{symbol: spaced OCC, quantity, avg_price?, raw}` | Match via OCC keys (`broker_live_audits._broker_position_keys`) |

E\*TRADE: `PrimeETradeTrading.get_positions()` preferred (raises on empty response / parse fail); adapter falls back to `get_portfolio()` + `normalize_broker_position_rows` (Product → OCC).

**Repair policy** ([`position_registry_contract.py`](../modules/position_registry_contract.py) `REPAIR_POLICY`):

| Mismatch | Policy |
|----------|--------|
| Qty mismatch | Broker qty wins |
| Avg fill mismatch | Broker avg wins (tolerance ~2%) |
| Local only | Grace (~120s pending visibility) → quarantine |
| Broker flat / local open | Close local as externally closed; remove |
| Broker only | Import as `STRATEGY_RECOVERED_UNKNOWN` / emergency-safe |

**Ownership SLAs** (env): `0DTE_OWNERSHIP_SYNC_TARGET_SEC=2`, block warning/critical 3s/5s, urgent sync timeout 2s. Stale sync sets `_unified_0dte_sync_gate_dispatch` — **evaluate still runs**; latch may defer **dispatch**.

**Live closed ledger:** `persist_live_close_from_position` / `persist_live_close_from_lifecycle` require broker close confirmation before GCS append.

### 2.5 What must stay identical

| Layer | Invariant |
|-------|-----------|
| Cadence | Same `0DTE_POSITION_MONITOR_INTERVAL_SEC` |
| Quotes | Exact OCC batch, bid STC, exit-grade |
| Premium | `premium_engine.evaluate` → frozen snapshot |
| Stealth | Same profile keys (`orb_0dte` / `trendline_0dte` / `breakout_0dte`) |
| Latch | First breach wins; survives quote gaps |
| Alerts | Path-correct Telegram ensure once per `trade_id` |

---

## 3. Sole cadence knob {#3-sole-cadence-knob}

**Resolver:** `modules/dte0_exit_monitor_config.py` → `resolve_0dte_position_monitor_interval_sec()` / `resolve_exit_monitor_config()`.

| Env | Default | Effect |
|-----|---------|--------|
| `0DTE_UNIFIED_POSITION_MONITOR_ENABLED` | `true` | Gate for unified `maybe_tick` vs legacy per-path wrappers |
| `0DTE_POSITION_MONITOR_INTERVAL_SEC` | **2.0** (floor 0.5) | Base tick, main-loop sleep cap, legacy fast-monitor backups, watchdog `max(15, interval×10)` |
| `0DTE_MONITOR_WIDE_OPEN_COUNT` / `0DTE_MONITOR_WIDE_INTERVAL_SEC` | **20** / **3** | Effective tick once this many names are open. JSONL still writes every PATH_V2 eval on that tick. |
| `0DTE_DISCOVERY_INTERVAL_SEC` | **10** (floor 10) | Separate from the monitor. Next 24 names. Not reduced when the book is open. |

**Main-loop contract** (`prime_trading_system.py`):

1. `start()` / each turn calls `ensure_background_tasks()`. While `_monitor_loop` is alive, the main loop does **not** `await maybe_tick`. Fallback `maybe_tick(trigger_source="main_loop")` only if that task is down.
2. Legacy `_maybe_run_trendline_fast_monitor` / `_maybe_run_orb_0dte_fast_monitor` **early-return** when unified is enabled (must not double-invoke).
3. Adaptive sleep **caps at the monitor interval** whenever any 0DTE position (or PO continuation) is open — never reintroduce a silent 30s sleep that starves exits.
4. ~30s `position_monitor_tick` is **watchdog-only** (`watchdog_should_run`). While `_monitor_loop` is alive it wakes or restarts that loop; it does not evaluate positions under a second tick id.

**Grep:** `UNIFIED_0DTE_MONITOR` · `MAIN_LOOP_SLEEP` · `MONITOR_CADENCE_SUMMARY`

---

## 4. Open-position registry lifecycle {#4-open-position-registry-lifecycle}

**Identity of record:** `trade_id` / `position_id` — sole key for pending, first_seen, alert dedupe, ghost quarantine.

**Contract identity (quoting):** structured key `underlying|expiration|call_put|strike` via `structured_identity_key()` — dedupes OCC / OSI / spaced / colon spellings of the same contract. Never rely on fragile string equality alone.

### 4.1 Fill → pending → monitored

```text
Executor fill (ORB / Trendline / Breakout)
  → OPTION_POSITION_REGISTERED
  → [Live] BrokerPositionSynchronizer.register_confirmed_fill(ConfirmedOpenFact)
  → [Demo] mock.active_positions[trade_id] = position
  → enqueue_0dte_pending_monitor(pending, trade_id, symbol, strategy_type)
       pending[trade_id] = {symbol, strategy_type, registered_ts}
  → (next unified tick sees roster)
  → stamp_0dte_first_seen_on_monitor_tick(...)
       first_seen[trade_id] = now; pop pending
  → OPTION_POSITION_MONITOR_START   (once)
```

| Module | Role |
|--------|------|
| `modules/unified_0dte_monitor_registry.py` | `enqueue_0dte_pending_monitor` · `stamp_0dte_first_seen_on_monitor_tick` · `clear_0dte_monitor_identity` · `sweep_0dte_pending_not_monitored` · `collect_stale_0dte_trade_ids` · **`audit_demo_dual_book_drift`** |
| `modules/position_registry_contract.py` | Canonical `PositionRecord`, `PremiumSnapshot`, `ExitDecision`, repair/quarantine vocabulary, `ConfirmedOpenFact` / `ConfirmedCloseFact` |
| `modules/active_option_contract_registry.py` | Per-tick rebuild from open positions; **frozen quote wire only** — fail-closed if missing (degraded monitoring, no synthetic wire) |
| `modules/unified_0dte_account.py` | `list_orb_open_positions` / `list_trendline_open_positions` · capital snapshot · Breakout rows bucket as **ORB-like** (strategy_type has no `trendline` substring) |
| `modules/broker_position_synchronizer.py` | Live sole writer · Demo no-op sync · premium apply routing |

### 4.2 Enrollment escalation (never silently unmonitored)

`sweep_0dte_pending_not_monitored()` every tick:

| Stage | Log token | Meaning |
|-------|-----------|---------|
| Within grace | `0DTE_MONITOR_PENDING_ENROLLMENT` | INFO — just filled |
| Past grace | `0DTE_MONITOR_ENROLLMENT_DELAYED` | WARNING |
| Failed | `0DTE_MONITOR_ENROLLMENT_FAILED` / `0DTE_POSITION_NOT_MONITORED` | ERROR — one-shot |

### 4.3 Close cleanup + ghost quarantine

On confirmed close:

1. `clear_0dte_monitor_identity()` — pops first_seen, pending, not_monitored_logged, MFE heartbeat key  
2. `_orb_0dte_quarantine_ghost(trade_id, "post_close_ledger")` — blocks late duplicate ticks  
3. **Demo:** ledger-close (`persist_demo_close_from_position` / mock close) then purge mock registries  
4. **Live:** `reconcile_confirmed_close` → local remains until `synchronize()` sees broker flat → remove  

**Persistent quote ghosts (Demo):** `_demo_0dte_handle_persistent_quote_ghost` force-ledger-closes and purges when a position repeatedly gets **no** leg quote — logs `ORB_0DTE_GHOST_POSITION_PURGED` / `DEMO_0DTE_GHOST_PURGED`.

**Stale force:** `0DTE_MONITOR_STALE` when `last_monitor_tick_ts` lags (~45s default) → force an extra cycle.

### 4.4 Qty / size integrity (Demo fill alerts)

After Liquidity Guard (or any risk clamp) changes filled quantity, payload fields must match the true book:

| Helper | Module | Ensures |
|--------|--------|---------|
| `sync_payload_to_filled_quantity` | `easyTrendline/trendline_options_executor.py` | Alert / PO payload qty ↔ filled qty |
| `resolve_true_position_size_usd` | same | Deployed $ from true contracts × premium (+ fees) |
| PO `_resolve_position_size_usd` | `profit_optimizer/trading_hooks.py` | Does not persist stale budget cost |

Tests: `tests/test_fill_qty_sync_after_risk_clamp.py`.

---

## 5. Unified monitor tick {#5-unified-monitor-tick}

**Module:** `modules/unified_0dte_position_monitor.py` · class `Unified0DTEPositionMonitor` · singleton `get_unified_0dte_position_monitor()`.

### 5.0 Background tasks

`ensure_background_tasks()` (from `start()` and the main loop) starts two asyncio tasks on the existing singleton:

| Task | Calls | Cadence |
|------|-------|---------|
| `_monitor_loop` | existing `maybe_tick(trigger_source="monitor_task")` | always pause after a tick (min 0.2s); interval or `request_wake`. Idle books schedule `_next_tick_monotonic` and **clear** `_0dte_force_monitor_tick` |
| `_ul_refresh_loop` | existing `refresh_underlying_spot_cache_async` | every **2s**. **On `00625-wwv`:** one inflight batch, never `wait_for`-cancel at 4s or 30s (`ul_refresh_slow` / `ul_refresh_hung`, `cancel=false`); next oldest-first wave starts as soon as inflight completes; in-tick join only if this task is down. OCC inner batches yield this lane between waves. Rollback `00624-8bn` 4s-cancels. Does **not** `begin_underlying_tick` (evaluate does, each tick) |

Fill enroll still only sets the force flag + `request_wake`. It never starts a second full-book `maybe_tick`.

### 5.1 `maybe_tick(system, trigger_source)`

- Monotonic schedule: `_next_tick_monotonic`  
- Single `asyncio.Lock` — no overlapping evaluate cycles  
- **Overlap policy:** if a tick is still running, suppress a second evaluate but **still deliver PO samples** from the last completed tick’s cached OCC quotes (`_deliver_po_samples_from_last_tick`) so research sampling never stalls. Overlap does **not** restamp a discovery/cache last as `fresh_batch`.  
- **Overlap starve alert:** `_overlap_streak` increments on suppress; resets when a full tick acquires the lock. At **≥5** consecutive suppressions → `WARNING` with `evaluate_starved=true` (telemetry extras: `overlap_streak`, `overlap_suppressions_total`)  
- Missed deadline: jump to `now + interval` (no burst catch-up storm)  
- Unique `tick_id` via `canonical_monitor_tick_id` (`u0dte_<ms>`). Only an existing `u0dte_<ms>` (12+ digit milliseconds) is reused; a whole-second leftover (`1700000000`, `u0dte_1700000000`) is reminted. Trendline and ORB fast loops mint the same way (`now=current_time`). Unified callers skip only `duplicate_same_tick`.

### 5.2 `_run_once()` spine

1. **Live** `BrokerPositionSynchronizer.synchronize()` — ownership gate (evaluate still runs; dispatch may defer). **Demo:** skipped.  
2. `rebuild_system_position_registry()` → re-collect open positions + PO continuations (**never** evaluate the pre-sync object list)  
3. `ActiveOptionContractRegistry.sync_from_positions()` — frozen wires only  
4. `build_prioritized_exact_quote_plan(...)` — open OCC first; closed shadow **0** on this batch  
5. Debounced OCC-expiry repair (`_occ_repair_due`: ≥15× interval, or 5× while incomplete)  
6. `fetch_exact_contract_quotes()` — inner batches of 25 run **sequentially** (one OCC HTTP). After each batch except the last, `yield_lane_to_equity_batch` lets a waiting UL refresh finish before OCC batch 2. Do not start a second OCC generation while `_OCC_FETCH_INFLIGHT`. After a successful apply, defer a *new* OCC fetch only while last exact is reusable (`OCC_DEFER_FOR_UL_MAX_S=12`). In-tick skip_cache equity last (`refresh_underlying_spot_cache_async`, cap 25) runs only when `_ul_refresh_loop` is not alive. Evaluate while OCC is inflight reuses last exact. Tick extras stamp option-quote age, positions handed / skipped, and fill-to-first-eval.  
7. Owned misses → `recover_missing_owned_exact_quotes()` (isolated 1-symbol fetches; **not** a second full batch)  
8. Populate `system._unified_0dte_exact_quotes`; decide chain-bridge need / defer  
9. When the 2s UL loop is down, join the in-tick owned underlying refresh. Do **not** pass discovery/last cache as `quotes=`. Age is the broker `date_time_utc`. Path `timestamp` is the monitor/write clock. Replay uses `eval_now`.  
10. `system.monitor_0dte_options_positions(...)` → `await run_fair_owned_eval_tick` — cache-only underlying at evaluate; pending fills never skip  
11. In-memory Profit Optimizer `premium_path`; `_checkpoint` marks dirty + `_schedule_persist` (`po-persist` — JSONL + metrics JSON/GCS **off** this thread)  
12. Telemetry + one `UNIFIED_0DTE_MONITOR` summary line  

### 5.3 Roster merge (who is monitored)

`_collect_open_positions()`:

1. `audit_demo_dual_book_drift(executor)` when Demo mock path is active  
2. Merge:
   - `dte0_manager.options_executor.get_open_positions()`
   - `system._trendline_unified_open_positions()`
   - `system._orb_unified_open_positions()`

Deduped by `position_id`. Strategy split happens **inside** evaluate:

| Bucket | Stealth profile | Includes |
|--------|-----------------|----------|
| ORB-like | `orb_options_stealth` | `orb_0dte`, **`breakout_0dte`**, other non-trendline |
| Trendline | `trendline_options_stealth` | `strategy_type` contains `trendline` |

---

## 6. Live exact-contract quotes {#6-live-exact-contract-quotes}

**Module:** `modules/exact_contract_option_quotes.py`.

Applies to **both Demo and Live** — Demo does not invent exit marks when `REQUIRE_LIVE_OPTION_DATA=true` / synthetic exit quotes are off.

### 6.1 Wire form and matching

- Requests use official E\*TRADE **colon** form: `underlier:year:month:day:CALL|PUT:strike` via `identity_to_etrade_quote_symbol`.  
- Acceptance: `match_quote_to_requested_occ()` requires **structured identity equality** when parseable — `identity_mismatch` is a hard reject even if the wire string echoes.  
- **Bid of 0.00 is valid** (worthless long); only `bid is None` is missing.

### 6.2 Freshness

`apply_max_age()`:

| Mode | Age basis |
|------|-----------|
| `broker_timestamp_verified` | E\*TRADE `dateTimeUTC` |
| `broker_timestamp_absent_conservative_receipt` | Local receipt age, capped at `min(max_age_sec, 3.0)` |

Older than `0DTE_EXIT_QUOTE_MAX_AGE_SEC` (default **8s**) → rejected · source `stale_exact_quote`.

### 6.3 Batching and pacing

| Knob | Default | Notes |
|------|---------|--------|
| `ETRADE_BATCH_QUOTE_SIZE` | **25** | Broker hard cap without override |
| `0DTE_OPEN_EXACT_QUOTE_BATCH_SIZE` | **25** | Inner OCC wave size (`Shared.env`). N>25 is **two sequential HTTP waves**, not one gather |
| `0DTE_EXIT_QUOTE_BATCH_CONCURRENCY` | **4** | Env still pinned. **Local primary OCC waves do not stack:** `_one_batch` runs in a `for` loop; after each wave except the last, `yield_lane_to_equity_batch`. One OCC HTTP at a time (`occ_max_inflight==1`) |
| `0DTE_EXIT_QUOTE_TIMEOUT_SEC` | **1.5** | Per-batch HTTP timeout |
| `ETRADE_MARKET_API_INTER_REQUEST_SEC` | **0.28** | Self-throttle (~3.5 rps vs broker **4 rps** / **14k/hr**) |

**1002 / empty recovery:** alternate spellings via `etrade_quote_symbol_candidates` — log `EXACT_CONTRACT_QUOTE_1002_RECOVERY`. That path is for wire format only. If `option_expiry_memo.is_empty` for that underlier+day, skip recovery and `EXACT_CONTRACT_QUOTE_HARD_FAIL` (`empty_expiry_memo_skip_1002_recovery`). OCC repair does not walk days; a same-day stamp whose day is memo-empty fetches the fill `selected_expiry_ymd` or `nearest_for` (`OCC_EXPIRY_REPAIR | stage=empty_same_day_use_listed`). Chain premium (`fresh_chain_refresh`) stays a valid exit-grade mark while OCC is quarantined.

### 6.4 Exit-grade policy

Strict classification (`modules/exit_grade_policy.py` when `0DTE_STRICT_EXIT_GRADE_ENABLED=true`):

| Verdict | May raise HWM / normal profit exit? |
|---------|-------------------------------------|
| `normal_exit_grade` | Yes — e.g. `exact_quote`, `bid_executable`, `fresh_chain_refresh`, `fresh_chain_bridge` |
| `emergency_risk_grade` | Timeout / no-progress safety only (bypass flag) |
| Rejected | synthetic / modeled / proxy / cached / stale / mid-only / underlying-derived |

**Aug 10 — Stage B / PE:** PremiumEngine `_usable_exact_oq` must accept source **`exact_quote`** (unified monitor rename). Requiring only `exact_contract*` left marks on `fresh_chain_refresh` and **blocked** `EXIT_GRADE_DURABLE_CONFIRMED` / Stage B Telegram even when exact OCC fetches succeeded. Fill/position stealth register stamps exact enrollment; DEMO mock enrolls the same wire. Grep: `ORB_0DTE_STAGE_B_PROMOTION_AUDIT` · `EXIT_GRADE_DURABLE_CONFIRMED`.

### 6.5 Degraded ladder (never invent marks)

1. Reuse last trusted exact quote **only while** an async refresh is in flight (does not relax freshness)  
2. Isolated single-contract recovery  
3. Chain bridge — **deferred** while open positions own the budget or owned exact is incomplete  
4. `EXIT_QUOTE_AWAITING_MARKET_DATA` (rate-limited)  
5. Eventually `no_data` / `degraded_data` exit reasons  

### 6.6 Force-refresh semantics (P0 — never conflate)

`resolve_force_refresh_underlyings()`:

| Value | Meaning |
|-------|---------|
| `None` | Legacy derive-from-all-open-underlyings (watchdog / kill-switch fallback only) |
| **`set()` empty** | Exact quotes complete → **zero** full-chain hydration |
| Non-empty set | Bridge **only** those underlyings with missing OCC |

Locked by `tests/test_unified_monitor_p0_corrections.py`.

---

## 7. Contended request lanes & priority {#7-contended-request-lanes--priority}

**Module:** `modules/open_position_quote_orchestration.py`. Shared HTTP gate: `modules/broker_api_pacing.py`.

While any 0DTE position is open, **owned exact OCC** wins the E\*TRADE Market Quote budget for the **full open duration** (`mark_open_quote_priority` · `open_quote_priority_active`). One Market HTTP at a time. Lane order: `open_exact_occ` (pri 0) > `equity_batch` (pri 6). **On `00625-wwv`:** after each OCC inner batch except the last, `yield_lane_to_equity_batch` waits until UL acquires then finishes (0.35s start / 30s hold) so a 38-name book can stamp UL between OCC wave 1 and wave 2. A *new* OCC generation is deferred only while last exact is reusable (`OCC_DEFER_FOR_UL_MAX_S=12`). Yield must not suppress expired option bids: when last exact is not reusable, OCC runs. Rollback `00624-8bn` still stacks OCC waves and 4s-cancels UL.

### 7.1 One prioritized plan per tick

`build_prioritized_exact_quote_plan()`:

| Lane | Rule |
|------|------|
| **Open positions** | Always included, always first, never capped |
| **PO / shadow continuations** | **0** on the owned 2s batch (`0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0`). After owned evaluate, leftover OCC **and leftover underlyings that left the open book** are quoted on a single-flight task off the owned lock (`0DTE_MAX_SHADOW_LEFTOVER_PER_TICK=0` = every winner) **when the skip flag is off**. **Local default on:** `0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true` skips leftover OCC while any owned name is open (leftover shares `occ_fetch` with owned exact). `0DTE_PATH_REFRESH_SKIP_CYCLE_MS` remains discovery path-chain only. `PROFIT_OPTIMIZER_SHADOW_SAMPLE_SEC=2`, `PROFIT_OPTIMIZER_MAX_SHADOW_CONTINUATIONS=0`. Losing / flat exits do not shadow (`PROFIT_OPTIMIZER_LOSS_SHADOW_SECONDS=0`). Observe is not skipped for book size (`0DTE_PO_CONTINUATION_MAX_OPEN=0`). First-shadow jump quarantine drops the leftover immediately. `shadow_path_trusted` is identity/jump only — it does **not** mean the leftover cadence is 2s-clean. |
| **Dedup** | Structured identity — duplicate spellings count once |

### 7.2 Who must yield

| Caller | Deferral API | Behavior |
|--------|--------------|----------|
| Trendline / Breakout universe equity polls | `should_defer_non_critical_scans(system)` | Skip extra watch-list equity polls while OCC owns the budget. **Do not** cancel or stretch `0DTE_DISCOVERY_INTERVAL_SEC=10` / 24 names. Skip scheduling if the unified monitor lock is held. |
| Full-chain bridge | `should_defer_chain_bridge(...)` | Defer if `owned_exact_incomplete` or `shadow_only_while_open` |
| Profit Optimizer live sampling | (no fetch) | Consumes `exact_quotes_by_occ` / premium snapshots from the unified tick only |
| Offline `priority_optimizer/` retrieve | N/A | GCS/analytics — does not compete for live quote budget |

### 7.3 Budget guards that protect the 2s cadence

| Guard | Rule |
|-------|------|
| Chain-bridge wall time | Skip heavy bridge if pre-bridge elapsed ≳ **55%** of tick interval |
| OCC repair debounce | ≥ **15×** interval ( **5×** while incomplete) |
| Owned-miss recovery | Isolated 1-symbol fetches — **must not** re-issue the full primary batch (`_schedule_open_quote_refresh` is an intentional no-op stub) |

**Contended resources:** E\*TRADE Market Quote API · options chain fetch (bridge/repair only) · unified monitor `asyncio.Lock` · broker close submission (latch + ownership gate).

---

## 8. Evaluate → latch → close → alert {#8-evaluate--latch--close--alert}

```text
monitor_0dte_options_positions(...)          # prime_trading_system.py
  → PremiumEngine path:
        premium_engine.evaluate(request)
          → frozen PremiumSnapshot (bid-based, exit-grade classified)
        BrokerPositionSynchronizer.apply_premium_state_update(...)
          → Demo: mock.active_positions · Live: executor.positions
  → orb_options_stealth.evaluate    [ORB + Breakout]
  → trendline_options_stealth.evaluate [Trendline]
        → ExitDecision(HOLD | SELL | FORCE_SELL, reason, thresholds, diagnostics)
  → HOLD → next tick
  → SELL/FORCE_SELL
        → latch_exit_decision()          # first breach wins; survives quote/sync gaps
        → should_defer_broker_dispatch?  # Live ownership gate
        → unified_executor.close_position_with_data(...)
             Demo → mock close @ reconstructed executable bid (not protect floor)
             Live → BrokerAdapter.submit_close_order + reconcile fill
        → close_confirmed + lifecycle_record + ledger persist
        → clear_0dte_monitor_identity + quarantine ghost
        → path exit Telegram (idempotent per trade_id / session; headline = net P&L)
        → Profit Optimizer observe_0dte_exit
        → arm same-side 1h cooldown (ORB1 / BO / TL)   # reject-parity RM — opposite side open
```

### 8.1 Module map (exit spine)

| Module | Role |
|--------|------|
| `modules/premium_engine.py` | `evaluate` / `snapshot` / `unavailable_snapshot` — frozen mark; `compute_unrealized_pnl` dollars include fees |
| `modules/options_trade_economics.py` | `resolve_true_to_live_exit_fill` · `net_result_pnl_dollars` / `_fraction` |
| `modules/prime_alert_manager.py` | Exit Telegram uses reconstructed bid + net P&L |
| `modules/desk_action_pulse.py` | Compact exit pulses for the desk Event Tape |
| `modules/desk_discovery_pulse.py` | `desk_public/discovery.json` (scan-only; never WAIT_BREAK persist) |
| `modules/monitor_premium_snapshot.py` | Helpers to build / attach path fields around PE |
| `modules/prime_options_stealth_trailing_tp.py` | Threshold logic (take_profit, MFE giveback, price_runner, BE, scratch, time, no-progress, EOD, data safety) |
| `modules/exit_latch.py` | Idempotent breach persistence · ownership-gated dispatch · duplicate-order prevention |
| `modules/exit_decision_dispatch.py` | `ExitDecision` ↔ legacy tuple · optional `STEALTH_EVALUATE_SHADOW` parity (`OPTION_STEALTH_USE_EVALUATE_API`) |
| `easy0DTE/modules/options_exit_reason_catalog.py` | Canonical reasons + legacy aliases |
| `modules/broker_position_synchronizer.py` | Live ownership truth · sync SLAs · premium apply |
| `modules/live_options_ledger.py` | `persist_demo_close_from_position` · `persist_live_close_from_*` · `persist_options_close_from_position` |
| `easy0DTE/modules/mock_options_executor.py` | Demo close + GCS mock ledger |
| `modules/broker_adapter.py` | Live STC submit / cancel / get_positions |

### 8.2 Close persistence

| Mode | Function | Guard |
|------|----------|-------|
| Demo | `persist_demo_close_from_position(position, mock_executor=…)` | Requires live mock instance — **no cold construct** |
| Live | `persist_live_close_from_position` / `persist_live_close_from_lifecycle` | Requires broker close confirmed |
| Router | `persist_options_close_from_position` | Branches on `is_demo_mode` / `is_live_mode` |

### 8.3 Alerts

**Alert ensure (ORB/Breakout book):** `_orb_0dte_ensure_exit_alert_sent` — session set `_orb_0dte_exit_alerts_sent`; Breakout titles via `_position_dict_is_breakout_0dte`.  
**Trendline:** dedicated `send_trendline_position_exit_alert` + `_trendline_exit_alerts_sent`.  
**Recovery:** ledger-closed but alert missing → `_orb_0dte_recover_exit_alert_from_ledger` / Trendline recover helper.

Canonical primary exits and thresholds: **[0DTEProfitCapture.md §3–§4](0DTEProfitCapture.md)** — this guide owns **cadence, marks, and mode ownership**, not ladder tuning. Fill vs decision: **[§3.11](0DTEProfitCapture.md#311-fills-and-net-pnl-true-to-live)**. Open `$` / cur% on the desk uses the same net mark as Telegram. `SETTLE_AT_STOP` still **fires** the −60% premium stop; it must not **record** −60% as the fill.

### 8.4 Same-side 1h cooldown after exit {#84-same-side-1h-cooldown-after-exit}

**Risk Management (Aug 10):** When a monitored position **closes** (Demo or Live), the exit spine arms the **same 1 hour strategy/symbol/side cooldown** used for terminal execution rejects. This stops **immediate same-side re-entry** after a green (or red) exit while leaving the **opposite CALL/PUT** eligible for a reverse breakout.

| Path | Armed from | Key | Duration |
|------|------------|-----|----------|
| **ORB1** | `_save_orb_0dte_exit_priority_snapshot` → `arm_orb1_side_cooldown_after_exit` | `symbol + side` | `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS` (**3600**) |
| **ORB2 Breakout** | `_save_breakout_exit_priority_snapshot` (+ live BO exit alert) → `arm_breakout_side_cooldown_after_exit` | `symbol + side` | same |
| **Trendline TL1–TL3** | `_save_trendline_exit_priority_snapshot` → `arm_trendline_post_exit_cooldown` | `symbol + slot + side` | same |

**Trigger:** **Any** filled exit reason (profit or loss) — `price_stop`, `asset_stop`, trail/TP, scratch, EOD, etc.  
**Not a substitute for:** open-position same-side dedupe while still flat-pending, or BO day-executed lock on losers.  
**BO green exit:** may still clear day-executed + re-arm `WAIT_BREAK` / ORB2 reclaim, but the **exited side remains on the 1h cooldown**.

Full policy, reject triggers, and desk rules: **[0DTERisk.md §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit)**.

**Grep:** `ORB1_SIDE_COOLDOWN_BLOCK` · `BREAKOUT_SIDE_COOLDOWN_BLOCK` · `TRENDLINE_SLOT_SIDE_COOLDOWN_BLOCK` · `exited_side_cooldown_1h` · `stage=post_exit_cooldown`

---

## 9. Module & function catalog {#9-module--function-catalog}

### 9.1 Scheduler / roster

| File | Symbol | Demo | Live |
|------|--------|------|------|
| `modules/unified_0dte_position_monitor.py` | `ensure_background_tasks` · `_monitor_loop` · `_ul_refresh_loop` · `request_wake` · `canonical_monitor_tick_id` | Same | Same |
| same | `Unified0DTEPositionMonitor.maybe_tick` | Same evaluate | Same evaluate |
| same | `_run_once` | Skip BPS sync; in-tick UL only if UL loop down | BPS.synchronize first |
| same | `_collect_open_positions` | Drift audit + mock roster | Broker-backed roster |
| same | `_deliver_po_samples_from_last_tick` | Overlap PO | Overlap PO |
| same | `get_unified_0dte_position_monitor` | Singleton | Singleton |
| `modules/unified_0dte_monitor_registry.py` | `enqueue_0dte_pending_monitor` | Same | Same |
| same | `stamp_0dte_first_seen_on_monitor_tick` | Same | Same |
| same | `clear_0dte_monitor_identity` | Same | Same |
| same | `sweep_0dte_pending_not_monitored` | Same | Same |
| same | `audit_demo_dual_book_drift` | **Active** | Skipped |
| `modules/dte0_exit_monitor_config.py` | `resolve_exit_monitor_config` | Same | Same |
| `modules/prime_trading_system.py` | `monitor_0dte_options_positions` | Same evaluate body | + ownership gate |
| `modules/owned_eval_scheduler.py` | `run_fair_owned_eval_tick` | Same (async, yield between names) | Same |
| `modules/underlying_freshness.py` | `resolve_underlying_observation` · `select_symbols_for_owned_refresh` | Age-first cache; oldest-first cap | Same |
| `modules/underlying_spot_stamp.py` | `refresh_underlying_spot_cache_async` | 2s UL loop; fallback in-tick when loop down | Same |
| `modules/owned_tick_observe.py` | `observe_owned_path_tick` · `reuse_owned_tick_eval` · `record_path_v2_eval_row` · `count_path_v2_eval` · `stamp_eval_record_fields` | Same — `evaluated` · `monitor_tick_id` · HOLD `out=None` reuse · one JSONL write · local engine/qty/fee stamps | Same |
| `modules/option_api_accounting.py` | option-API accounting | Same | Same |

### 9.2 Books / sync / adapter

| File | Symbol | Demo | Live |
|------|--------|------|------|
| `easy0DTE/modules/options_trading_executor.py` | `get_open_positions` | → mock | → `positions` |
| same | `_use_mock_fills` | True when mock bound | False |
| `easy0DTE/modules/mock_options_executor.py` | `active_positions` / `close_position` | **Truth** | N/A |
| `modules/broker_position_synchronizer.py` | `synchronize` | no-op skip | Sole reconcile |
| same | `register_confirmed_fill` | N/A (mock fill) | Open enroll |
| same | `reconcile_confirmed_close` | N/A | Close pending → flat |
| same | `apply_premium_state_update` | Patch mock | Patch executor |
| `modules/broker_adapter.py` | `get_positions` / `normalize_broker_position_rows` | Unused for book | Required |
| `modules/prime_etrade_trading.py` | `get_positions` / `get_portfolio` / `_parse_portfolio_response` | Market data account | Portfolio → OCC |
| `modules/broker_live_audits.py` | `_broker_position_keys` / `_position_contract_key` | — | Sync matching |
| `modules/position_registry_contract.py` | `PremiumSnapshot`, `REPAIR_POLICY`, facts | Shared types | Shared types |

### 9.3 Quotes / premium / exit

| File | Symbol | Role |
|------|--------|------|
| `modules/exact_contract_option_quotes.py` | `fetch_exact_contract_quotes`, `prepare_exact_quote_requests` | Sequential inner OCC batches of 25; `yield_lane_to_equity_batch` between waves |
| `modules/broker_api_pacing.py` | `market_lane_active`, `yield_lane_to_equity_batch` | One Market HTTP; OCC pri 0 > `equity_batch` pri 6 |
| `modules/open_position_quote_orchestration.py` | `build_prioritized_exact_quote_plan`, `mark_open_quote_priority` | Lane priority |
| `modules/active_option_contract_registry.py` | `sync_from_positions` | Frozen quote wires |
| `modules/option_contract_identity.py` | `identity_from_*`, `occ_from_identity` | OCC / Product / colon |
| `modules/premium_engine.py` | `evaluate`, `snapshot`, `compute_unrealized_pnl` | Frozen mark; dollars net of fees |
| `modules/options_trade_economics.py` | `resolve_true_to_live_exit_fill`, `net_result_pnl_dollars` | Bid reconstruct + fees |
| `modules/prime_alert_manager.py` | `send_strategy_position_exit_alert` | Telegram net headline |
| `modules/exit_grade_policy.py` | exit-grade classification | HWM / profit eligibility |
| `modules/prime_options_stealth_trailing_tp.py` | `evaluate` (ORB + TL engines) | ExitDecision |
| `modules/exit_latch.py` | `latch_exit_decision`, `should_defer_broker_dispatch` | Breach + Live gate |
| `modules/exit_decision_dispatch.py` | `dispatch_exit_decision` | API / shadow parity |
| `modules/live_options_ledger.py` | `persist_*_close_*` | Mode ledgers |
| `modules/path_exec_day_exclude.py` | `arm_trendline_post_exit_cooldown` | TL slot+side 1h after exit |
| `modules/breakout_watch.py` | `arm_breakout_side_cooldown_after_exit` | BO side 1h after exit |
| `modules/orb1_0dte_watch.py` | `arm_orb1_side_cooldown_after_exit` | ORB1 side 1h after exit |
| `modules/prime_trading_system.py` | `_save_*_exit_priority_snapshot` | Exit snap + cooldown arm |
| `profit_optimizer/trading_hooks.py` | `observe_0dte_tick` · `stamp_live_rsi` · `stamp_owned_monitor_review_keys` · `flush_po_persist_on_shutdown` | Cache-only session tech + TP review keys; shutdown flush |
| `profit_optimizer/path_jsonl.py` | `compact_premium_row` / `PREMIUM_JSONL_KEYS` / `_LINE_COUNTS` / `invalidate_line_count` / `attach_jsonl_paths` | Persist whitelist · locked cached counts (cleared after GCS download) · `PATH_V2_RESTORE` |
| `profit_optimizer` flight recorder | `TradeFlightRecorder.observe_premium_tick` / `capture_exit` / `_checkpoint` / `_schedule_persist` / `_restore_trade_from_dict` | Eval ticks: `evaluated=true` bypasses sample throttle. Close: frozen-contract `exit_bid`. Restore keeps entry time; never `save_daily_dataset` on eval |

### 9.4 Tests (monitor / registry)

| Test | Locks |
|------|-------|
| `tests/test_unified_monitor_p0_corrections.py` | Cadence · force_refresh empty set · no double tick |
| `tests/test_monitor_cadence_and_reuse.py` | Sleep cap · quote reuse |
| `tests/test_main_loop_0dte_monitor_cadence.py` | No second main-loop tick (`main_loop_tail`) |
| `tests/test_monitor_performance_budget.py` | Fill flag-only + `request_wake`; idle pause + force-flag clear; same-second tick ids both evaluate; watchdog no dual-eval; JSONL 5s / daily 45s |
| `tests/test_true_to_live_exit_monitor.py` | Bid STC economics |
| `tests/test_pre_arm_profit_protect.py` | Protect floor is a decision, not the fill |
| `tests/test_max_pnl_telegram_units.py` | Telegram / tape net (DELL −$54.60 · GLD −$429) |
| `tests/test_broker_position_synchronizer.py` | Repair matrix · sync_unavailable |
| `tests/test_broker_adapter_get_positions.py` | Portfolio → OCC · no silent empty |
| `tests/test_demo_registry_dual_book.py` | Drift audit · cold-mock skip |
| `tests/test_fill_qty_sync_after_risk_clamp.py` | Qty / deployed $ sync |
| `tests/test_forced_mismatch_certification.py` | Live forced mismatch cert |
| `tests/test_exit_settlement_alert_integrity.py` | Exit stamp vs live bid (no entry×1.25) |
| `tests/test_post_exit_side_cooldown.py` | Post-exit 1h same-side cooldown (ORB1 / BO / TL) |
| `tests/test_breakout_profit_reentry.py` | BO green exit + exited-side cooldown |
| `tests/test_owned_tick_cadence_contract.py` | Cache-only evaluate · age >8s unavailable · owned refresh cap · no cached-quotes restamp |
| `tests/test_underlying_spot_stamp.py` | skip_cache live last stamps `fresh_batch`; cached quotes do not |
| `tests/test_po_persist_off_eval_path.py` | Checkpoint enqueue only; persist worker coalesces; budget overrun still evaluates |
| `tests/test_path_rsi_stamp.py` | Compact JSONL keeps RSI/PVE/BB/OTM/share/L1 **and** UL provenance |
| `tests/test_path_v2_exit_units.py` | Recorder writes `evaluated=true` ticks; frozen-contract exit bid; PT clock |
| `tests/test_path_v2_eval_recorder_align.py` | One eval per tick · HOLD `out=None` reuse · write-dedupe ≠ eval-count · replay epoch sort |
| `tests/test_pc_parity_1103_1104.py` | Live/replay parity (IWM-285p-777 eval-clock `hwm_rtc` +$987.30 fee-inclusive) |
| `tests/test_full_monitor_shared_lane.py` | Executed `maybe_tick` + one Market HTTP; 16-tick 5s UL miss at 38/50 (TTL not raised). Optional `path_v2_record` runs real PATH_V2 + JSONL + latch. |
| `tests/test_1106_runtime_inputs.py` | Local 11/6 pack: pret HOLD, leftover skip, discovery scan slot, Telegram notification-only, caller→jsonl→replay, Bid vs Mark MFE, 0.45s 38/50 coverage, 5s slow-bid safety |
| `tests/test_wide_book_quote_apply.py` | Sequential OCC inner batches · `yield_lane_to_equity_batch` · `occ_max_inflight==1` |
| `tests/test_wide_book_followup.py` | Compact keys · `stamp_eval_record_fields` · `prep_replay_ticks` entry join · settlement formula |

---

## 10. Configuration {#10-configuration}

### 10.1 Cadence / quote / latch (canonical)

| Env | Default | Notes |
|-----|---------|--------|
| `0DTE_UNIFIED_POSITION_MONITOR_ENABLED` | `true` | Unified vs legacy wrappers |
| `0DTE_POSITION_MONITOR_INTERVAL_SEC` | **2** | Base monitor tick. JSONL writes every PATH_V2 eval (`evaluated=true`), not a 2s sample throttle inside that tick. |
| `0DTE_MONITOR_WIDE_OPEN_COUNT` / `0DTE_MONITOR_WIDE_INTERVAL_SEC` | **20** / **3** | Effective tick once this many names are open |
| `0DTE_DISCOVERY_INTERVAL_SEC` | **10** (floor 10) | Next 24 names. Not cancelled while the book is open. |
| `0DTE_MONITOR_EVAL_BUDGET_MS` | **1200** | Warning only. Every open is still exit-checked; recorder writes that tick. |
| `0DTE_MONITOR_EVAL_BUDGET_MS_OPEN20` | **1800** | Same — log on overrun, do not skip (cap 2200ms) |
| `0DTE_MAX_SHADOW_LEFTOVER_PER_TICK` | **0** | Every winner leftover OCC after owned eval, off the owned lock. Positive cap is operator-only. |
| `0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW` | **true** | Skip leftover OCC while any owned name is open. Leftover shares `occ_fetch` inflight with owned exact quotes. |
| `0DTE_UNDERLYING_FRESH_MAX_MS` | **4000** | Age ≤ this = fresh / usable |
| `0DTE_UNDERLYING_LAST_GOOD_MAX_MS` | **8000** | Age ≤ this = last_good/`stale`; **> this always unavailable** |
| `0DTE_UNDERLYING_OWNED_REFRESH_MAX` | **25** | Unique owned names skip_cache-refreshed per tick (stale first, then the rest; one E\*TRADE batch). 0 = unlimited. |
| `0DTE_OPEN_EXACT_QUOTE_BATCH_SIZE` | **25** | Inner OCC wave (broker error 1023 above 25). Local and `00625-wwv` waves are sequential + UL yield. Sequentialization is architectural, not a broker concurrency proof. Env `0DTE_EXIT_QUOTE_BATCH_CONCURRENCY=4` does not stack a second OCC HTTP. |
| `0DTE_PATH_REFRESH_SKIP_CYCLE_MS` | **8000** | Skip discovery path-chain live refresh only when the last cycle is past the last-good quote age |
| `0DTE_QG_CHAIN_CACHE_WHILE_OPEN_SEC` | **600** | Layer C dir% may reuse cached chain while positions are open |
| `0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN` | **2** | Live QG chain fetches per discovery scan while open |
| `0DTE_DISCOVERY_MAX_QUOTES_WHILE_OPEN` | **24** | Discovery equity cap while any position is open |
| `0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN` | **0** | Closed PO OCC never on the owned 2s batch (quoted after evaluate) |
| `0DTE_PO_CONTINUATION_MAX_OPEN` | **0** | Never skip leftover observe for book size (`fill_enroll` still skips one tick) |
| `0DTE_EXIT_QUOTE_MODE` | `exact_contract_batch` | Anything else forces full-chain path |
| `0DTE_EXIT_QUOTE_TIMEOUT_SEC` | `1.5` | Exact quote HTTP timeout |
| `0DTE_EXIT_QUOTE_MAX_AGE_SEC` | `8` | Freshness reject |
| `0DTE_EXIT_QUOTE_BATCH_CONCURRENCY` | **4** | Env still 4. Local primary OCC does not use it to stack HTTP |
| `0DTE_FORCE_CHAIN_REFRESH_BRIDGE` | `true` | Bridge allowed but still deferrable |
| `0DTE_STRICT_EXIT_GRADE_ENABLED` | `true` | Strict source classification |
| `0DTE_LONG_EXIT_PRICE_FIELD` | `bid` | STC economics |
| `0DTE_MFE_HWM_PRICE_FIELD` | `bid` | HWM series |
| `0DTE_GIVEBACK_PRICE_FIELD` | `bid` | Giveback series |
| `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS` | `true` | Bid STC + spread/book quality |
| `OPTION_USE_BID_FOR_LONG_EXIT` | `true` | Long exits mark at bid |
| `OPTIONS_OPEN_COMMISSION_PER_CONTRACT` | **0.65** | Modeled E\*TRADE-style open fee |
| `OPTIONS_CLOSE_COMMISSION_PER_CONTRACT` | **0.65** | Modeled close fee (both sides on every result) |
| `0DTE_EXIT_LATCH_ENABLED` | `true` | Breach latch |
| `OPTION_STEALTH_USE_EVALUATE_API` | `true` | Prefer `ExitDecision` API (code default) |
| `OPTION_STEALTH_EVALUATE_SHADOW` | `false` | Shadow parity log |
| `REQUIRE_LIVE_OPTION_DATA` | `true` | Demo + Live require real broker option data |
| `0DTE_ALLOW_SYNTHETIC_EXIT_QUOTES` | `false` | Kill synthetic exit marks |
| `0DTE_DEMO_SYNTHETIC_CHAIN` | `false` | Kill synthetic demo chains |
| `ETRADE_BATCH_QUOTE_SIZE` | `25` | `Data.env` |
| `ETRADE_MARKET_API_INTER_REQUEST_SEC` | `0.28` | `Data.env` |
| `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS` | **3600** | Shared same-side 1h reject **and** post-exit cooldown (`Breakout0DTE.env`) — [§8.4](#84-same-side-1h-cooldown-after-exit) · [0DTERisk §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit) |

### 10.2 Live ownership SLAs

| Env | Default |
|-----|---------|
| `0DTE_OWNERSHIP_GRACE_SEC` | `45` (code default; pin in env if ops need override) |
| `0DTE_OWNERSHIP_SYNC_TARGET_SEC` | `2` |
| `0DTE_OWNERSHIP_BLOCK_WARNING_SEC` | `3` |
| `0DTE_OWNERSHIP_BLOCK_CRITICAL_SEC` | `5` |
| `0DTE_OWNERSHIP_URGENT_SYNC_TIMEOUT_SEC` | `2` |

### 10.3 Where keys live

| App | Primary files |
|-----|----------------|
| **RESEARCH** | `configs/Shared.env` + mirrors/comments in `ORB0DTE.env` · pacing in `Data.env` |
| **FINAL** | `configs/Shared.env` + mirrors in `ORB0DTE.env` · pacing in `Data.env` · comments in `ProfitCapture.env` / `System.env` |

Code-identical monitor stack across apps; env **file organization** is now the same (`Shared.env` in both trees). Full knob index: [Configs.md](Configs.md).

---

## 11. Forensic log tokens {#11-forensic-log-tokens}

### Cadence / tick

`UNIFIED_0DTE_MONITOR` · `overlapping_suppressed` · `evaluate_starved` · `overlap_streak` · `fill_enroll_flag_only` · `MONITOR_CADENCE_SUMMARY` · `MONITOR_CADENCE_SUMMARY_DETAIL` · `MAIN_LOOP_SLEEP` · `OPTION_POSITION_MONITOR_TICK` · `OPTION_POSITION_MONITOR_START` · `POSITION_EVAL_MS` · `PATH_V2_EVAL_COUNT` · `PATH_V2_EVAL_DUP_BLOCKED` · `PATH_V2_EVAL_INTERVAL` · `quote_shadow_leftover_skipped` · `live_path_v2_pret_from_tick` · `unified_discovery_scan_slot_free` · `flush_notification_batch` · `notification_only`

### Underlying cache / PO persist

`UNDERLYING_CACHE` · `PO_PERSIST` · `PO_PERSIST_QUEUE` · `po_observe_ms` · `po_enqueue_ms`

### Quotes / bridge / repair

`EXACT_CONTRACT_QUOTE` · `EXACT_CONTRACT_QUOTE_MATCH` · `EXACT_CONTRACT_QUOTE_1002_RECOVERY` · `EXACT_CONTRACT_QUOTE_BRIDGE` · `EXACT_QUOTE_RELIABILITY` · `EXIT_QUOTE_REUSED_LAST_EXACT` · `EXIT_QUOTE_AWAITING_MARKET_DATA` · `OPEN_POSITION_QUOTE_RECOVERY` · `OCC_EXPIRY_REPAIR` · `CHAIN_BRIDGE_HIT` · `ORB_0DTE_MONITOR_CHAIN_HYDRATE_SKIPPED` · `TRENDLINE_PIPELINE | stage=monitor_chain_hydrate_skipped` · `BROKER_LANE_COUNTERS`

### Registry / ghosts / ownership

`OPTION_POSITION_REGISTERED` · `0DTE_MONITOR_PENDING_ENROLLMENT` · `0DTE_MONITOR_ENROLLMENT_DELAYED` · `0DTE_MONITOR_ENROLLMENT_FAILED` · `0DTE_POSITION_NOT_MONITORED` · `0DTE_MONITOR_STALE` · `0DTE_MONITOR_EMPTY_OPEN_SET` · `0DTE_MONITOR_FILTERED_EMPTY` · `ORB_0DTE_GHOST_QUARANTINED` · `ORB_0DTE_GHOST_POSITION_PURGED` · `DEMO_0DTE_GHOST_PURGED` · `DEMO_REGISTRY_DRIFT` · `DEMO_OPTIONS_LEDGER` · `BROKER_POSITION_SYNC` · `BROKER_POSITIONS`

### Exit / alert

`EXIT_LATCHED` · `EXIT_LATCHED_BUT_SYNC_BLOCKED` · `EXIT_OWNERSHIP_GATE_CLEARED` · `STEALTH_EVALUATE` · `STEALTH_EVALUATE_SHADOW` · `TRENDLINE_EXIT_EXECUTION_CONFIRMED` · `TRENDLINE_CLOSE_FAILED` · `ORB_0DTE_EXIT_EXECUTION_CONFIRMED` · `OPTION_EXIT_WITHOUT_POSITION` · `BREAKOUT_OPTION_EXIT_ALERT_SENT` · `ORB_0DTE_OPTION_EXIT_ALERT_SENT`

### Post-exit / reject same-side cooldown

`BREAKOUT_SIDE_COOLDOWN_BLOCK` · `TRENDLINE_SLOT_SIDE_COOLDOWN_BLOCK` · `ORB1_SIDE_COOLDOWN_BLOCK` · `exited_side_cooldown_1h` · `stage=post_exit_cooldown` · `stage=profit_reentry`

### Premium

`OPTION_STEALTH_PREMIUM_PNL_AUDIT` · `OPTIONS_STEALTH_PREMIUM_STATE` · `OPTIONS_STEALTH_MONITOR_CADENCE`

---

## 12. Known failure modes & P0 fixes {#12-known-failure-modes--p0-fixes}

| Issue | Fix / invariant | Tests / grep |
|-------|-----------------|--------------|
| Dual ORB + Trendline fast monitors double-ticking | **One** `maybe_tick` owner (`_monitor_loop`); main loop does not serialize a second tick while that task is alive; legacy wrappers no-op | `test_unified_monitor_p0_corrections.py` · `test_main_loop_0dte_monitor_cadence.py` |
| Serial equity `get_quote` inside evaluate (IBM-class stall at 35-open on 2.37.6) | Evaluate is **cache-only**; 2s `_ul_refresh_loop` owns skip_cache last (cap 25) | `UNDERLYING_CACHE` · `test_owned_tick_cadence_contract.py` |
| 4s `wait_for` cancelled the owned UL batch (11/5: ~half of 08:00–09:00 PT cycles); 30s hang-cancel stacked a second `to_thread`; OCC `wait_for` discarded a finished batch → STEP 9 / ULR dark and missed bids | Loop leaves the fetch running (`ul_refresh_slow` / `ul_refresh_hung`, `cancel=false`). OCC `await_uncancelled_executor` applies a completed worker. In-tick join only when the loop task is down. Exact batch 25. | `test_wide_book_quote_apply.py` · `test_monitor_performance_budget.py` |
| OCC inner `gather` held the Market lane for 2×5s > 8s last_good; UL starved at 38/50; a yield that skipped expired OCC suppressed bids | **Local:** sequential `_one_batch`; `yield_lane_to_equity_batch` between waves; OCC defer only while last exact reusable. 16-tick 5s load (`test_full_monitor_shared_lane.py`): N=38/50 second-half miss ~42% / ~56% (TTL not raised). At 0.45s RTT after-warmup coverage is 0%. Historical replay parity is **not** claimed. | `test_full_monitor_shared_lane.py` · `test_wide_book_followup.py` |
| Frozen UL last restamped as fresh (10/30: last changed on ~7% of ticks; replay `ulq` = tick time) | Owned tick skip_cache-fetches stale unique names; missing last is not `updated`; JSONL keeps `underlying_source` / `underlying_age_ms` / observation clock. Profit Capture UL steps use that age, not the tick timestamp. Monitor may pass UL price `0` when unavailable; PATH_V2 `_positive` treats `<= 0` as missing. | `test_underlying_spot_stamp.py` · `test_owned_tick_cadence_contract.py` · `test_path_v2_exit_units.py` |
| 2s sample throttle dropped PATH_V2 eval ticks | `evaluated=true` (and `force_path_sample`) writes the compact JSONL row | `test_path_v2_exit_units.py` |
| HOLD tick re-evaluated by Trendline/ORB (velocity advanced unnoticed; live ≠ replay) | One evaluate per `(trade_id, monitor_tick_id)`. HOLD stores `out=None`; later loops reuse (`PATH_V2_EVAL_DUP_BLOCKED`). Replay uses `eval_now` / parsed epoch, not quote `timestamp` | `test_path_v2_eval_recorder_align.py` · `test_pc_parity_1103_1104.py` |
| Post-12:30 trendline loop with no in-flight monitor tick | `_monitor_trendline_positions(tick_id=…)` runs only when the unified monitor is `_running`; do not invent a tick id after cutoff | `_monitor_trendline_positions` · unified `_running` |
| Closed-trade `exit_bid` from a different contract | `capture_exit` uses the frozen `entry_option_symbol` path bid; log `PATH_V2_EXIT_BID_MISMATCH` if claimed diverges > 25% | `resolve_frozen_contract_exit_bid` · `test_path_v2_exit_units.py` |
| Age >8000ms still labeled `fresh_batch` (MRVL hole on 2.37.7) | Age wins over `fresh_set`; >8000ms **always** `unavailable` | `modules/underlying_freshness.py` |
| Sync `save_daily_dataset` (~158MB JSON, 6–13s) on the owned-eval thread (`_checkpoint("premium_tick")`) | `_checkpoint` marks dirty + `_schedule_persist`; `po-persist` worker | `PO_PERSIST` · `test_po_persist_off_eval_path.py` |
| PO daily JSON grew to **327 MiB** (uncapped merge of live+shadow paths + duplicate `trades_by_path`); `json.dumps` GIL-stall → Cloud Run TCP :8080 `DEADLINE_EXCEEDED` (Sep 1 ~9:37–9:54) | Append-only JSONL per trade (replay source) · daily JSON metrics-only · cap `PROFIT_OPTIMIZER_MAX_DAILY_JSON_BYTES=32000000` (Shared.env and code default) · over-cap skips GCS daily JSON · skip hydrate >~15 MiB · do not join GCS on owned eval | `test_gcs_session_retention.py` · `PROFIT_OPTIMIZER_MAX_DAILY_JSON_BYTES` |
| Fill enroll started a second full-book `maybe_tick` while holding the lock (Aug 28: 16–106s cycles, `evaluate_starved`, `fill_enroll_error \| name 'sys'`) | Fill enroll **flags** `_0dte_force_monitor_tick` + `request_wake` only | `fill_enroll_flag_only` |
| Empty book left `_0dte_force_monitor_tick` set (30k ticks/s) | Idle `maybe_tick` clears the flag and still pauses ≥0.2s | `test_idle_maybe_tick_clears_force_flag` |
| Trendline fast loop passed whole-second tick ids; same-second second call reused HOLD | `canonical_monitor_tick_id` keeps only `u0dte_<ms>`; remints everything else; Trendline/ORB mint with `now=current_time` | `test_same_second_tick_ids_both_run_eval` |
| Restart rebuild used process-start entry time / reset a live HWM | `_rebuild_path_v2_restore` uses fill clock or first path row; does not replace an existing record; skips rehydrate when live HWM is already above entry | `PATH_V2_RESTORE` · `_stamp_restored_entry_mono` |
| `stealth_restart_gap` rebasing HWM on a starved tick of a live process (SLV 103% → 53%) | Rebase only on **process start / Cloud Run replace** | `modules/stealth_restart_gap.py` |
| PX3 `trend_runner` after PX2 20pp already gone (UBER 67% tick) | `keep_px2` / `px3_through_px2_giveback_floor` | `modules/px3_exit.py` |
| Closed PO OCC on the owned 2s batch stretching exits | `0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0`; winner leftover quoted after evaluate **off** the owned lock | `PO_LEFTOVER_SHADOW` |
| Leftover skipped on slow owned ticks (Sep 2 RESEARCH: shadow median 4.06s, p95 16s, max 153s; `shadow_path_trusted=true` anyway) | Do not reuse `PATH_REFRESH_SKIP` for leftover. **11/6 local:** skip leftover OCC while owned is open (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`) because leftover and owned share `occ_fetch`. Do not observe missing leftover quotes (gap spam). | `quote_shadow_leftover_skipped` · `PO_LEFTOVER_SHADOW` · `test_1106_runtime_inputs.py` |
| PATH_V2 pret from chain mark / `pnl_pct` (11/6 IBIT/SLV/MU false peaks) | `live_path_v2_pret_from_tick`: this-tick bid / entry − 1 only. Missing bid → None → STEP 0 HOLD. Replay uses the same helper. Serving `00625-wwv` still assigned `path_pret = current_pnl_pct`. | `test_1106_runtime_inputs.py` · `Never mark / pnl_pct` |
| Unbounded Telegram flush on the eval spine; timeout retried the same send | Local `ledger_alert_flush.py`: notification-only queue, `alert_event_id`, FIFO 32, max 3 attempts, 8s timeout. Drops never rewrite ledger/fills/GCS. | `notification_only=true` · `flush_notification_batch` |
| Wall-clock dedup dropped legitimate fast ticks | Unified = **tick_id equality only** | `should_skip_duplicate_by_tick_id` |
| `force_refresh=None` vs `set()` conflated → full-chain every tick | Empty set = **zero** hydrate | `resolve_force_refresh_underlyings` |
| Second full-batch refresh on owned miss | Stub `_schedule_open_quote_refresh`; use isolated recovery | `OPEN_POSITION_QUOTE_RECOVERY` |
| OCC dash-padded root poisoning (`QQQ---` / `QBTS--`) | Never replace correct spaced OCC with bad osiKey; fail-closed frozen wire | Jul 22 comments in `exact_contract_option_quotes.py` |
| Ambiguous expiry remap | Refuse repair on multi-expiry ties | `ambiguous_expiry_candidates` |
| Same-day OCC stamp on a Monday/weekly (Nov 2 CSCO/RTX `1002`) | Do not remap a later listed payload onto session today; fill OCC = `selected_expiry`; 1002 skip when the day is memo-empty; repair one listed day | `CHAIN_EXPIRY_NEAREST_FROM_PAYLOAD` · `empty_same_day_use_listed` · `empty_expiry_memo_skip_1002_recovery` |
| Heavy chain repair stretched 2s → 10–20s | Bridge budget ≤ ~55% interval · OCC repair debounce | `_occ_repair_due` · bridge elapsed guard |
| Overlap starved Profit Optimizer | PO replay from last tick cache during overlap | `_deliver_po_samples_from_last_tick` |
| Overlap ticks suppress evaluate quietly | Streak counter; WARNING at ≥5 consecutive (`evaluate_starved`) | `overlapping_suppressed` · `overlap_streak` |
| Main loop slept 30s with opens | Cap sleep at monitor interval when open 0DTE | `MAIN_LOOP_SLEEP` · `test_monitor_cadence_and_reuse.py` |
| Single cliff print latched `mfe_giveback` | Gap-guard defer until confirm ticks / deep giveback | `mfe_giveback_gap_guard_should_defer` (Jul 24) |
| `quote_grade` UnboundLocal on open positions | Assign grade before PO observe | Jun 2 / 00358 |
| Live `get_positions` missing → silent `[]` → false broker-flat | Adapter uses `get_portfolio` / `get_positions`; raise `broker_positions_unavailable` | `test_broker_adapter_get_positions.py` · `BROKER_POSITIONS` |
| Demo `executor.positions` ghosts vs mock truth | `audit_demo_dual_book_drift`; never cold-construct mock for GCS RMW | `test_demo_registry_dual_book.py` · `DEMO_REGISTRY_DRIFT` |
| Fill alert / PO `position_size` pre–Liquidity Guard qty | `sync_payload_to_filled_quantity` / `resolve_true_position_size_usd` | `test_fill_qty_sync_after_risk_clamp.py` |
| Exit stamp wash `entry × 1.25` | Settle live chain STC bid; no `entry_cap` | `test_exit_settlement_alert_integrity.py` |

---

## 13. RESEARCH vs FINAL

| Layer | Parity |
|-------|--------|
| `unified_0dte_position_monitor.py` | Byte-identical |
| `unified_0dte_monitor_registry.py` | Byte-identical |
| `open_position_quote_orchestration.py` | Byte-identical |
| `exact_contract_option_quotes.py` | Byte-identical |
| `dte0_exit_monitor_config.py` | Byte-identical |
| `underlying_freshness.py` · `owned_eval_scheduler.py` · `owned_tick_observe.py` · `option_api_accounting.py` | Keep in sync (Aug 31 monitor contract) |
| `active_option_contract_registry.py` | Byte-identical |
| `broker_adapter.py` / `broker_position_synchronizer.py` | Keep in sync on ownership changes |
| Env organization | Both apps: knobs in `Shared.env` (+ `ORB0DTE.env` mirrors). Pacing in `Data.env`. |
| Telegram banner | RESEARCH may prefix desk banner; FINAL strips research desk banner on send |

This guide is the single architecture source of truth; keep both trees’ docs in sync when the monitor stack changes.

---

## 14. Verify checklist (ops)

### Shared (Demo or Live)

1. With ≥1 open 0DTE position, grep `MAIN_LOOP_SLEEP` — `sleep_sec` ≤ `0dte_monitor_interval_sec`.  
2. Grep `UNIFIED_0DTE_MONITOR` — unique `u0dte_` `tick_id`s ~ every 2s; `action=loop_started` / `ul_refresh_started`; no duplicate `maybe_tick` from legacy wrappers. New fill: `force_tick` + `PATH_V2_RESTORE` after restart.  
3. Healthy tick: `premium_source` / quote path shows **exact** · `full_chain_requests=0` when exact-complete (`ORB_0DTE_MONITOR_CHAIN_HYDRATE_SKIPPED`).  
4. New fill: `OPTION_POSITION_REGISTERED` → `OPTION_POSITION_MONITOR_START` within grace (no `0DTE_POSITION_NOT_MONITORED`).  
5. While open: Trendline/Breakout watch logs show deferral / skip under open-quote priority.  
6. Exit: latch → close_confirmed → path-correct Telegram (🔮 ORB / 📏 Trendline / ⛓️ Breakout) once per trade_id.  
7. Overlap: no sustained `evaluate_starved=true` (investigate heavy ticks if WARNING streak).  
8. Run: `tests/test_unified_monitor_p0_corrections.py` · `tests/test_monitor_cadence_and_reuse.py` · `tests/test_main_loop_0dte_monitor_cadence.py` · `tests/test_true_to_live_exit_monitor.py` · `tests/test_owned_tick_cadence_contract.py` · `tests/test_po_persist_off_eval_path.py` · `tests/test_path_v2_eval_recorder_align.py`.
8b. Grep `POSITION_EVAL_MS` — `po_observe_ms` / `po_enqueue_ms` only; no GCS wall time. Grep `PO_PERSIST_QUEUE` lag vs `PROFIT_OPTIMIZER_PERSIST_LAG_WARN_SECONDS` (default **90s**).
8c. Compact JSONL eval rows include `evaluated=true`, `monitor_tick_id`, `eval_now` / `eval_age_s`, and `prem_vel_10`. **Local new tapes** also carry `path_v2_peak` / `path_pret` / `path_v2_reason`, `quote_age_ms`, `contracts`, and round-trip fees. Grep `PATH_V2_EVAL_DUP_BLOCKED` on same-tick Trendline/ORB reuse. Close logs: `PATH_V2_EXIT_BID_MISMATCH` should be absent (or investigated).

### Demo-specific

9. Grep `DEMO_REGISTRY_DRIFT` — should be absent (or only transient during purge).  
10. Open roster comes from mock (`D-…`); no cold `MockOptionsExecutor` construct in close path (`DEMO_OPTIONS_LEDGER | missing_mock_executor` must not appear for real closes).  
11. Fill alert contracts ≡ mock qty ≡ PO `contracts` / true `position_size`.  
12. Run: `tests/test_demo_registry_dual_book.py` · `tests/test_fill_qty_sync_after_risk_clamp.py`.

### Live-specific

13. Grep `BROKER_POSITION_SYNC` — `pass=true` or explicit `sync_unavailable` (never silent empty portfolio).  
14. Grep `BROKER_POSITIONS` — portfolio fetch/normalize succeeding.  
15. Close: `reconcile_confirmed_close` → later `broker_flat` remove (local must not vanish while broker still long).  
16. Run: `tests/test_broker_adapter_get_positions.py` · `tests/test_broker_position_synchronizer.py` · `tests/test_forced_mismatch_certification.py`.

---

## Related documentation (quick)

| Guide | Boundary |
|-------|----------|
| **This file** | Cadence · Demo/Live ownership · registry · exact OCC · request priority · evaluate/close spine · module catalog |
| [0DTEProfitCapture.md](0DTEProfitCapture.md) | Exit reasons · ladders · bid economics · path profiles |
| [Brokers.md](Brokers.md) | Adapter interface · `get_positions` contract · multi-broker stubs |
| [0DTEORB1Breakout.md](0DTEORB1Breakout.md) / [0DTETrendline.md](0DTETrendline.md) / [0DTEORB2Breakout.md](0DTEORB2Breakout.md) | Entry path specifics |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System-wide module map |
| [PriorityOptimizer.md](PriorityOptimizer.md) | Handoff / fill / mid-trade / exit snapshots. Persist slices session tech to the event; executed copies the freeze |
| [profit_optimizer/README.md](../profit_optimizer/README.md) | In-trade flight JSONL (current hub + UL provenance). Not the PO snapshot writer |
| [Alerts.md](Alerts.md) | Telegram method catalog |
| [Configs.md](Configs.md) | Full env index |
| [LiveModeReadiness.md](../LiveModeReadiness.md) | Live ownership / cutover (RESEARCH tree) |

---

## Broker Data Budget: Open Positions + Discovery

When any 0DTE position is open, exact OCC quotes remain the **P0** broker-data lane. The unified monitor publishes `_0dte_open_exact_quote_health` after each tick with open count, owned missing count, recovery attempts, deferred recovery count, quote latency, and batch count.

**Priority while any 0DTE position is open:**

1. **P0** — Open-position exact OCC (~2s) + owned miss recovery  
2. **P1** — Priority Optimizer **entry and exit** snapshots (must succeed; never defer heavy enrich)  
3. **P2** — Closed shadow OCC **off** the owned batch (`0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0`)  
4. **P3** — Discovery equity: **next 24 of `0dte_list` every 10s**, always advance. Exit-quote latency does not cancel this scan or the break check.  
5. **P3b** — Option-chain lanes (chain sampler, held path chain-skew). The sampler caps to one position chain when the exit-quote median exceeds **4000ms**. Path-chain refresh skips when the last monitor cycle exceeds **8000ms**.  
6. **P4** — Everything else (universe rebuild, ad-hoc chain, non-entry/exit hydrate)

Trendline and Breakout discovery no longer need to stop simply because positions are open. They share **one** `get_batch_quotes` for the next **24** list names every **10s** (cursor always advances; status/rank does not pin slots). A slow open-quote batch does not cancel that rotation. Owned OCC stays the quote priority. The remaining lag on a busy book is Priority Optimizer snapshot hydrate (time-correct keys), not the 10s scan.

Default open-pressure behavior:

| Lane | Behavior while 0DTE positions are open |
| --- | --- |
| Open-position exact OCC | Always first; one deduped exact batch per monitor tick |
| Owned miss recovery | Isolated single-contract retries, bounded per tick |
| PO entry/exit snapshots | **Must succeed** — `force=True` but **cache-first** (session tech sliced to the event + discovery quotes + Layer C TTL). Executed copies the sealed handoff (no fill-time hub restamp, no second chain). Mid-trade `age_120s` / `first_red` stay cache-only. Live broker only on a miss the family allows. While open: chain `prefer_cache_only` so exact OCC keeps P0 |
| Profit Optimizer shadow | **0** closed OCC on the owned 2s batch; winners-only leftover off-batch (`0DTE_MAX_SHADOW_LEFTOVER_PER_TICK=0` = every winner · sample 2s) |
| Trendline + Breakout discovery | Next 24 of `0dte_list` every 10s; always rotate. A slow quote batch does not cancel it. |
| Mid-session heavy PO enrich (non entry/exit) | Deferred unless `0DTE_PO_HEAVY_ENRICH_WHILE_OPEN=true` |

Relevant knobs:

```env
0DTE_OPEN_QUOTE_RECOVERY_MAX_ATTEMPTS_PER_TICK=2
0DTE_OPEN_QUOTE_RECOVERY_MAX_WALL_SEC=0.9
0DTE_DISCOVERY_MAX_OPEN_QUOTE_LATENCY_MS=4000
0DTE_DISCOVERY_SYMBOLS_PER_TICK=24
0DTE_DISCOVERY_INTERVAL_SEC=10
0DTE_DISCOVERY_MAX_QUOTES_WHILE_OPEN=24
0DTE_PATH_REFRESH_SKIP_CYCLE_MS=8000
0DTE_PO_HEAVY_ENRICH_WHILE_OPEN=false
0DTE_BROKER_LANE_COUNTER_LOG_EVERY=50
```

Pinned in `configs/Shared.env` §2b (both apps). Lane counters emit `BROKER_LANE_COUNTERS` logs for: `open_exact_occ`, `equity_batch`, `owned_recovery`, `trendline_watch_equity`, `breakout_watch_equity`, `trendline_build`, `option_chain_pc_oi`, `po_snapshot_chain`.

This keeps exit monitoring on the 2s cadence while still allowing high-probability Trendline/Breakout candidates to continue receiving fresh broker data. A filled entry writes the sealed handoff as the executed snap (no second chain). Mid-trade and exit snaps restamp from cache at their own event clocks.

---

## Path / shadow field fill (Oct 27 2026)

Unified monitor + Profit Optimizer share the **effective** monitor clock (2s; 3s at ≥20 opens). Every PATH_V2 eval on that clock writes JSONL (`evaluated=true`). Closed shadow OCC is **not** on the owned batch. Winning leftover is quoted after evaluate, off the owned lock. RSI/PVE/BB are **1m bar** overlays — mine bar-close events. Full key list: [Data.md §7](Data.md#7-hold--monitor--path-refresh) · [profit_optimizer/README.md](../profit_optimizer/README.md#owned-tick-stamp-open-position-review-keys).

| Field group | Live open path | Shadow leftover | Notes |
|-------------|----------------|-----------------|-------|
| `evaluated` · `monitor_tick_id` · `eval_now` · `eval_age_s` · `prem_vel_10` | yes on PATH_V2 eval | same when evaluated | Copied from the engine; replay uses `eval_now` / `eval_age_s` |
| `path_v2_peak` · `path_pret` · `path_v2_reason` | **local new recordings** | same when evaluated | Live engine state on this tick. Historical jsonl before this pack is empty — do not infer degraded-tick peak from those tapes |
| `quote_age_ms` · `contracts` · `quantity` · `opening_fees` · `closing_fees` · `round_trip_fees` · `entry_premium` | **local new recordings** | same when evaluated | `stamp_eval_record_fields`. Fees are modeled $0.65 open + $0.65 close. `actual_exit_*` only when a fill is already on metadata |
| bid / ask / mark / last / age / trust / exit-grade | yes | yes | Exact Market Quote |
| HWM / drawdown / mfe_giveback / seconds since entry | yes | yes | Compact stamps |
| `cumulative_volume` · bid/ask size · `option_l1_imbalance` | yes when broker sends them | same | Missing stays null |
| delta / gamma / theta / iv | yes when last chain refresh has them | inherited | Sparse; do **not** full-hydrate every tick |
| RSI / MACD / VWAP / ATR / `volume_1m` / PVE3 / `volume_3m` / `volume_ratio` / BB / `distance_from_vwap_atr` | yes (1m hub) | same overlay | `stamp_live_rsi` — no extra quote |
| `underlying_price` · `underlying_source` · `underlying_age_ms` · `underlying_observation_timestamp` · `underlying_stale` | yes (skip_cache last this tick) | leftover skip_cache when stale | Broker `date_time_utc` on the UL observation fields. Path `timestamp` is the monitor/write clock. Replay uses `eval_now`. `unavailable` / age >8s is not a live last. |
| `otm_pct` · `call_share` / `put_share` · tape `dir_pct_change_5m` · `underlying_l1_imbalance` | yes when inputs exist | same | `stamp_owned_monitor_review_keys` — strike/tape/L1 ring |
| `spy_change_pct` · `vega` · `break_distance_pct` · chain OI | no | no | Would need a new fetch or are entry-static |
