# Profit Capture — PC V2 (operating guide)

The open this guide exits was filled by [0.OrderExecution.md](0.OrderExecution.md). Entry is the ask on the Trade Profile strike (Demo sim, or the Live broker fill). Exits below are sell-to-close at the bid.

Dated status notes, historical replay / parity / V3A / SF4 / R2X / CF / exit-usage, the old PATH_V2 guide, and the long changelog live in [history/0DTEProfitCapture_history.md](history/0DTEProfitCapture_history.md) (verbatim copy of the pre-streamline file).

## Current system (11/04; Demo `00624-8bn`)

**Profit Capture PC V2** is one flat engine: `evaluate_path_v2_tick` in `modules/path_v2_exit.py` plus `configs/Shared.env` §2b. STATE S0–S10 then STEP 0–30 in evaluation order. 20% Save (STEP 2) is **on**. Contract Fade (STEP 15) stays in the code and **off**. Theta (STEP 14), hybrid trails (STEP 10–11, in development) and premium trails (STEP 20–21) stay in the code and **off**.

- **Scope:** 0DTE singles (ORB, trendline, breakout), CALL and PUT. Spreads do not use this engine. State: `option_stealth["path_v2"]`.
- **Mark:** `pret = bid / entry − 1` (executable bid). No fresh quote → last valid / modeled premium until `no_data` at 90s.
- **Settlement:** fee-inclusive, same number Demo and Live book. Dollars: `(exit − entry) × contracts × 100 − contracts × $1.30`. Fees $0.65/contract open and close. Percent: settlement / (premium + opening fees). A flat bid is a fee loss. Bid return `(exit − entry) / entry` is labeled bid move only.
- **Replay clock:** Live evaluates with tick-start `now`. JSONL `timestamp` is the monitor/write clock. Replay passes `eval_now` / `eval_age_s`; `prep_replay_ticks` sorts by parsed epoch then `monitor_tick_id`, keeps `evaluated=true` when that flag exists, and falls back to `timestamp` on historical dumps. PT is `America/Los_Angeles`. Session hours: `DISCOVERY_ORB2_TL_END_PT` (12:30) and `EOD_FLATTEN_START_PT` (12:50) from `session_clock`. Missing UL fields are `ul_source=unavailable` (S1/S2 stay None). Session tech (`atr`, `vwap_distance_pct`, `session_tech_bar_at`) is overlaid before each evaluate and the last overlay is kept between ticks.
- **Current books (8/19–11/6, accepted harness, 0 per-trade diffs):** TP S0_maxnet `a449ab0d` **542 / 436W/106L / +$166,360.74 / −$6,960.35 / net +$159,400.39**. Total **7,996 / −$365,395.21**. IWM-285p-777: `hwm_rtc` fee-inclusive **+$987.30**.

### Overview

- **Scope:** 0DTE singles (ORB, trendline, and breakout), CALL and PUT. Spreads do not use this engine. The engine is called from `modules/prime_options_stealth_trailing_tp.py`, and its state lives in `option_stealth["path_v2"]`.
- **Mark:** `pret = bid / entry − 1`, the executable bid. A tick with no fresh quote uses the last valid price, or a modeled premium, until `no_data` at 90s.
- **One system, one engine:** Profit Capture PC V2 is a single flat function, `evaluate_path_v2_tick`, read top to bottom. Every feature is a **STEP of this engine with its own setting**, not a nested engine or a second exit path. Replay as **"Profit Capture (current Shared.env)"**.
  - **STATE S0–S10** runs on every tick and never exits.
  - **STEP 0–30** is checked in order; the first exit returns. No lettered sub-steps. One header per STEP, except **STEP 12** (two exits under one header: spike fail then ULMFE). Registry: `PATH_V2_ENGINE_EXITS` in `easy0DTE/modules/options_exit_reason_catalog.py` (**32 rows, 31 exit ids, 36 details** + 3 monitor/EOD = 34 live ids). STEP 0 and 16 never exit.
  - A **HOLD** does not return. The 120s hold skips STEP 17–25; each peak region skips the other regions. STEP 26–30 still run on a held tick (29–30 only past the shared 120s checkpoint and with no R2X).
  - **Exception, R2X hold mode (STEP 1):** once R2X has taken over a runner, STEP 1 returns on every tick (exit or HOLD), so STEP 2–30 do not run for that trade.
  - **Numbering (10/26, then 10/27 append):** integer sequence only. Old → new (10/26): 0–1 = 0–1, 1A → 2, 2–7 → 3–8, **7A → 9**, 8–16 → 10–18, 17A → 19, 17–25 → 20–28; STATE S1A → S2, S2–S8 → S3–S9. 10/27 appended STEP 29–30 and STATE S10. Dated notes under Historical keep the numbers of their date.
  - Every block in the code has the same header: `STEP n | Feature | Exit Reason / detail`, then `Knobs` and `Rule`.
- **What changed on 10/6:**
  - The nested engine (V3A, sha `261df582…ae47`) was rewritten flat. Decisions are unchanged (see the parity proof).
  - Later the same day: the Stale-fail 4 thresholds (Shared.env only) and the Runner Extension (R2X, new STEP 1, own kill switch).
- **What changed on 10/11:**
  - STEP 24 keeps the normal `gb30_giveback` 24pp sale, but a mined snapshot gate extends clean runner candidates: when `ul_dd <= 0.0009` and `0.05 <= pret <= 0.10`, the extension latches and the 24pp touch is held until 38pp giveback or a later Profit Capture exit.
  - The gate is controlled by `OPTION_PATH_V2_GB30_EXTENSION_*` knobs in `Shared.env` and can be disabled without changing the base 24pp GB30 behavior.
- **What changed on 10/17:** STEP 12 adds `underlying_mfe_capture` after spike protection and before stale-high exits. It requires a fresh adverse UL retrace and only operates in the 175-250% MFE band while the contract remains at least +50%. Its own kill switch is `OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_ENABLED`.
- **What changed later on 10/17:** STEP 19 adds `bounded_mfe_capture`, a replay-selected 290-305% peak band after 10:45 PT. It sells only after a 10pp giveback while the bid remains at least +100%. It is inactive at and after 12:30 PT, leaving the mandatory `mfe_trail` as the afternoon owner. Its own kill switch is `OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_ENABLED`.
- **What changed on 10/20:** STEP 2 added `twenty_percent_save` / `PVE_20P_SAVE` (PVE3 first-touch latch). Turned **off** on 10/22. Replaced on 11/03 (below).
- **What changed on 10/22:** STEP 9 adds `guarded_retrace_exit` / `GBUL_RETRACE_05_65` and `ul_giveback_ratio_exit` / `UFR_GIVEBACK_30_70` for 30% up to 200% MFE winners, after the runner steps and the mandatory exits and before the giveback and stale exits they pre-empt. A new STATE S2 tracks its own favorable UL (first valid price anchor, retrace against the extreme). Each rule has a kill switch (`OPTION_PATH_V2_GBUL_ENABLED`, `OPTION_PATH_V2_UFR_ENABLED`); both are `true` in Shared.env, so **STEP 9 is on for future deployments**. Also on 10/22: the PVE3 20% Save was turned **off** and its catalog row moved to the disabled tier; the 20p snapshot stays decoupled from Save.
- **What changed on 11/03:** STEP 2 is two flat details, same reason `twenty_percent_save`: stall save `P20_STALL_SAVE` then UL-retrace `P20_UL_RETRACE_SAVE`. Shared.env `OPTION_PATH_V2_20P_SAVE_ENABLED=true` and `OPTION_PATH_V2_20P_SAVE_ULR_ENABLED=true` (dataclass both false). Entry VWAP distance is frozen once onto osnap (`freeze_entry_distance_from_vwap_atr`); missing field never arms ULR. `PVE_20P_SAVE` still normalizes on old logs. See **20% Save knobs (11/03)**.
- **What changed on 11/04 (streamline, book-neutral):** `replay_ticks` matches the live evaluate call (theta, bid/ask, `valid_bid_ask_contracts`, session-tech overlay, `mfe_trail_*=None`). Missing UL is `unavailable` (never fresh). 12:30 / 12:50 come from `session_clock` (`DISCOVERY_ORB2_TL_END_PT`, `EOD_FLATTEN_START_PT`). Dead PVE3 latch, inert ULR knobs (ATR_K / ARM_S / hour-ET / symbols), and `path_v2_exit_nested_historical.py` are gone. Post-evaluate 20p / persist / shadow are try/except so count + JSONL always write.  ULR treats UL `<= 0` / `None` as unavailable and runs only when both `20P_SAVE_ENABLED` and `20P_SAVE_ULR_ENABLED` are true. Recorder stores every evaluation (`eval_now`, `eval_age_s`, `monitor_tick_id`, `prem_vel_10`). HOLD stores `out=None` so later loops reuse the same tick (`PATH_V2_EVAL_DUP_BLOCKED`). `PATH_V2_EVAL_COUNT` increments after evaluate, before the JSONL write. Exit bid is the frozen contract. Replay uses `eval_now` / `eval_age_s` (historical fallback: `timestamp`), sorts by parsed epoch, and `ZoneInfo("America/Los_Angeles")`, and can stop at the live sale. Sparse-velocity was removed.
- **What changed on 11/05 (local until next Demo deploy):** PATH_V2 `pret` is this tick's exact bid / entry − 1 (not a prior `osnap` bid, even when PE marks the tick degraded). Evaluate-path 20% snapshot does not wait 3s for hub (`allow_sync_refresh=False`). UL loop leaves a slow/hung batch running (`ul_refresh_slow` / `ul_refresh_hung`, cancel=false) and starts the next wave as soon as inflight completes. OCC apply-on-complete; after the 1.5s slow window evaluate last reusable exact bids without cancelling the worker. Open exact batch size is 25. Cap 25 cannot hold 38/50 names under the 8s last_good cutoff at a 5s RTT — TTL unchanged. Serving `00624-8bn` still 4s-cancels UL and 1.5s-cancels OCC. No exit id or threshold change. Rollback of the next ship is `00624-8bn`.
- **What did not change:** every earlier exit id and every exit threshold. Trade Profile JSON bounds are admit, not this engine. 11/04 added `pacific_clock_hour`, `resolve_frozen_contract_exit_bid`, and `modules/path_v2_replay.py`. The evaluate / load / architecture API is otherwise the same. Live cadence (dedicated `maybe_tick` loop that always pauses, 2s UL cache, 5s JSONL / 45s daily persist; on `00624-8bn`) changes **future** tick density and UL age — replay of already-saved rows still uses stored `eval_now` / UL age and does not re-score the historical book. STEP 2 Save is **on** Demo (`ENABLED=true`, `SIDES=call`).

### Replay clock

Live PATH_V2 evaluates with the tick-start `now`. The JSONL `timestamp` is the monitor/write clock and can differ. Replay must pass `eval_now` into `evaluate_path_v2_tick` and use saved `eval_age_s` as the age the engine used. `prep_replay_ticks` parses `eval_now` (or `timestamp` / `now`) to a datetime, sorts by epoch then `monitor_tick_id`, keeps only `evaluated=true` rows when that flag exists, and falls back to `timestamp` on historical dumps that lack `eval_now` so N100 / Total stay put. `stop_at_live_exit` includes the deciding tick (`eval_now` ≤ live exit) and tags later ticks `SHADOW_CONTINUATION`. The exit row stores `deciding_monitor_tick_id` and `deciding_eval_now`.
- **Settlement:** fee-inclusive. `(exit − entry) × contracts × 100 − contracts × $1.30`.

### State block (every tick, no exit)

| State | Feature | What it keeps | Knobs (default) |
|---|---|---|---|
| S0 | Tick inputs | age = seconds since open; `pret`; afternoon-trail trigger and giveback passed in by the caller. R2X: `runner_ext_ticks` counts every call on the position (also ticks with no mark). | — |
| S1 | Favorable underlying (UL) | Favorable UL return, its running max, drawdown, and retrace. All three are None when the UL quote is older than 10s, `underlying_source=unavailable`, or the price is missing / `<= 0` (`_positive`). Age is the monitor broker/receive clock (`underlying_age_ms`), not the tick timestamp. | `OPTION_PATH_V2_STALE_MAX_SECONDS` (10) |
| S2 | UL retrace tracker for STEP 9 (first-UL anchor, 10/22) | `ulr_u0` (anchor = first valid UL price on the position, not `entry_underlying`), `ulr_umax` (running max favorable move off the anchor, starts at 0), `ulr_ext` (favorable-price extreme), `ulr_dd` = (extreme − fav) / abs(extreme), `ulr_retr` = (umax − move) / umax once umax > 0.05%. `ulr_armed` is set only on the very first call for a position. Hold (dd / retr None, count resets, nothing stored): UL missing, `unavailable`, or older than 10s; not armed (restart, re-enroll, or a snapshot that predates this state); anchor, max or extreme missing or invalid. It never re-anchors mid-trade. Runs only while GBUL or UFR is on. | `GBUL_ENABLED` / `UFR_ENABLED` (true), `STALE_MAX_SECONDS` (10) |
| S3 | Premium peak and velocity samples | Peak bid return and the time of the high. A new high at least +5pp above the old one also resets the material clock and the `hwm_rtc` velocity timer. Keeps the last 10s of pret samples. A >10s gap or one in-window sample leaves **measured** velocity None (STEP 17 cannot fire); RTC / telemetry `prem_vel_10` is 0 (does not meet ≤ −0.0005). Replay must use `eval_now` so live 10s windows stay 10s. R2X: `runner_ext_prev_pret` = the previous tick's pret (stored before this tick is added). | `MATERIAL_HWM_PP` (0.05), `RTC_VEL_WINDOW` (10) |
| S4 | Contract Fade quote memory | Bid and ask change against the nearest quote at least 30s earlier | `CONTRACT_FADE_QUOTE_WINDOW_SECONDS` (30) |
| S5 | V3A shallow-floor bookkeeping | Ticks printed at the running peak bid (`sf_peak_ticks`), and whether the bid was above −18% at or after age 120s (fresh cross) | `SHALLOW_FLOOR_STOP_ENABLED` (true), `SHALLOW_FLOOR_PRET` (−0.18), `SHALLOW_FLOOR_MIN_AGE_SECONDS` (120) |
| S6 | Running MFE, proof, runner arm | Running MFE = peak (0 before the first pret). `proven` at ≥ 50%. Stamps the first tick the peak reaches +350%. | `PROOF` (0.50), `RUNNER_CAPTURE_ENABLED` (true), `RUNNER_CAPTURE_ARM_MFE` (3.50) |
| S7 | Clocks | Seconds since the premium high; runner arm age | — |
| S8 | V3A runner timing | Timed-capture arm age is 30s (base 60s). Profit-floor wait is 900s while bid ≥ +50%, else the base 600s. | `RUNNER_TIMING_V3A_ENABLED` (true), `_TIMED_MIN_ARM_AGE_SECONDS` (30), `_FLOOR_GREEN_MIN_AGE_SECONDS` (900), `_FLOOR_GREEN_MIN_PRET` (0.50); base `RUNNER_CAPTURE_TIMED_MIN_ARM_AGE_SECONDS` (60), `RUNNER_CAPTURE_FLOOR_MIN_AGE_SECONDS` (600) |
| S9 | V3A stall-floor bookkeeping | Eligible = 15–40% peak, age 60–3600s, and the high ≥ 1500s old. Counts consecutive ticks with bid ≤ +4%. Arms once the bid has held above +4% for 30s while eligible. Runs on every tick, exit or not. | `STALL_FLOOR_STOP_ENABLED` (true), `_MIN_MFE` (0.15), `_MAX_MFE` (0.40), `_MIN_AGE_SECONDS` (60), `_MAX_AGE_SECONDS` (3600), `_MIN_SEC_HWM` (1500), `_FLOOR` (0.04), `_ARM_ABOVE_SECONDS` (30) |
| S10 | Confirmed premium peak for STEP 29–30 (10/27) | `tv_confirmed_peak` = highest min(bid pret, previous tick bid pret), so a one-tick print does not count; `tv_prev_pret`. Runs on every priced tick while either switch is on. | `ATR_LOSS_CUT_ENABLED` (true), `VWAP_CROSS_EXIT_ENABLED` (true) |

All knob names are `OPTION_PATH_V2_*` in `configs/Shared.env` §2b. The prefix is left out above.

### Steps in evaluation order

| Step | Feature | Exit Reason id / detail | Rule | Knobs and defaults | Fill / routing |
|---|---|---|---|---|---|
| 0 | No executable mark | none (hold) | No pret: no peak update, no exit | — | — |
| 1 | **Runner Extension (R2X)**, hold mode | `runner_extended_capture` / `RUNNER_EXT_TARGET`, `RUNNER_EXT_ULREV`; `runner_extension_floor` / `RUNNER_EXT_FLOOR`; hold details `RUNNER_EXT_START` (STEP 7) and `RUNNER_EXT_HOLD` | Only after STEP 7 handed the runner over (L0 = bid return at the timed-capture touch). First of: bid < +50% → floor; bid ≥ 1.25 × L0 → target; bid ≥ 1.045 × L0 and UL retrace ≥ 0.04 (fresh UL) → UL reversal; else HOLD. Owns the exit: STEP 2–30 are skipped on every tick while it holds (exit or HOLD). | `RUNNER_EXTENSION_ENABLED` (true; dataclass default false), `_TARGET_MULT` (1.25), `_UL_RETRACE` (0.04), `_UL_MIN_MULT` (1.045), `_FLOOR_PRET` (0.50) | Passive limit |
| 2 | **20% Save** (Nov 3 2026 replacement: +20% stall save + UL-retrace ext) | `twenty_percent_save` / `P20_STALL_SAVE`, `P20_UL_RETRACE_SAVE` | **ON** (`20P_SAVE_ENABLED=true`). Stall save latches on the first tick the executable bid return reaches exactly +20% (`MIN_MFE`, inclusive) and records `t20` = seconds from open to that touch, the peak, the peak time, ticks since touch `n` and new-high ticks `nh` (`state["save20_stall"]`). Sells a **CALL** (`SIDES=call`) at the current executable bid when all hold: `t20` ≥ 90s, peak < +50%, ≥ 60s since the touch, ≥ 60s since the peak, peak − pret ≥ 15pp, pret ≥ +11%, and `nh/n` ≤ 0.05. After the stall check, UL-retrace runs only when **both** `20P_SAVE_ENABLED` and `ULR_ENABLED` are true. It arms at the same +20% touch when \|`entry_distance_from_vwap_atr`\| < 0.50 and the entry→touch UL move is favorable, then sells when a fresh UL gives back ≥ 90% of that move (`P20_UL_RETRACE_SAVE`). Missing entry VWAP distance never arms. UL `<= 0` or `None` is unavailable (no arm, no fire). Premium path for the stall; UL quote for the extension. Same-tick result as evaluating it after STEP 30 (any later exit on that tick sells at the same bid). `SHADOW_ONLY=true` tracks without exiting. *(Replaced the 10/20–10/22 PVE3 first-touch latch `PVE_20P_SAVE`, which cost Total −$4,713 / TP −$1,356 on the 8/19–11/02 books.)* | `20P_SAVE_ENABLED` (**true**, kill switch), `_MIN_MFE` (0.20), `_SIDES` (call), `_MIN_T20_S` (90), `_MAX_PEAK` (0.50), `_MIN_SINCE_TOUCH_S` (60), `_MIN_SEC_HWM` (60), `_GIVEBACK` (0.15), `_MIN_PRET` (0.11), `_MAX_NEWHIGH_FRAC` (0.05), `_ULR_ENABLED` (true; dataclass false), `_ULR_FRAC` (0.90), `_ULR_MAX_ENTRY_VWAP_ATR` (0.50), `_SHADOW_ONLY` (false) | Marketable limit at current executable bid |
| 3 | Shallow proof stop | `shallow_proof_stop` / `SHALLOW_PROOF_10_20` | Age ≥ 60s, peak 10% up to 20%, bid ≤ −30%. Beats the 120s hold. | `SHALLOW_PROOF_STOP_ENABLED` (true), `_MIN_AGE_SECONDS` (60), `_MIN_MFE` (0.10), `_MAX_MFE` (0.20), `_PRET` (−0.30) | Moderate aggressive limit |
| 4 | Catastrophic premium stop (mandatory) | `catastrophic_premium_stop` / `CAT_PREM_STOP` | Bid ≤ −80%, age ≥ 60s | `CAT_FLOOR` (−0.80), `CAT_MIN_AGE_SECONDS` (60) | Urgent market |
| 5 | Afternoon MFE trail (mandatory) | `mfe_trail` / `MFE_TRAIL_1230` | After `DISCOVERY_ORB2_TL_END_PT` (12:30 PT) via `session_clock` / `orb2_tl_end_hour` (live and replay; not UTC−7). Peak ≥ 10%, giveback ≥ 10pp. Trigger/exit from `cfg` (`OPTION_SINGLE_MFE_TRAIL_*`); live and replay pass `None`. | `OPTION_SINGLE_MFE_TRAIL_TRIGGER` (0.10), `OPTION_SINGLE_MFE_TRAIL_EXIT` (0.10) | Passive limit |
| 6 | Absorption exit (mandatory) | `absorption_exit` / `ABSORPTION_EXIT` | Age ≥ 450s, peak / age < 0.00005, bid ≥ +2% | `ABSORPTION_EXIT_ENABLED` (true), `_MIN_AGE_SECONDS` (450), `_MAX_SPEED` (0.00005), `_MIN_PRET` (0.02) | Passive limit |
| 7 | 350% runner timed capture | `runner_timed_capture` / `RUNNER_TIMED_CAPTURE_350`; `RUNNER_TIMED_CAPTURE_350_A30` when only the V3A 30s arm allowed it | Armed ≥ 30s (S8), peak ≥ 350%, bid ≥ +400% and ≥ 50% of peak. **R2X on:** if ≥ 2 ticks came before this one and the previous tick's bid return ≥ 0.5 × this one (gap guard), it does not sell: it stores L0 and returns HOLD `RUNNER_EXT_START`, and STEP 1 owns the exit from the next tick. Guard fails → sells here as before. | `RUNNER_CAPTURE_ENABLED` (true), `_ARM_MFE` (3.50), `_TIMED_MIN_CURRENT_RETURN` (4.00), `_TIMED_MIN_PEAK_RATIO` (0.50) + S8; R2X entry `RUNNER_EXTENSION_MIN_TICKS` (2), `_JUMP_GUARD` (0.5) | Passive limit |
| 8 | 350% runner profit floor | `runner_profit_floor` / `RUNNER_PROFIT_FLOOR_350`; `RUNNER_PROFIT_FLOOR_350_G900` when the V3A 900s green wait applied | Peak ≥ 350%, age ≥ the S8 wait (900s while bid ≥ +50%, else 600s), bid ≤ 80% of peak | `RUNNER_CAPTURE_FLOOR_PEAK_RATIO` (0.80) + S8 | Passive limit |
| 9 | **UL retrace capture (GBUL + UFR)** (10/22) | `guarded_retrace_exit` / `GBUL_RETRACE_05_65`; `ul_giveback_ratio_exit` / `UFR_GIVEBACK_30_70` | Peak 30% up to (not including) 200%; fresh UL (S2, else HOLD and the 2-tick count resets); each rule on 2 consecutive ticks. **GBUL:** favorable UL ≥ 0.5% off its extreme and bid ≥ 0.65 × peak. **UFR:** favorable UL moved ≥ 0.5% off the anchor and gave back ≥ 30% of that move, bid ≥ 0.70 × peak and ≥ +3%. GBUL wins a same-tick tie. The 350% runner path is untouched. | `GBUL_ENABLED` (true; dataclass default false), `GBUL_MIN_MFE` (0.30), `_MAX_MFE` (2.00), `_MIN_UL_DD` (0.005), `_MIN_PEAK_RATIO` (0.65), `_CONFIRM_TICKS` (2); `UFR_ENABLED` (true; dataclass default false), `UFR_MIN_MFE` (0.30), `_MAX_MFE` (2.00), `_MIN_UL_MFE` (0.005), `_MIN_UL_RETRACE` (0.30), `_MIN_PEAK_RATIO` (0.70), `_MIN_PRET` (0.03), `_CONFIRM_TICKS` (2) | Passive limit |
| 10 | Hybrid premium trail stage 1 (**off**, in development) | `premium_trail_>50%_25trail` / `PREMIUM_TRAIL_GT50_25TRAIL` | Fresh UL; peak ≥ 50%, giveback ≥ 25pp, age ≥ 60s, bid ≥ +20%, UL retrace ≤ 0.50, UL drawdown ≥ 0.001 | `HYBRID_PREMIUM_TRAIL_ENABLED` (false), `PREMIUM_TRAIL_GT50_*` | Moderate aggressive limit |
| 11 | Hybrid premium trail stage 2 (**off**, in development) | `premium_trail_>30%_10trail` / `PREMIUM_TRAIL_GT30_10TRAIL` | Fresh UL; peak ≥ 30%, giveback ≥ 10pp, age ≥ 60s, bid ≥ +5%, UL retrace ≤ 0.75, UL drawdown ≥ 0 | `HYBRID_PREMIUM_TRAIL_ENABLED` (false), `PREMIUM_TRAIL_GT30_*` | Moderate aggressive limit |
| 12 | Premium spike fail | `premium_spike_fail` / `SPIKE_FAIL` | Peak 30–60%, giveback ≥ 15pp, bid ≥ +10%, age ≥ 60s, high ≤ 16s old | `SPIKE_FAIL_ENABLED` (true), `_ARM_MFE` (0.30), `_MAX_MFE` (0.60), `_GIVEBACK` (0.15), `_MIN_PRET` (0.10), `_MIN_AGE_SECONDS` (60), `_MAX_SEC_HWM` (16) | Moderate aggressive limit |
| 12 | Underlying-confirmed MFE capture | `underlying_mfe_capture` / `UL_MFE_CAPTURE_175_250` | Fresh UL; MFE 175% up to 250%, giveback ≥ 25pp, bid ≥ +50%, high ≥ 120s old, favorable-UL retrace ≥ 0.50 and drawdown ≥ 0.003 | `UNDERLYING_MFE_CAPTURE_ENABLED` (true), `UNDERLYING_MFE_CAPTURE_*` | Passive limit |
| 13 | Premium stale high fail (7 shapes) | `premium_stale_fail` / `STALE_HIGH_FAIL` | Any shape fires: **band** 70–90% peak, bid +2–8%, high ≥ 1000s old · **early** 70–90%, giveback ≥ 50pp, bid ≥ +25%, high ≥ 300s old · **upper** 75–82%, bid +2–40%, high ≥ 180s old, UL drawdown ≥ 0.003 · **reversal** 50% up to 70%, bid +2–20%, high ≥ 200s old, UL against the trade, UL retrace ≥ 1.15, UL drawdown ≥ 0.004 · **gap** 90% up to 94%, bid +2–20%, high ≥ 800s old, UL retrace ≥ 0.55 · **high** peak ≥ 100%, bid +2–34%, high ≥ 1800s old · **runner** peak ≥ 150%, bid +2–24%, UL retrace ≥ 0.50, age ≥ 42s. The UL shapes need a fresh UL quote. | `STALE_FAIL_ENABLED` (true), `STALE_FAIL_*`; high-shape refinement (10/18): `STALE_FAIL_HIGH_MAX_PRET` (**0.34**); Stale-fail 4 (10/6): `STALE_FAIL_UPPER_MIN_MFE` (0.75), `STALE_FAIL_RUNNER_MAX_PRET` (0.24), `STALE_FAIL_RUNNER_MIN_AGE_SECONDS` (42) | Moderate aggressive limit |
| 14 | Theta exit (**off**, experimental) | `theta_exit` / `THETA_EXIT` | Never green (peak < 0), age ≥ 300s, theta ≥ −0.50 | `THETA_EXIT_ENABLED` (false), `_MIN_AGE_SECONDS` (300), `_MIN` (−0.50) | Moderate aggressive limit |
| 15 | Contract Fade Exit (**off** since 10/6) | `contract_fade_exit` / `CONTRACT_FADE_EXIT` | Entry chain ≤ 13 valid contracts; peak above 0 and under 20%; ≥ 2pp off the high; high ≥ 15s old; bid and ask both lower than the quote ≥ 30s earlier (S4). Beats the 120s hold. | `CONTRACT_FADE_EXIT_ENABLED` (**false** in Shared.env; dataclass default true), `_MAX_VALID_BID_ASK_CONTRACTS` (13), `_MIN_MFE` (0), `_MAX_MFE` (0.20), `_MIN_DRAWDOWN` (0.02), `_MIN_SEC_HWM` (15) | Passive limit |
| 16 | 120s minimum hold + region split | none (state / hold) | Under 120s: HOLD, skip STEP 17–25. From 120s: compute retrace fraction and 10s velocity. A >10s sample gap leaves measured velocity None (and `prem_vel_10` 0 for RTC). Replay uses `eval_now` so live 10s windows stay 10s. Peak region: immature < 15%, middle 15% up to 50%, mature ≥ 50%. | `MIN_HOLD_SECONDS` (120), `EARLY_CUT` (0.15), `PROOF` (0.50), `RTC_VEL_WINDOW` (10) | — |
| 17 | CALL shallow-green exit | `shallow_green_exit` / `SHALLOW_GREEN_EXIT` | CALL only. Peak 2% up to 20%, high ≥ 90s old, **measured** 10s velocity present and ≤ 0, bid ≥ 0. A >10s gap or one in-window sample leaves measured velocity None, so this step cannot fire. **10/26 SGUL hold:** HOLD while S1 favorable UL since entry ≥ +0.5% and UL giveback from its best ≤ 0.1% (fresh UL only), re-checked every tick | `SHALLOW_GREEN_EXIT_ENABLED` (true), `_MIN_AGE_SECONDS` (120), `_MIN_MFE` (0.02), `_MAX_MFE` (0.20), `_MIN_SEC_HWM` (90), `_MAX_VEL` (0), `_MIN_PRET` (0); `SG_UL_HOLD_ENABLED` (true; dataclass false), `SG_UL_HOLD_MIN_FAV` (0.005), `SG_UL_HOLD_MAX_GIVEBACK` (0.001) | Passive limit |
| 18 | Shallow time stop | `shallow_time_stop` / `SHALLOW_15M_MFE7P5` | Age ≥ 900s and peak still under 7.5% | `SHALLOW_TIME_STOP_ENABLED` (true), `_SECONDS` (900), `_MAX_MFE` (0.075) | Moderate aggressive limit |
| 19 | Bounded high-MFE capture | `bounded_mfe_capture` / `BOUNDED_MFE_CAPTURE_290_305` | 10:45 PT through `DISCOVERY_ORB2_TL_END_PT` (12:30; same `session_clock` hour as STEP 5), peak 290% up to 305%, giveback ≥ 10pp, bid ≥ +100% | `BOUNDED_MFE_CAPTURE_ENABLED` (true), `BOUNDED_MFE_CAPTURE_*` | Passive limit |
| 20 | Early premium trail, 175% guard (**off**) | `premium_trail` / `PATH_V2_EARLY_PREM_TRAIL` | Peak ≥ 175%, bid ≥ +75%, giveback ≥ 5pp | `EARLY_PREM_TRAIL_ENABLED` (false), `_TRIGGER` (1.75), `_EXIT` (0.05), `_MIN_CURRENT` (0.75) | Moderate aggressive limit |
| 21 | Premium trail, 100% guard (**off**) | `premium_trail` / `PATH_V2_PREMIUM_TRAIL` | Peak ≥ 100%, giveback ≥ 30pp, high ≥ 900s old | `PREMIUM_TRAIL_ENABLED` (false), `_TRIGGER` (1.00), `_EXIT` (0.30), `_MIN_SEC_HWM` (900) | Moderate aggressive limit |
| 22 | **V3A shallow floor stop (SF)** | `shallow_floor_stop` / `SHALLOW_FLOOR_5_10_18` | Peak 5% up to 10% that printed on ≥ 2 ticks; age ≥ 120s; bid ≤ −18%; bid was above −18% at or after 120s (S5). Sells at the bid, ahead of `immature_stress`. | `SHALLOW_FLOOR_STOP_ENABLED` (true), `_MIN_MFE` (0.05), `_MAX_MFE` (0.10), `_MIN_AGE_SECONDS` (120), `_PRET` (−0.18), `_MIN_PEAK_TICKS` (2), `_REQUIRE_FRESH_CROSS` (true) | Moderate aggressive limit (explicit moderate token in `execution_routing.py`) |
| 23 | Immature stress + active-expansion warning | `immature_stress` / `IMMATURE_STRESS`; hold detail `AE_WARNING_HOLD` | Immature region (peak < 15%). 23a: live stress < 0.40 → cancel warning, HOLD. 23b: stress ≥ 0.40 → record the first-fire shadow. 23c: a new +2pp high since the warning → cancel, HOLD. 23d: active expansion (peak / age ≥ 0.0001, high ≤ 120s old) and bid ≤ 0 → warning HOLD up to 90s; after 90s with the high ≥ 45s old → EXIT. 23e: otherwise EXIT. | `STRESS_THR` (0.40), `AE_WARNING_ENABLED` (true), `AE_WARNING_WINDOW_SECONDS` (90), `AE_WARNING_STALL_SECONDS` (45), `AE_WARNING_MATERIAL_HWM_PP` (0.02), `AE_WARNING_MAX_START_RETURN` (0.00), `AE_EXP_SPEED_MIN` (0.0001), `AE_SEC_HWM_MAX` (120) | Moderate aggressive limit |
| 24 | 30–50% giveback | `gb30_giveback` / `GB30_24PP`; hold detail `GB30_38PP_EXTENSION_HOLD` | Middle region (peak 15% up to 50%): cancel warning. Peak ≥ 30% and giveback ≥ 24pp → EXIT, except the gated extension latches when `ul_dd <= 0.0009` and bid return is +5% to +10%; once latched, it waits for 38pp giveback or a later exit. Peak 15% up to 30% is a HOLD. | `GB30_ENABLED` (true), `_MIN_MFE` (0.30), `_GIVEBACK` (0.24), `GB30_EXTENSION_*` (on, 0.38 / 0.0009 / 0.05–0.10) | Passive limit |
| 25 | Mature high-water retrace | `hwm_rtc` / `HWM_RTC` | Mature region (peak ≥ 50%): cancel warning. Retrace ≥ 20%, material high ≥ 300s old, and 10s velocity ≤ −0.0005 → EXIT. A >10s gap on the **eval** clock reports velocity 0 (does not meet ≤ −0.0005). IWM-285p-777 live eval ticks were inside 10s; replay must use `eval_now` or it looks like a gap. Retrace < 20% or velocity above −0.0005 clears the velocity timer (HOLD). | `RTC_RETRACE` (0.20), `RTC_MATERIAL_SEC` (300), `RTC_VEL` (−0.0005) | Passive limit |
| 26 | 32–40% timed giveback (package B) | `gb32_timed_giveback` / `GB32_40_TIMED_15PP` | Also on held ticks. Peak 32% up to 40%, giveback ≥ 15pp, high ≥ 120s old, bid ≥ +5%. | `GB32_TIMED_ENABLED` (true), `_MIN_MFE` (0.32), `_MAX_MFE` (0.40), `_GIVEBACK` (0.15), `_MIN_SEC_HWM` (120), `_MIN_PRET` (0.05) | Passive limit |
| 27 | Deep green stop (package B) | `deep_green_stop` / `DEEP_STOP_10_30_65` | Age ≥ 60s, peak 10% up to 30%, bid ≤ −65%. Not held by the 120s hold. | `DEEP_STOP_ENABLED` (true), `_MIN_AGE_SECONDS` (60), `_MIN_MFE` (0.10), `_MAX_MFE` (0.30), `_PRET` (−0.65) | Moderate aggressive limit |
| 28 | **V3A stall floor stop (SBE v2-A)** | `stall_floor_stop` / `STALL_FLOOR_15_40_4` | Eligible and armed (S9), bid ≤ +4% on the 2nd consecutive tick, and bid still ≥ +1% (floor − slip). A deeper print does not fire, and the base exits keep the trade. | `STALL_FLOOR_STOP_ENABLED` (true), `_MAX_SLIP` (0.03), `_CONFIRM_TICKS` (2) + S9 | Moderate aggressive limit (explicit moderate token in `execution_routing.py`) |
| 29 | **ATR loss cut (10/27)** | `atr_loss_cut` / `ATR_LOSS_CUT_30_2ATR` | Only when STEP 0–28 gave no exit and no runner extension is active; past the shared 120s checkpoint and age ≥ 600s; fresh session-tech bar (≤ 180s; missing = no fire). Bid ≤ −30%, S10 confirmed peak < +10%, side-aware (UL − entry UL) / `atr` ≤ −2.0, and side-aware `vwap_distance_pct` < 0. | `ATR_LOSS_CUT_ENABLED` (true; dataclass false), `_MAX_PRET` (−0.30), `_ATR_MULT` (2.0), `_MAX_PEAK` (0.10), `_MIN_AGE_S` (600), `_REQUIRE_VWAP_AGAINST` (true), `_BAR_MAX_AGE_S` (180); `MIN_HOLD_SECONDS` (120, shared) | Moderate aggressive limit (explicit moderate token) |
| 30 | **VWAP cross exit (10/27)** | `vwap_cross_exit` / `VWAP_CROSS_50_025ATR` | Same gates as STEP 29 (no age knob of its own: shared 120s checkpoint). Side-aware `vwap_distance_pct` > 0 on a fresh bar marks "was on the right side". Armed once seen and S10 confirmed peak ≥ +50%. Below = side-aware distance ≤ −(0.25 × `atr` / UL × 100)%. Counts distinct bars (`session_tech_bar_at`) below while armed (reset otherwise); exits at ≥ 1 bar with bid ≥ +10%. | `VWAP_CROSS_EXIT_ENABLED` (true; dataclass false), `_MIN_PEAK` (0.50), `_ATR_BUFFER` (0.25), `_CONFIRM_BARS` (1), `_MIN_PRET` (0.10), `_BAR_MAX_AGE_S` (180); `MIN_HOLD_SECONDS` (120, shared) | Passive midpoint-first (explicit passive token) |
| end | No exit | none (hold) | Detail `AE_WARNING_HOLD` while the warning holds, else empty (R2X holds return `RUNNER_EXT_START` / `RUNNER_EXT_HOLD` from STEP 7 / STEP 1) | — | — |

STEP 12 is one code header with two exits (`premium_spike_fail` then `underlying_mfe_capture`).

### Exit-reason catalog (matches `easy0DTE/modules/options_exit_reason_catalog.py`)

`PATH_V2_ENGINE_EXITS` in the catalog lists every exit the engine can return, one row per exit id and STEP, in STEP order (STEP 1, 9 and 12 have two ids; STEP 2 has two details on one id; STEP 0 and 16 never exit). **Count (11/03): 32 engine rows, 31 distinct engine exit ids, 36 engine details, plus 3 monitor/EOD exits = 34 live ids.** `tests/test_path_v2_exit_catalog.py` checks it against the engine `REASON_` / `DETAIL_` constants, every `_decide()` call at its STEP, the `# STEP n` headers (STEP 0–30, integers only, in true order), Shared.env, the alert labels, the stats counters and the router. Tier: **on** = `PRIMARY_ENABLED_EXITS`; **off** = `PRIMARY_DISABLED_EXITS` (`premium_trail` stays in the primary tier because the legacy PX2 ratchet also uses it). `twenty_percent_save` is in `PRIMARY_ENABLED_EXITS` because Shared.env has STEP 2 on; the catalog row flag is `True` (tied to `OPTION_PATH_V2_20P_SAVE_ENABLED`; ULR has its own env key). Nothing in the engine reads these tuples, so the change affects only the catalog, logs and tests (`test_on_off_matches_shared_env` pins the match with Shared.env).

| Step | Exit id | Engine detail(s) | State (Shared.env) | Kill switch | Routing | Telegram label | Stats counter |
|---|---|---|---|---|---|---|---|
| 1 | `runner_extended_capture` | `RUNNER_EXT_TARGET`, `RUNNER_EXT_ULREV` (hold details `RUNNER_EXT_START`, `RUNNER_EXT_HOLD`) | on (R2X, 10/6) | `RUNNER_EXTENSION_ENABLED` | passive | Runner Extended Capture (R2X) (new 10/6) | `runner_extended_capture_exits` (new 10/6) |
| 1 | `runner_extension_floor` | `RUNNER_EXT_FLOOR` | on (R2X, 10/6) | `RUNNER_EXTENSION_ENABLED` | passive | Runner Extension Floor (R2X, bid < +50%) (new 10/6) | `runner_extension_floor_exits` (new 10/6) |
| 2 | `twenty_percent_save` | `P20_STALL_SAVE` | **on (11/03)** | `20P_SAVE_ENABLED` | moderate, captured executable-bid limit | 20% Save | `twenty_percent_save_exits` |
| 2 | `twenty_percent_save` | `P20_UL_RETRACE_SAVE` | **on via env (11/03 ext)**; off without entry fields | `20P_SAVE_ULR_ENABLED` | moderate, captured executable-bid limit | 20% Save UL-retrace extension | `twenty_percent_save_exits` |
| 3 | `shallow_proof_stop` | `SHALLOW_PROOF_10_20` | on | `SHALLOW_PROOF_STOP_ENABLED` | moderate | Shallow Proof Stop (10–20% MFE, bid ≤ −30%) | `shallow_proof_stop_exits` (new 10/6) |
| 4 | `catastrophic_premium_stop` | `CAT_PREM_STOP` | on (mandatory) | — | urgent market | Catastrophic Premium Stop (−80%) | `catastrophic_premium_stop_exits` |
| 5 | `mfe_trail` | `MFE_TRAIL_1230` | on (mandatory) | — | passive | Afternoon MFE Trail (−10pp after 12:30) | `mfe_giveback_exits` |
| 6 | `absorption_exit` | `ABSORPTION_EXIT` | on (mandatory) | `ABSORPTION_EXIT_ENABLED` | passive | Absorption Exit (+2% weak expansion) | `absorption_exit_exits` |
| 7 | `runner_timed_capture` | `RUNNER_TIMED_CAPTURE_350`, `RUNNER_TIMED_CAPTURE_350_A30` | on | `RUNNER_CAPTURE_ENABLED` (+ V3A RT) | passive | 350% Runner Timed Capture | `runner_timed_capture_exits` |
| 8 | `runner_profit_floor` | `RUNNER_PROFIT_FLOOR_350`, `RUNNER_PROFIT_FLOOR_350_G900` | on | `RUNNER_CAPTURE_ENABLED` (+ V3A RT) | passive | 350% Runner Profit Floor | `runner_profit_floor_exits` |
| 9 | `guarded_retrace_exit` | `GBUL_RETRACE_05_65` | on (10/22, local) | `GBUL_ENABLED` | passive | Guarded UL Retrace Capture (30-200% MFE, UL off 0.5%, bid ≥ 65% of peak) (new 10/22) | `guarded_retrace_exits` (new 10/22) |
| 9 | `ul_giveback_ratio_exit` | `UFR_GIVEBACK_30_70` | on (10/22, local) | `UFR_ENABLED` | passive | UL Giveback Ratio Capture (30-200% MFE, UL gave back 30%, bid ≥ 70% of peak) (new 10/22) | `ul_giveback_ratio_exits` (new 10/22) |
| 10 | `premium_trail_>50%_25trail` | `PREMIUM_TRAIL_GT50_25TRAIL` | **off** | `HYBRID_PREMIUM_TRAIL_ENABLED` | moderate | Hybrid Premium Trail (50% / 25pp) | `premium_trail_gt50_25trail_exits` |
| 11 | `premium_trail_>30%_10trail` | `PREMIUM_TRAIL_GT30_10TRAIL` | **off** | `HYBRID_PREMIUM_TRAIL_ENABLED` | moderate | Hybrid Premium Trail (30% / 10pp) | `premium_trail_gt30_10trail_exits` |
| 12 | `premium_spike_fail` | `SPIKE_FAIL` | on | `SPIKE_FAIL_ENABLED` | moderate | Premium Spike Fail (30–60% / 15pp) | `premium_spike_fail_exits` |
| 12 | `underlying_mfe_capture` | `UL_MFE_CAPTURE_175_250` | on | `UNDERLYING_MFE_CAPTURE_ENABLED` | passive | Underlying-confirmed MFE Capture (175-250%) | `underlying_mfe_capture_exits` |
| 13 | `premium_stale_fail` | `STALE_HIGH_FAIL` | on | `STALE_FAIL_ENABLED` | moderate | Premium Stale High Fail | `premium_stale_fail_exits` |
| 14 | `theta_exit` | `THETA_EXIT` | **off** | `THETA_EXIT_ENABLED` | moderate | Theta Never-Green Exit (off) | `theta_exit_exits` |
| 15 | `contract_fade_exit` | `CONTRACT_FADE_EXIT` | **off** (10/6) | `CONTRACT_FADE_EXIT_ENABLED` | passive | Contract Fade Exit (off) | `contract_fade_exit_exits` |
| 17 | `shallow_green_exit` | `SHALLOW_GREEN_EXIT` | on | `SHALLOW_GREEN_EXIT_ENABLED` | passive | CALL Shallow-Green Exit | `shallow_green_exit_exits` |
| 18 | `shallow_time_stop` | `SHALLOW_15M_MFE7P5` | on | `SHALLOW_TIME_STOP_ENABLED` | moderate | Shallow 15m / <7.5% MFE Stop | `shallow_time_stop_exits` (new 10/6) |
| 19 | `bounded_mfe_capture` | `BOUNDED_MFE_CAPTURE_290_305` | on | `BOUNDED_MFE_CAPTURE_ENABLED` | passive | Bounded MFE Capture (290-305% / 10pp) | `bounded_mfe_capture_exits` |
| 20 | `premium_trail` | `PATH_V2_EARLY_PREM_TRAIL` | **off** | `EARLY_PREM_TRAIL_ENABLED` | moderate | Premium Trail (20pp after ARM) | none (off; legacy PX2 id) |
| 21 | `premium_trail` | `PATH_V2_PREMIUM_TRAIL` | **off** | `PREMIUM_TRAIL_ENABLED` | moderate | Premium Trail (20pp after ARM) | none (off; legacy PX2 id) |
| 22 | `shallow_floor_stop` | `SHALLOW_FLOOR_5_10_18` | on (V3A) | `SHALLOW_FLOOR_STOP_ENABLED` | moderate | Shallow Floor Stop (5–10% MFE, bid ≤ −18%) (new 10/6) | `shallow_floor_stop_exits` (new 10/6) |
| 23 | `immature_stress` | `IMMATURE_STRESS` (hold detail `AE_WARNING_HOLD`) | on | — (`AE_WARNING_ENABLED` for the hold) | moderate | Immature Stress Exit | `immature_stress_exits` |
| 24 | `gb30_giveback` | `GB30_24PP` (hold detail `GB30_38PP_EXTENSION_HOLD`) | on | `GB30_ENABLED`, `GB30_EXTENSION_ENABLED` | passive | 30–50% Giveback (−24pp) | `gb30_giveback_exits` |
| 25 | `hwm_rtc` | `HWM_RTC` | on | — | passive | Mature High-Water Retrace | `hwm_rtc_exits` |
| 26 | `gb32_timed_giveback` | `GB32_40_TIMED_15PP` | on | `GB32_TIMED_ENABLED` | passive | 32–40% Timed Giveback (−15pp, high ≥120s) | `gb32_timed_giveback_exits` |
| 27 | `deep_green_stop` | `DEEP_STOP_10_30_65` | on | `DEEP_STOP_ENABLED` | moderate | Deep Green Stop (10–30% MFE, bid ≤ −65%) | `deep_green_stop_exits` |
| 28 | `stall_floor_stop` | `STALL_FLOOR_15_40_4` | on (V3A) | `STALL_FLOOR_STOP_ENABLED` | moderate | Stall Floor Stop (15–40% MFE, stalled high, bid ≤ +4%) (new 10/6) | `stall_floor_stop_exits` (new 10/6) |
| 29 | `atr_loss_cut` | `ATR_LOSS_CUT_30_2ATR` | on (10/27, local) | `ATR_LOSS_CUT_ENABLED` | moderate | ATR Loss Cut (bid ≤ −30%, UL 2 ATR against, below VWAP) (new 10/27) | `atr_loss_cut_exits` (new 10/27) |
| 30 | `vwap_cross_exit` | `VWAP_CROSS_50_025ATR` | on (10/27, local) | `VWAP_CROSS_EXIT_ENABLED` | passive | VWAP Cross Exit (50%+ peak, 0.25 ATR through VWAP, bid ≥ +10%) (new 10/27) | `vwap_cross_exits` (new 10/27) |
| monitor | `no_data` | — | on (mandatory) | — | urgent market | No Data Exit (new 10/6) | `degraded_exit_count` (live-quote mode) |
| monitor | `degraded_data` | — | on (mandatory) | — | urgent market | Degraded Data Exit (new 10/6) | `degraded_exit_count` (live-quote mode) |
| EOD | `end_of_day_exit` | — | on (mandatory, 12:50 PT) | — | urgent market | End of Day Exit | `eod_exits` |

- STEP 0 and STEP 16 never exit. *(11/03: 32 engine rows / 31 engine exit ids / 36 details + 3 monitor/EOD exits = 34 live ids. 10/27 appended STEP 29–30; 11/03 added `P20_UL_RETRACE_SAVE`.)* `AE_WARNING_HOLD` (STEP 23), `RUNNER_EXT_START` (STEP 7) and `RUNNER_EXT_HOLD` (STEP 1) are hold details, not exits. Kill switch names drop the `OPTION_PATH_V2_` prefix.
- Old details still normalize (`GB30_25PP`, `GB30_20PP`, `SHALLOW_20M`, `RUNNER_*_300`, `PVE_20P_SAVE`, …).
- **Historical ids** (normalize only, in `DISABLED_EXITS`): `mfe_trailing_stop`, `moon_hold`, and since 10/6 `ul_trail` / `ul_be` (PC8 engine removed Sep 23).
- **Legacy stack ids** (`mfe_take_profit`, `price_stop`, `premium_stop`, `price_trail`, `take_profit`, `hold_exit`, `breakeven`, `scratch`, `time_exit`, `no_progress`) are emitted only under `OPTION_PC_ARCHITECTURE=current` or by non-PATH_V2 positions.
- `PATH_END` is the replay's end-of-tape label, not a live exit id. Live, those trades close by `end_of_day_exit`.

### Complete OPTION_PATH_V2_* knob reference (generated 10/22 from `load_path_v2_config()` and Shared.env)

*(10/26: +3 STEP 17 SGUL keys, then 210 loader keys / 212 in Shared.env; 14 ≠ knobs incl. `SG_UL_HOLD_ENABLED`.)* *(10/27: +13 STEP 29–30 keys, then 223 loader keys / 225 in Shared.env; 16 ≠ knobs incl. `ATR_LOSS_CUT_ENABLED` and `VWAP_CROSS_EXIT_ENABLED`.)* *(11/04 recount after sparse-velocity removal.)* As of 11/04 streamline, **231** `OPTION_PATH_V2_*` keys are read by `load_path_v2_config()` (includes `OPTION_PATH_V2_ENABLED`), **232** in Shared.env (includes the caller's `OPTION_PATH_V2_CONTRACT_FADE_ENTRY_CHAIN_FALLBACK_ENABLED=true`). Four inert ULR keys (`ULR_ATR_K`, `ULR_ARM_S`, `ULR_MAX_TOUCH_HOUR_ET`, `ULR_SYMBOLS`) were removed; ULR is the UL-move path only. **≠** marks the knobs where Shared.env differs from the dataclass default: the V3A, R2X, STEP 9, underlying and bounded MFE capture switches (on in Shared.env, off in the dataclass), Contract Fade (off in Shared.env, on in the dataclass), the four Stale-fail 4 / 0.34 values, and the 20% Save switches (`false` in the dataclass, `true` in Shared.env, 11/03). Values are Shared.env as of the sha above.

| Knob (`OPTION_PATH_V2_` prefix dropped) | STEP / state | `PathV2Config` field | Dataclass default | Shared.env |
|---|---|---|---:|---:|
| `ENABLED` | architecture | — (not a PathV2Config field; `path_v2_architecture_active`) | true | true |
| `CONTRACT_FADE_ENTRY_CHAIN_FALLBACK_ENABLED` | 15 (caller, `os.getenv` in `prime_trading_system`) | — | true | true |
| `MIN_HOLD_SECONDS` | 16 | `min_hold_seconds` | 120.0 | 120 |
| `EARLY_CUT` | 16 (S6 region) | `early_cut` | 0.15 | 0.15 |
| `STRESS_THR` | 23 | `stress_threshold` | 0.4 | 0.40 |
| `PROOF` | 16 / S6 | `proof` | 0.5 | 0.50 |
| `RTC_RETRACE` | 25 | `rtc_retrace` | 0.2 | 0.20 |
| `RTC_MATERIAL_SEC` | 25 | `rtc_material_seconds` | 300.0 | 300 |
| `RTC_VEL` | 25 | `rtc_vel_threshold` | -0.0005 | -0.0005 |
| `RTC_VEL_WINDOW` | S3 | `rtc_vel_window_seconds` | 10.0 | 10 |
| `MATERIAL_HWM_PP` | S3 | `material_hwm_pp` | 0.05 | 0.05 |
| `CAT_FLOOR` | 4 | `catastrophic_prem_stop_pct` | -0.8 | -0.80 |
| `CAT_MIN_AGE_SECONDS` | 4 | `catastrophic_min_age_seconds` | 60.0 | 60 |
| `SHALLOW_PROOF_STOP_ENABLED` | 3 | `shallow_proof_stop_enabled` | true | true |
| `SHALLOW_PROOF_MIN_AGE_SECONDS` | 3 | `shallow_proof_stop_min_age_seconds` | 60.0 | 60 |
| `SHALLOW_PROOF_MIN_MFE` | 3 | `shallow_proof_stop_min_mfe` | 0.1 | 0.10 |
| `SHALLOW_PROOF_MAX_MFE` | 3 | `shallow_proof_stop_max_mfe` | 0.2 | 0.20 |
| `SHALLOW_PROOF_PRET` | 3 | `shallow_proof_stop_pret` | -0.3 | -0.30 |
| `20P_SAVE_ENABLED` | 2 | `twenty_percent_save_enabled` | false | true |
| `20P_SAVE_MIN_MFE` | 2 | `twenty_percent_save_min_mfe` | 0.2 | 0.20 |
| `20P_SAVE_SIDES` | 2 | `twenty_percent_save_sides` | call | call |
| `20P_SAVE_MIN_T20_S` | 2 | `twenty_percent_save_min_t20_s` | 90.0 | 90 |
| `20P_SAVE_MAX_PEAK` | 2 | `twenty_percent_save_max_peak` | 0.5 | 0.50 |
| `20P_SAVE_MIN_SINCE_TOUCH_S` | 2 | `twenty_percent_save_min_since_touch_s` | 60.0 | 60 |
| `20P_SAVE_MIN_SEC_HWM` | 2 | `twenty_percent_save_min_sec_hwm` | 60.0 | 60 |
| `20P_SAVE_GIVEBACK` | 2 | `twenty_percent_save_giveback` | 0.15 | 0.15 |
| `20P_SAVE_MIN_PRET` | 2 | `twenty_percent_save_min_pret` | 0.11 | 0.11 |
| `20P_SAVE_MAX_NEWHIGH_FRAC` | 2 | `twenty_percent_save_max_newhigh_frac` | 0.05 | 0.05 |
| `20P_SAVE_ULR_ENABLED` | 2 | `twenty_percent_save_ulr_enabled` | false | true |
| `20P_SAVE_ULR_FRAC` | 2 | `twenty_percent_save_ulr_frac` | 0.90 | 0.90 |
| `20P_SAVE_ULR_MAX_ENTRY_VWAP_ATR` | 2 | `twenty_percent_save_ulr_max_entry_vwap_atr` | 0.50 | 0.50 |
| `20P_SAVE_SHADOW_ONLY` | 2 | `twenty_percent_save_shadow_only` | false | false |
| `20P_SAVE_MAX_PVE3_AGE_SEC` | 2 (recorder only) | `twenty_percent_save_max_pve3_age_seconds` | 75.0 | 75 |
| `20P_SAVE_SYNC_REFRESH` | 2 (recorder only) | `twenty_percent_save_sync_refresh` | false | false |
| `20P_SAVE_SYNC_TIMEOUT_SEC` | 2 (recorder only) | `twenty_percent_save_sync_timeout_s` | 3.0 | 3 |
| `STALE_MAX_SECONDS` | S1 / S2 | `stale_max_seconds` | 10.0 | 10 |
| `AE_EXP_SPEED_MIN` | 23 | `ae_exp_speed_min` | 0.0001 | 0.0001 |
| `AE_SEC_HWM_MAX` | 23 | `ae_sec_hwm_max` | 120.0 | 120 |
| `AE_WARNING_ENABLED` | 23 | `ae_warning_enabled` | true | true |
| `AE_WARNING_WINDOW_SECONDS` | 23 | `ae_warning_window_seconds` | 90.0 | 90 |
| `AE_WARNING_STALL_SECONDS` | 23 | `ae_warning_stall_seconds` | 45.0 | 45 |
| `AE_WARNING_MATERIAL_HWM_PP` | 23 | `ae_warning_material_hwm_pp` | 0.02 | 0.02 |
| `AE_WARNING_MAX_START_RETURN` | 23 | `ae_warning_max_start_return` | 0.0 | 0.00 |
| `PREMIUM_TRAIL_ENABLED` | 21 | `premium_trail_enabled` | false | false |
| `PREMIUM_TRAIL_TRIGGER` | 21 | `premium_trail_trigger_pct` | 1.0 | 1.00 |
| `PREMIUM_TRAIL_EXIT` | 21 | `premium_trail_exit_pct` | 0.3 | 0.30 |
| `PREMIUM_TRAIL_MIN_SEC_HWM` | 21 | `premium_trail_min_sec_since_hwm` | 900.0 | 900 |
| `EARLY_PREM_TRAIL_ENABLED` | 20 | `early_premium_trail_enabled` | false | false |
| `EARLY_PREM_TRAIL_TRIGGER` | 20 | `early_premium_trail_trigger_pct` | 1.75 | 1.75 |
| `EARLY_PREM_TRAIL_EXIT` | 20 | `early_premium_trail_exit_pct` | 0.05 | 0.05 |
| `EARLY_PREM_TRAIL_MIN_CURRENT` | 20 | `early_premium_trail_min_current_pct` | 0.75 | 0.75 |
| `BOUNDED_MFE_CAPTURE_ENABLED` | 19 | `bounded_mfe_capture_enabled` | false | true **≠** |
| `BOUNDED_MFE_CAPTURE_MIN_MFE` | 19 | `bounded_mfe_capture_min_mfe` | 2.9 | 2.90 |
| `BOUNDED_MFE_CAPTURE_MAX_MFE` | 19 | `bounded_mfe_capture_max_mfe` | 3.05 | 3.05 |
| `BOUNDED_MFE_CAPTURE_GIVEBACK` | 19 | `bounded_mfe_capture_giveback` | 0.1 | 0.10 |
| `BOUNDED_MFE_CAPTURE_MIN_PRET` | 19 | `bounded_mfe_capture_min_pret` | 1.0 | 1.00 |
| `BOUNDED_MFE_CAPTURE_START_PT_HOUR` | 19 | `bounded_mfe_capture_start_pt_hour` | 10.75 | 10.75 |
| `SHALLOW_TIME_STOP_ENABLED` | 18 | `shallow_time_stop_enabled` | true | true |
| `SHALLOW_TIME_STOP_SECONDS` | 18 | `shallow_time_stop_seconds` | 900.0 | 900 |
| `SHALLOW_TIME_STOP_MAX_MFE` | 18 | `shallow_time_stop_max_mfe` | 0.075 | 0.075 |
| `GB30_ENABLED` | 24 | `gb30_enabled` | true | true |
| `GB30_MIN_MFE` | 24 | `gb30_min_mfe` | 0.3 | 0.30 |
| `GB30_GIVEBACK` | 24 | `gb30_giveback` | 0.24 | 0.24 |
| `GB30_EXTENSION_ENABLED` | 24 | `gb30_extension_enabled` | true | true |
| `GB30_EXTENSION_GIVEBACK` | 24 | `gb30_extension_giveback` | 0.38 | 0.38 |
| `GB30_EXTENSION_MAX_UL_DD` | 24 | `gb30_extension_max_ul_dd` | 0.0009 | 0.0009 |
| `GB30_EXTENSION_MIN_PRET` | 24 | `gb30_extension_min_pret` | 0.05 | 0.05 |
| `GB30_EXTENSION_MAX_PRET` | 24 | `gb30_extension_max_pret` | 0.1 | 0.10 |
| `GB32_TIMED_ENABLED` | 26 | `gb32_timed_enabled` | true | true |
| `GB32_TIMED_MIN_MFE` | 26 | `gb32_timed_min_mfe` | 0.32 | 0.32 |
| `GB32_TIMED_MAX_MFE` | 26 | `gb32_timed_max_mfe` | 0.4 | 0.40 |
| `GB32_TIMED_GIVEBACK` | 26 | `gb32_timed_giveback` | 0.15 | 0.15 |
| `GB32_TIMED_MIN_SEC_HWM` | 26 | `gb32_timed_min_sec_hwm` | 120.0 | 120 |
| `GB32_TIMED_MIN_PRET` | 26 | `gb32_timed_min_pret` | 0.05 | 0.05 |
| `DEEP_STOP_ENABLED` | 27 | `deep_stop_enabled` | true | true |
| `DEEP_STOP_MIN_AGE_SECONDS` | 27 | `deep_stop_min_age_seconds` | 60.0 | 60 |
| `DEEP_STOP_MIN_MFE` | 27 | `deep_stop_min_mfe` | 0.1 | 0.10 |
| `DEEP_STOP_MAX_MFE` | 27 | `deep_stop_max_mfe` | 0.3 | 0.30 |
| `DEEP_STOP_PRET` | 27 | `deep_stop_pret` | -0.65 | -0.65 |
| `ABSORPTION_EXIT_ENABLED` | 6 | `absorption_exit_enabled` | true | true |
| `ABSORPTION_MIN_AGE_SECONDS` | 6 | `absorption_min_age_seconds` | 450.0 | 450 |
| `ABSORPTION_MAX_SPEED` | 6 | `absorption_max_speed` | 5e-05 | 0.00005 |
| `ABSORPTION_MIN_PRET` | 6 | `absorption_min_pret` | 0.02 | 0.02 |
| `THETA_EXIT_ENABLED` | 14 | `theta_exit_enabled` | false | false |
| `THETA_EXIT_MIN_AGE_SECONDS` | 14 | `theta_exit_min_age_seconds` | 300.0 | 300 |
| `THETA_EXIT_MIN` | 14 | `theta_exit_min` | -0.5 | -0.50 |
| `SHALLOW_GREEN_EXIT_ENABLED` | 17 | `shallow_green_exit_enabled` | true | true |
| `SHALLOW_GREEN_MIN_AGE_SECONDS` | 17 | `shallow_green_min_age_seconds` | 120.0 | 120 |
| `SHALLOW_GREEN_MIN_MFE` | 17 | `shallow_green_min_mfe` | 0.02 | 0.02 |
| `SHALLOW_GREEN_MAX_MFE` | 17 | `shallow_green_max_mfe` | 0.2 | 0.20 |
| `SHALLOW_GREEN_MIN_SEC_HWM` | 17 | `shallow_green_min_sec_hwm` | 90.0 | 90 |
| `SHALLOW_GREEN_MAX_VEL` | 17 | `shallow_green_max_vel` | 0.0 | 0 |
| `SHALLOW_GREEN_MIN_PRET` | 17 | `shallow_green_min_pret` | 0.0 | 0 |
| `SG_UL_HOLD_ENABLED` | 17 (10/26 SGUL) | `shallow_green_ul_hold_enabled` | false | **true** ≠ |
| `SG_UL_HOLD_MIN_FAV` | 17 (10/26 SGUL) | `shallow_green_ul_hold_min_fav` | 0.005 | 0.005 |
| `SG_UL_HOLD_MAX_GIVEBACK` | 17 (10/26 SGUL) | `shallow_green_ul_hold_max_giveback` | 0.001 | 0.001 |
| `CONTRACT_FADE_EXIT_ENABLED` | 15 | `contract_fade_exit_enabled` | true | false **≠** |
| `CONTRACT_FADE_MAX_VALID_BID_ASK_CONTRACTS` | 15 | `contract_fade_max_valid_bid_ask_contracts` | 13.0 | 13 |
| `CONTRACT_FADE_MIN_MFE` | 15 | `contract_fade_min_mfe` | 0.0 | 0 |
| `CONTRACT_FADE_MAX_MFE` | 15 | `contract_fade_max_mfe` | 0.2 | 0.20 |
| `CONTRACT_FADE_MIN_DRAWDOWN` | 15 | `contract_fade_min_drawdown` | 0.02 | 0.02 |
| `CONTRACT_FADE_MIN_SEC_HWM` | 15 | `contract_fade_min_sec_hwm` | 15.0 | 15 |
| `CONTRACT_FADE_QUOTE_WINDOW_SECONDS` | S4 | `contract_fade_quote_window_seconds` | 30.0 | 30 |
| `HYBRID_PREMIUM_TRAIL_ENABLED` | 10, 11 | `hybrid_premium_trail_enabled` | false | false |
| `PREMIUM_TRAIL_GT50_ARM_MFE` | 10 | `hybrid_aggressive_arm_mfe` | 0.5 | 0.50 |
| `PREMIUM_TRAIL_GT50_GIVEBACK` | 10 | `hybrid_aggressive_giveback` | 0.25 | 0.25 |
| `PREMIUM_TRAIL_GT50_MIN_AGE_SECONDS` | 10 | `hybrid_aggressive_min_age_seconds` | 60.0 | 60 |
| `PREMIUM_TRAIL_GT50_MIN_CURRENT_RETURN` | 10 | `hybrid_aggressive_min_current_return` | 0.2 | 0.20 |
| `PREMIUM_TRAIL_GT50_MAX_UL_RETRACE` | 10 | `hybrid_aggressive_max_ul_retrace` | 0.5 | 0.50 |
| `PREMIUM_TRAIL_GT50_MIN_UL_DRAWDOWN` | 10 | `hybrid_aggressive_min_ul_drawdown` | 0.001 | 0.001 |
| `PREMIUM_TRAIL_GT30_ARM_MFE` | 11 | `hybrid_salvage_arm_mfe` | 0.3 | 0.30 |
| `PREMIUM_TRAIL_GT30_GIVEBACK` | 11 | `hybrid_salvage_giveback` | 0.1 | 0.10 |
| `PREMIUM_TRAIL_GT30_MIN_AGE_SECONDS` | 11 | `hybrid_salvage_min_age_seconds` | 60.0 | 60 |
| `PREMIUM_TRAIL_GT30_MIN_CURRENT_RETURN` | 11 | `hybrid_salvage_min_current_return` | 0.05 | 0.05 |
| `PREMIUM_TRAIL_GT30_MAX_UL_RETRACE` | 11 | `hybrid_salvage_max_ul_retrace` | 0.75 | 0.75 |
| `PREMIUM_TRAIL_GT30_MIN_UL_DRAWDOWN` | 11 | `hybrid_salvage_min_ul_drawdown` | 0.0 | 0.0 |
| `RUNNER_CAPTURE_ENABLED` | 7, 8 | `runner_capture_enabled` | true | true |
| `RUNNER_CAPTURE_ARM_MFE` | S6 / 7 | `runner_capture_arm_mfe` | 3.5 | 3.50 |
| `RUNNER_CAPTURE_TIMED_MIN_CURRENT_RETURN` | 7 | `runner_capture_timed_min_current_return` | 4.0 | 4.00 |
| `RUNNER_CAPTURE_TIMED_MIN_PEAK_RATIO` | 7 | `runner_capture_timed_min_peak_ratio` | 0.5 | 0.50 |
| `RUNNER_CAPTURE_TIMED_MIN_ARM_AGE_SECONDS` | 7 | `runner_capture_timed_min_arm_age_seconds` | 60.0 | 60 |
| `RUNNER_CAPTURE_FLOOR_MIN_AGE_SECONDS` | 8 | `runner_capture_floor_min_age_seconds` | 600.0 | 600 |
| `RUNNER_CAPTURE_FLOOR_PEAK_RATIO` | 8 | `runner_capture_floor_peak_ratio` | 0.8 | 0.80 |
| `SPIKE_FAIL_ENABLED` | 12 | `spike_fail_enabled` | true | true |
| `SPIKE_FAIL_ARM_MFE` | 12 | `spike_fail_arm_mfe` | 0.3 | 0.30 |
| `SPIKE_FAIL_MAX_MFE` | 12 | `spike_fail_max_mfe` | 0.6 | 0.60 |
| `SPIKE_FAIL_GIVEBACK` | 12 | `spike_fail_giveback` | 0.15 | 0.15 |
| `SPIKE_FAIL_MIN_PRET` | 12 | `spike_fail_min_pret` | 0.1 | 0.10 |
| `SPIKE_FAIL_MIN_AGE_SECONDS` | 12 | `spike_fail_min_age_seconds` | 60.0 | 60 |
| `SPIKE_FAIL_MAX_SEC_HWM` | 12 | `spike_fail_max_sec_hwm` | 16.0 | 16 |
| `UNDERLYING_MFE_CAPTURE_ENABLED` | 12 | `underlying_mfe_capture_enabled` | false | true **≠** |
| `UNDERLYING_MFE_CAPTURE_MIN_MFE` | 12 | `underlying_mfe_capture_min_mfe` | 1.75 | 1.75 |
| `UNDERLYING_MFE_CAPTURE_MAX_MFE` | 12 | `underlying_mfe_capture_max_mfe` | 2.5 | 2.50 |
| `UNDERLYING_MFE_CAPTURE_GIVEBACK` | 12 | `underlying_mfe_capture_giveback` | 0.25 | 0.25 |
| `UNDERLYING_MFE_CAPTURE_MIN_PRET` | 12 | `underlying_mfe_capture_min_pret` | 0.5 | 0.50 |
| `UNDERLYING_MFE_CAPTURE_MIN_SEC_HWM` | 12 | `underlying_mfe_capture_min_sec_hwm` | 120.0 | 120 |
| `UNDERLYING_MFE_CAPTURE_MIN_UL_RETRACE` | 12 | `underlying_mfe_capture_min_ul_retr` | 0.5 | 0.50 |
| `UNDERLYING_MFE_CAPTURE_MIN_UL_DD` | 12 | `underlying_mfe_capture_min_ul_dd` | 0.003 | 0.003 |
| `STALE_FAIL_ENABLED` | 13 | `stale_fail_enabled` | true | true |
| `STALE_FAIL_BAND_MIN_MFE` | 13 | `stale_fail_band_min_mfe` | 0.7 | 0.70 |
| `STALE_FAIL_BAND_MAX_MFE` | 13 | `stale_fail_band_max_mfe` | 0.9 | 0.90 |
| `STALE_FAIL_BAND_MIN_PRET` | 13 | `stale_fail_band_min_pret` | 0.02 | 0.02 |
| `STALE_FAIL_BAND_MAX_PRET` | 13 | `stale_fail_band_max_pret` | 0.08 | 0.08 |
| `STALE_FAIL_BAND_MIN_SEC_HWM` | 13 | `stale_fail_band_min_sec_hwm` | 1000.0 | 1000 |
| `STALE_FAIL_GIVEBACK_MIN_MFE` | 13 | `stale_fail_giveback_min_mfe` | 0.7 | 0.70 |
| `STALE_FAIL_GIVEBACK_MAX_MFE` | 13 | `stale_fail_giveback_max_mfe` | 0.9 | 0.90 |
| `STALE_FAIL_GIVEBACK` | 13 | `stale_fail_giveback` | 0.5 | 0.50 |
| `STALE_FAIL_GIVEBACK_MIN_PRET` | 13 | `stale_fail_giveback_min_pret` | 0.25 | 0.25 |
| `STALE_FAIL_GIVEBACK_MIN_SEC_HWM` | 13 | `stale_fail_giveback_min_sec_hwm` | 300.0 | 300 |
| `STALE_FAIL_UPPER_MIN_MFE` | 13 | `stale_fail_upper_min_mfe` | 0.8 | 0.75 **≠** |
| `STALE_FAIL_UPPER_MAX_MFE` | 13 | `stale_fail_upper_max_mfe` | 0.82 | 0.82 |
| `STALE_FAIL_UPPER_MIN_PRET` | 13 | `stale_fail_upper_min_pret` | 0.02 | 0.02 |
| `STALE_FAIL_UPPER_MAX_PRET` | 13 | `stale_fail_upper_max_pret` | 0.4 | 0.40 |
| `STALE_FAIL_UPPER_MIN_SEC_HWM` | 13 | `stale_fail_upper_min_sec_hwm` | 180.0 | 180 |
| `STALE_FAIL_UPPER_MIN_UL_DD` | 13 | `stale_fail_upper_min_ul_dd` | 0.003 | 0.003 |
| `STALE_FAIL_REVERSAL_MIN_MFE` | 13 | `stale_fail_reversal_min_mfe` | 0.5 | 0.50 |
| `STALE_FAIL_REVERSAL_MAX_MFE` | 13 | `stale_fail_reversal_max_mfe` | 0.7 | 0.70 |
| `STALE_FAIL_REVERSAL_MIN_PRET` | 13 | `stale_fail_reversal_min_pret` | 0.02 | 0.02 |
| `STALE_FAIL_REVERSAL_MAX_PRET` | 13 | `stale_fail_reversal_max_pret` | 0.2 | 0.20 |
| `STALE_FAIL_REVERSAL_MIN_SEC_HWM` | 13 | `stale_fail_reversal_min_sec_hwm` | 200.0 | 200 |
| `STALE_FAIL_REVERSAL_MIN_UL_RETRACE` | 13 | `stale_fail_reversal_min_ul_retr` | 1.15 | 1.15 |
| `STALE_FAIL_REVERSAL_MIN_UL_DD` | 13 | `stale_fail_reversal_min_ul_dd` | 0.004 | 0.004 |
| `STALE_FAIL_GAP_MIN_MFE` | 13 | `stale_fail_gap_min_mfe` | 0.9 | 0.90 |
| `STALE_FAIL_GAP_MAX_MFE` | 13 | `stale_fail_gap_max_mfe` | 0.94 | 0.94 |
| `STALE_FAIL_GAP_MIN_PRET` | 13 | `stale_fail_gap_min_pret` | 0.02 | 0.02 |
| `STALE_FAIL_GAP_MAX_PRET` | 13 | `stale_fail_gap_max_pret` | 0.2 | 0.20 |
| `STALE_FAIL_GAP_MIN_SEC_HWM` | 13 | `stale_fail_gap_min_sec_hwm` | 800.0 | 800 |
| `STALE_FAIL_GAP_MIN_UL_RETRACE` | 13 | `stale_fail_gap_min_ul_retr` | 0.55 | 0.55 |
| `STALE_FAIL_HIGH_MIN_MFE` | 13 | `stale_fail_high_min_mfe` | 1.0 | 1.00 |
| `STALE_FAIL_HIGH_MIN_PRET` | 13 | `stale_fail_high_min_pret` | 0.02 | 0.02 |
| `STALE_FAIL_HIGH_MAX_PRET` | 13 | `stale_fail_high_max_pret` | 0.25 | 0.34 **≠** |
| `STALE_FAIL_HIGH_MIN_SEC_HWM` | 13 | `stale_fail_high_min_sec_hwm` | 1800.0 | 1800 |
| `STALE_FAIL_RUNNER_MIN_MFE` | 13 | `stale_fail_runner_min_mfe` | 1.5 | 1.50 |
| `STALE_FAIL_RUNNER_MIN_PRET` | 13 | `stale_fail_runner_min_pret` | 0.02 | 0.02 |
| `STALE_FAIL_RUNNER_MAX_PRET` | 13 | `stale_fail_runner_max_pret` | 0.2 | 0.24 **≠** |
| `STALE_FAIL_RUNNER_MIN_UL_RETRACE` | 13 | `stale_fail_runner_min_ul_retr` | 0.5 | 0.50 |
| `STALE_FAIL_RUNNER_MIN_AGE_SECONDS` | 13 | `stale_fail_runner_min_age_seconds` | 60.0 | 42 **≠** |
| `SHALLOW_FLOOR_STOP_ENABLED` | 22 / S5 | `shallow_floor_stop_enabled` | false | true **≠** |
| `SHALLOW_FLOOR_MIN_MFE` | 22 / S5 | `shallow_floor_min_mfe` | 0.05 | 0.05 |
| `SHALLOW_FLOOR_MAX_MFE` | 22 / S5 | `shallow_floor_max_mfe` | 0.1 | 0.10 |
| `SHALLOW_FLOOR_MIN_AGE_SECONDS` | 22 / S5 | `shallow_floor_min_age_seconds` | 120.0 | 120 |
| `SHALLOW_FLOOR_PRET` | 22 / S5 | `shallow_floor_pret` | -0.18 | -0.18 |
| `SHALLOW_FLOOR_MIN_PEAK_TICKS` | 22 / S5 | `shallow_floor_min_peak_ticks` | 2 | 2 |
| `SHALLOW_FLOOR_REQUIRE_FRESH_CROSS` | 22 / S5 | `shallow_floor_require_fresh_cross` | true | true |
| `RUNNER_TIMING_V3A_ENABLED` | S8 (7, 8) | `runner_timing_v3a_enabled` | false | true **≠** |
| `RUNNER_TIMING_V3A_TIMED_MIN_ARM_AGE_SECONDS` | S8 (7, 8) | `runner_timing_v3a_timed_min_arm_age_seconds` | 30.0 | 30 |
| `RUNNER_TIMING_V3A_FLOOR_GREEN_MIN_AGE_SECONDS` | S8 (7, 8) | `runner_timing_v3a_floor_green_min_age_seconds` | 900.0 | 900 |
| `RUNNER_TIMING_V3A_FLOOR_GREEN_MIN_PRET` | S8 (7, 8) | `runner_timing_v3a_floor_green_min_pret` | 0.5 | 0.50 |
| `STALL_FLOOR_STOP_ENABLED` | 28 / S9 | `stall_floor_stop_enabled` | false | true **≠** |
| `STALL_FLOOR_MIN_MFE` | 28 / S9 | `stall_floor_min_mfe` | 0.15 | 0.15 |
| `STALL_FLOOR_MAX_MFE` | 28 / S9 | `stall_floor_max_mfe` | 0.4 | 0.40 |
| `STALL_FLOOR_MIN_AGE_SECONDS` | 28 / S9 | `stall_floor_min_age_seconds` | 60.0 | 60 |
| `STALL_FLOOR_MAX_AGE_SECONDS` | 28 / S9 | `stall_floor_max_age_seconds` | 3600.0 | 3600 |
| `STALL_FLOOR_MIN_SEC_HWM` | 28 / S9 | `stall_floor_min_sec_hwm` | 1500.0 | 1500 |
| `STALL_FLOOR_FLOOR` | 28 / S9 | `stall_floor_floor` | 0.04 | 0.04 |
| `STALL_FLOOR_MAX_SLIP` | 28 / S9 | `stall_floor_max_slip` | 0.03 | 0.03 |
| `STALL_FLOOR_ARM_ABOVE_SECONDS` | 28 / S9 | `stall_floor_arm_above_seconds` | 30.0 | 30 |
| `STALL_FLOOR_CONFIRM_TICKS` | 28 / S9 | `stall_floor_confirm_ticks` | 2 | 2 |
| `RUNNER_EXTENSION_ENABLED` | 1 (7 entry) | `runner_extension_enabled` | false | true **≠** |
| `RUNNER_EXTENSION_TARGET_MULT` | 1 (7 entry) | `runner_extension_target_mult` | 1.25 | 1.25 |
| `RUNNER_EXTENSION_UL_RETRACE` | 1 (7 entry) | `runner_extension_ul_retrace` | 0.04 | 0.04 |
| `RUNNER_EXTENSION_UL_MIN_MULT` | 1 (7 entry) | `runner_extension_ul_min_mult` | 1.045 | 1.045 |
| `RUNNER_EXTENSION_FLOOR_PRET` | 1 (7 entry) | `runner_extension_floor_pret` | 0.5 | 0.50 |
| `RUNNER_EXTENSION_MIN_TICKS` | 1 (7 entry) | `runner_extension_min_ticks` | 2 | 2 |
| `RUNNER_EXTENSION_JUMP_GUARD` | 1 (7 entry) | `runner_extension_jump_guard` | 0.5 | 0.5 |
| `GBUL_ENABLED` | 9 | `guarded_retrace_enabled` | false | true **≠** |
| `GBUL_MIN_MFE` | 9 | `guarded_retrace_min_mfe` | 0.3 | 0.30 |
| `GBUL_MAX_MFE` | 9 | `guarded_retrace_max_mfe` | 2.0 | 2.00 |
| `GBUL_MIN_UL_DD` | 9 | `guarded_retrace_min_ul_dd` | 0.005 | 0.005 |
| `GBUL_MIN_PEAK_RATIO` | 9 | `guarded_retrace_min_peak_ratio` | 0.65 | 0.65 |
| `GBUL_CONFIRM_TICKS` | 9 | `guarded_retrace_confirm_ticks` | 2 | 2 |
| `UFR_ENABLED` | 9 | `ul_giveback_enabled` | false | true **≠** |
| `UFR_MIN_MFE` | 9 | `ul_giveback_min_mfe` | 0.3 | 0.30 |
| `UFR_MAX_MFE` | 9 | `ul_giveback_max_mfe` | 2.0 | 2.00 |
| `UFR_MIN_UL_MFE` | 9 | `ul_giveback_min_ul_mfe` | 0.005 | 0.005 |
| `UFR_MIN_UL_RETRACE` | 9 | `ul_giveback_min_ul_retrace` | 0.3 | 0.30 |
| `UFR_MIN_PEAK_RATIO` | 9 | `ul_giveback_min_peak_ratio` | 0.7 | 0.70 |
| `UFR_MIN_PRET` | 9 | `ul_giveback_min_pret` | 0.03 | 0.03 |
| `UFR_CONFIRM_TICKS` | 9 | `ul_giveback_confirm_ticks` | 2 | 2 |
| `ATR_LOSS_CUT_ENABLED` | 29 (10/27) | `atr_loss_cut_enabled` | false | **true** ≠ |
| `ATR_LOSS_CUT_MAX_PRET` | 29 (10/27) | `atr_loss_cut_max_pret` | -0.3 | -0.30 |
| `ATR_LOSS_CUT_ATR_MULT` | 29 (10/27) | `atr_loss_cut_atr_mult` | 2.0 | 2.0 |
| `ATR_LOSS_CUT_MAX_PEAK` | 29 (10/27) | `atr_loss_cut_max_peak` | 0.1 | 0.10 |
| `ATR_LOSS_CUT_MIN_AGE_S` | 29 (10/27) | `atr_loss_cut_min_age_s` | 600.0 | 600 |
| `ATR_LOSS_CUT_REQUIRE_VWAP_AGAINST` | 29 (10/27) | `atr_loss_cut_require_vwap_against` | true | true |
| `ATR_LOSS_CUT_BAR_MAX_AGE_S` | 29 (10/27) | `atr_loss_cut_bar_max_age_s` | 180.0 | 180 |
| `VWAP_CROSS_EXIT_ENABLED` | 30 (10/27) | `vwap_cross_exit_enabled` | false | **true** ≠ |
| `VWAP_CROSS_MIN_PEAK` | 30 (10/27) | `vwap_cross_min_peak` | 0.5 | 0.50 |
| `VWAP_CROSS_ATR_BUFFER` | 30 (10/27) | `vwap_cross_atr_buffer` | 0.25 | 0.25 |
| `VWAP_CROSS_CONFIRM_BARS` | 30 (10/27) | `vwap_cross_confirm_bars` | 1 | 1 |
| `VWAP_CROSS_MIN_PRET` | 30 (10/27) | `vwap_cross_min_pret` | 0.1 | 0.10 |
| `VWAP_CROSS_BAR_MAX_AGE_S` | 30 (10/27) | `vwap_cross_bar_max_age_s` | 180.0 | 180 |


## Live wiring and replay parity

Live and replay call the same `evaluate_path_v2_tick` and the same `load_path_v2_config()` after `load_configuration()` (same Shared.env).

**Live choke** (`PrimeTradingSystem._0dte_run_stealth_exit_decision`): overlay session tech, freeze Contract Fade breadth, `stealth.evaluate` → PATH_V2. One evaluate per `(trade_id, monitor_tick_id)`. HOLD stores `out=None`; later Trendline/ORB reuse (`PATH_V2_EVAL_DUP_BLOCKED`). After evaluate, `capture_po_20p_from_metadata`, `_schedule_po_snapshot_persist`, and the shadow compare each run in try/except (`PATH_V2_POST_EVAL_*_SKIP`). `count_path_v2_eval` and `_write_eval_row` always run so the bag is cached. Live passes `mfe_trail_trigger_pct=None`, `mfe_trail_exit_pct=None`. Cadence owner is `_monitor_loop` → `maybe_tick` (not a second evaluate). UL last is the 2s `_ul_refresh_loop` cache. **Local:** no 4s cancel; in-tick join only if the loop is down; `pret` is this tick's exact bid / entry − 1. **Serving `00624-8bn`:** 4s cancel + in-tick on stall. JSONL persist min gap is 5s; daily JSON 45s. Restart rebuilds PATH_V2. `dispatch_exit_decision` is try/except (`PATH_V2_POST_EVAL_DISPATCH_SKIP`).

**Replay** (`modules/path_v2_replay.replay_ticks`): same evaluate inputs (theta, bid/ask, `valid_bid_ask_contracts`, session-tech overlay, MFE knobs None). Missing UL is unavailable, never defaulted to `entry_underlying` or now.

**Live-only:** 20p snapshot keys (`20P_SAVE_MAX_PVE3_AGE_SEC`, `_SYNC_REFRESH`, `_SYNC_TIMEOUT_SEC` — research snapshot only). Evaluate-path capture is `allow_sync_refresh=False` (local; bars come from the tick prime). Shadow compare (`OPTION_STEALTH_EVALUATE_SHADOW`); legacy kill-switch (`OPTION_PC_ARCHITECTURE=current`).

**Replay-only:** none of those; the box harness may skip `RTC_BIDHOLD` / `GR_BIDHOLD` (engine never emits them).

## Per-exit contribution (8/19–11/4)

Contribution = base − (that exit off). Positive means the exit earns its keep. Mandatory exits (cat stop, `mfe_trail`, EOD) were not scored. Net-negative removals flip TP winners; none were removed.

| Exit (switch off) | Total contrib | TP net contrib | TP win-sum contrib | TP W→L if removed |
|---|---:|---:|---:|---:|
| runner_extension (R2X) | +8,696 | +7,486 | +7,486 | 0 |
| stale_fail | +6,749 | +1,750 | +311 | 7 |
| 20% Save (whole) | +5,354 | +1,478 | +730 | 5 |
| runner_capture (350%) | +4,959 | +5,116 | +5,116 | 0 |
| gb30 extension | +4,817 | +4,312 | +4,312 | 0 |
| GBUL | +4,538 | +1,651 | +1,651 | 0 |
| 20% Save ULR | +3,358 | +420 | +420 | 0 |
| shallow_time_stop | +2,722 | +179 | −71 | 0 |
| shallow_proof_stop | +2,158 | −36 | 0 | 0 |
| absorption | +1,858 | +290 | −749 | 13 |
| vwap_cross | +1,748 | +82 | +82 | 0 |
| SGUL hold | +1,738 | +530 | +530 | 0 |
| bounded_mfe | +1,047 | +1,047 | +1,047 | 0 |
| deep_stop | +970 | 0 | 0 | 0 |
| ULMFE | +668 | +252 | +252 | 0 |
| ae_warning | +273 | +4,411 | +3,746 | 24 |
| atr_loss_cut | +208 | +16 | 0 | 0 |
| shallow_floor | +148 | 0 | 0 | 0 |
| gb30 | +96 | −580 | −4,885 | 13 |
| runner_timing_v3a | −20 | −20 | −20 | 0 (1 TP winner −52) |
| UFR | −122 | +237 | +237 | 0 |
| shallow_green_exit | −1,382 | +464 | −1,095 | 22 |
| spike_fail | −2,456 | −633 | −1,854 | 10 |
| stall_floor | −2,534 | −546 | −570 | 1 |
| gb32_timed | −2,658 | −2,046 | −2,364 | 8 |

Proposals for Eric (not in this streamline): (a) `stall_floor_stop` off; (b) `gb32_timed` off; (c) Contract Fade code removal (inert); (d) `runner_timing_v3a` removal (−$20).


## Deploy checklist

Local-only streamline: no deploy, no commit. Eric deploys. Refresh shas immediately before a build.

```
shasum -a 256 modules/path_v2_exit.py modules/path_v2_replay.py modules/session_clock.py \
  modules/prime_trading_system.py modules/prime_options_stealth_trailing_tp.py \
  modules/broker_position_synchronizer.py easy0DTE/modules/options_exit_reason_catalog.py configs/Shared.env
```

| File | sha256 |
|---|---|
| `modules/path_v2_exit.py` | `d48e5966b5ce469ed86f3433809ec55df17cbb65f1c95fb1010fcefda1bb34dd` |
| `modules/path_v2_replay.py` | `b0185ad803d6113c594df340b55bf1a4a9d3d8c84c940fc5930b5399bf6e9209` |
| `modules/session_clock.py` | `c9c40141ed63506fcd6c3a0cb98fc3e0aadde2339697c2b28cfec92e0326e70a` |
| `modules/prime_trading_system.py` | `6487435956dff3633f6cf1922216bfa0eb85bfea2ccb2de9ca9b2f8e08d4b096` |
| `modules/prime_options_stealth_trailing_tp.py` | `d8ff5a10beb405cae4debf4c5a4817cc1647f655252eeae3fd3fde0596114807` |
| `modules/broker_position_synchronizer.py` | `ac429d87f1db3a57e913275860a31eab4373e265b670ec431564a56a164ad85c` |
| `easy0DTE/modules/options_exit_reason_catalog.py` | `ee47cd8f7866c10bf515cdeb0683b541d1e6cd80b790928657613d6c06cd0e70` |
| `configs/Shared.env` | `5e097baaed80e577b26204c6ce6358142021b5591b8d3c315d4b570a128e997a` |

**Env:** `OPTION_PC_ARCHITECTURE=path_v2`, `OPTION_PATH_V2_ENABLED=true`. 20% Save `OPTION_PATH_V2_20P_SAVE_ENABLED=true` + `ULR_ENABLED=true` (no ATR_K / ARM_S / hour-ET / symbols). Off: Contract Fade, hybrid, theta, premium trails. Afternoon trail: `OPTION_SINGLE_MFE_TRAIL_TRIGGER=0.10`, `_EXIT=0.10`. Session clock: `DISCOVERY_ORB2_TL_END_PT=12:30`, `EOD_FLATTEN_START_PT=12:50`. Snapshot-only: `20P_SAVE_MAX_PVE3_AGE_SEC`, `_SYNC_REFRESH`, `_SYNC_TIMEOUT_SEC`. Key counts: **231** loader / **232** Shared.env.

**Before build (scratch copy, `GCS_PERSISTENCE_ENABLED=false`):**
```
python3 -m unittest tests.test_path_v2_exit tests.test_options_exit_reason_catalog \
  tests.test_contract_fade_entry_breadth tests.test_path_v2_exit_catalog \
  tests.test_path_v2_runner_extension tests.test_ul_retrace_capture \
  tests.test_path_v2_sg_ul_hold tests.test_path_v2_exit_units \
  tests.test_pc_parity_1103_1104 tests.test_path_v2_eval_recorder_align \
  tests.test_po_20p_first_touch tests.test_po_mfe20_snapshot
```

**On Demo `00624-8bn`:** Cloud Run `OPTION_PATH_V2_20P_SAVE_ENABLED=true`. Expect `P20_STALL_SAVE` / `P20_UL_RETRACE_SAVE` on **CALL** only (`SIDES=call`). No `contract_fade_exit` on `PATH_V2_EXIT`. `PATH_V2_EVAL_COUNT` after evaluate; one JSONL row per eval. Next deploy is the 11/05 UL pass-2 + current-tick pret + no 3s hub wait. Rollback Save: `ENABLED=false` (whole STEP 2), `ULR_ENABLED=false` (extension), `GBUL`/`UFR` false (STEP 9), `RUNNER_EXTENSION_ENABLED=false` (R2X).

## Changelog

- 2026-11-05: Local runtime (not on `00624-8bn`): UL fetch not cancelled at 4s; pret is this tick's exact bid; 20% snapshot does not wait 3s for hub. No threshold change.
- 2026-11-04: Cadence + Save ON shipped on Demo `00624-8bn`. PC V2 streamline: replay_ticks = live inputs; session_clock 12:30/12:50; MFE knobs from cfg; latch/ULR-inert/nested-historical removed; post-eval side effects protected; this guide slimmed (history moved).
- 2026-11-04: Live/replay eval-clock alignment; ULR treats UL `<= 0` as unavailable; one eval per tick.
- 2026-11-03: STEP 2 stall save + ULR on; PVE3 latch replaced.
- 2026-10-27: STEP 29–30 + STATE S10.
- 2026-10-26: Unify numbering; SGUL hold.
- 2026-10-22: STEP 9 GBUL+UFR on; 20% Save off (later replaced).
- 2026-10-20: 20% Save added (later replaced).
- 2026-10-18: Stale-high ceiling 0.34.
- 2026-10-17: ULMFE + bounded MFE.
- 2026-10-11: GB30 38pp extension.
- 2026-10-06: Flat engine, V3A, SF4, R2X; Contract Fade off.
