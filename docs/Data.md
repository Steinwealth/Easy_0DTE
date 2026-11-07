# Easy 0DTE Data Guide

The live chain this guide describes is the chain Trade Profile picks from. The order that uses it is [0.OrderExecution.md](0.OrderExecution.md).

> **Oct 22 2026 notes (20% Save / PVE3 / PO snapshots; PVE3/PO clocks still apply; 20% Save superseded 11/03).**
> - **20% Save (historical 10/22):** was OFF (`OPTION_PATH_V2_20P_SAVE_ENABLED=false`). The 10/22 replay/20p mining found no reliable first-touch PVE3 latch (saving every 20p trade at +20%: TP −$18,014 on 60, Total −$10,757 on 237; runner signatures failed out of sample). **Superseded 11/03 / on Demo `00624-8bn`:** STEP 2 stall save + UL-retrace ON (`ENABLED=true`, `ULR_ENABLED=true`, `SIDES=call`). Puts do not save. See [0DTEProfitCapture.md](0DTEProfitCapture.md) and [Configs.md](Configs.md). The `mfe_20p` snapshots keep collecting independently.
> - **PVE3 clocks:** `session_tech_bar_at` / `pve3_bar_at` = the source 1m bar time; `session_tech_asof_at` = that minute's close (minute start + 60s — hub stamps are often the last tick in the minute, not the open); `session_tech_observed_at` = the receipt/upsert (fetch) time. The 20% Save latch (`_save20_decide`) judges freshness by `pve3_age_s` from the **bar close**, cap `OPTION_PATH_V2_20P_SAVE_MAX_PVE3_AGE_SEC=75`, not the 90s receipt clock. Live Path V2 / flight JSONL still overlay the current hub, including the forming minute. Priority Optimizer persist slices to bars whose close is ≤ the event (`read_session_tech_at` / `get_session_tech_asof`). When the save runs, the `mfe_20p` snapshot carries the same hub read as the decision (`save20`, `save20_decision`, `save20_hold_reason`, `pve3`, `pve3_limit`, `pve3_bar_at`, `pve3_age_s`, `tech_age_s`, `hub_read_id`, `refreshed_sync` in `po_mfe20_snapshot.py`).
> - **`po_tech_asof_at`:** on handoff it is the decision freeze; on new lifecycle snapshots it is the event time; on exit rows it is set to the exit time (`po_priority_snapshot.py`), so it does **not** show the tech bar's real age. Use `po_session_tech_asof_at` / `session_tech_bar_at` for that.
> - **PO snapshot set:** handoff, entry_executed, age_120s, first_red, 20p, pullback, exit. 50p is retired (Oct 23, 2026). Shared.env: `PO_SNAP_AGE120/FIRST_RED/PULLBACK_ENABLED=true`, pullback ladder `PO_PB_ARM_H=0.15`, `BAND_H=0.20`, `LOW_K=0.70`, `HI_K=0.10`, `MIN_DIP=0.03`, `MAX_SHOTS=4`, `NEXT_MULTS=1.25,1.5,1.5,1.5`, `NEXT_MIN_H=0.30`, `NEXT_MIN_ADD=0.05`, `HUB_TIMEOUT_S=3` (replaces ARM_PEAK/DEPTH/REARM_PP/MAX_CYCLES), `PO_SNAP_AGE_SEC=120`, `MAX_PER_TRADE=6`, `MAX_PER_MIN=40`, `PO_SNAP_SYNC=true`.
> - **Oct 30 persist clocks (in `00619-xr6`):** PO session tech uses cached bars whose minute close is ≤ the event. A future session-tech clock nulls RSI/MACD/BB. Executed copies the handoff freeze. `first_red` ages the live OCC quote on that tick (`po_monitor_quote_at`), not entry `quote_observed_at`. Mid-trade greeks restamp from that OCC quote or stay null. A mid/exit chain older than 60 s, or a row with no chain read, nulls chain volumes (`po_chain_source=stale_over_60s` / `entry_carry`). Owned 5m keys come from OCC volume/OI samples already on the trade. Flatten primes remaining opens once, then persist is cache-only. No extra chain or equity fetch. `breakout_watch_activated` is not a 267-key or 27-key training fill — score `WATCH_ACTIVATION_SCORE_KEYS`. After the next session, `po_session_certification.py` reports `watch_activation_fill`, `watch_tp27_in_scope_fill`, and `exit_session_tech_fill`.
> - **10/20–10/22 snapshot audit:** the wall-clock age fix is live. Still seen on 10/21: late arming, stale chain/tech (tech bars median ~5 min old at exit), frozen greeks (delta frozen on 10–27% of snapshots), `representative_*` strike values, and missing bid/ask size; PVE3 absent on most 20p snapshots (181 of 222 book-matched). Cursor's local fixes are pending verification on the next session.

> **Nov 4 2026 — monitor cadence (in `00624-8bn`).** `_monitor_loop` always pauses after `maybe_tick` (min 0.2s). An empty book clears `_0dte_force_monitor_tick`. Watchdog restarts that loop; it does not evaluate beside a live tick. Tick ids are `u0dte_<ms>` only (Trendline/ORB fast loops mint the same way; a whole-second leftover is reminted). `_ul_refresh_loop` (2s) calls `refresh_underlying_spot_cache_async`. That image still 4s-cancels a slow batch. Evaluate stays cache-only (`resolve_underlying_observation`) and resets UL labels each tick. JSONL min gap **5s**; daily JSON **45s**; `_LINE_COUNTS` is locked and cleared after a GCS download. Restart rebuilds PATH_V2 with the real entry time and does not reset a live HWM. Replay of saved rows still uses stored `eval_now` / UL age.
> - **Nov 6 2026 — runtime pack (on serving `00629-f7k`).** `live_path_v2_pret_from_tick`: pret is this-tick bid / entry − 1 only; missing bid → None → STEP 0 HOLD; never mark / `pnl_pct`. Leftover OCC skip while owned (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`) because leftover shares `occ_fetch`. Discovery scan slot must not wait on a running watch (`unified_discovery_scan_slot_free`). Telegram flush is notification-only (`ledger_alert_flush.py`, FIFO 32 / 3 attempts / 8s; drops never rewrite ledger/fills/GCS). Desk Bid MFE% vs Mark MFE%. 5s + real PATH_V2 + JSONL at 38/50: late bid cov ~27%/30%, fill→first eval ~14.4s, no mark-as-pret / stale exits / duplicate STC — TTL 4s/8s not raised. Sequential inner OCC is architectural. Rollback `00628-pf4`.
> - **Nov 5 2026 — UL pass-2 + OCC apply-on-complete (in `00625-wwv`).** Leave the owned UL fetch running past 4s (`ul_refresh_slow`, cancel=false). A 30s hang logs `ul_refresh_hung` and does **not** cancel (no second `to_thread`). OCC no longer `wait_for`-cancels a finished executor batch. Open exact batch size is 25. Inner OCC waves run **sequentially** and `yield_lane_to_equity_batch` between them. A new OCC fetch is deferred only while last exact is reusable (`OCC_DEFER_FOR_UL_MAX_S=12`). In-tick UL join only when the UL loop task is down. Compact JSONL keys include `path_v2_peak` / `path_pret` / `path_v2_reason`. Serving `00629-f7k` assigns `path_pret` from `live_path_v2_pret_from_tick`. Historical `00625-wwv` still assigned `path_pret = current_pnl_pct`. The 20% snapshot on evaluate does not wait 3s for hub (`allow_sync_refresh=False`). `quote_age_ms`, `contracts` / `quantity`, modeled $0.65×2 fees. 16-tick 5s load: N=38/50 second-half miss ~42% / ~56% — TTL not raised. Rollback `00624-8bn`.
> - **Nov 4 2026 — one stamp, one age table, monitor tick clock (in `00624-8bn`).**
> - **One scored record.** The live gate writes `po_decision_freeze.values` from the post-pick stamp. `po_profile_decision.keys` is that same dict. `decision_digest` is `sha256` of those keys. Do not keep a second pre-pick copy.
> - **One age table.** `central_tech.decision_freeze.max_age_by_source()`: chain **60 s**, quote **120 s**, SPY **45 s**, session tech **90 s**. Scoring, PO hydrate reuse, and chain `*_now` all read it. A key older than its cap is left empty on the freeze. After one refresh, a still-stale gated key waits (`required_data_not_ready`); it is not scored as missing-pass. `dir_pct_change_5m` is not capped here (phased). Env `0DTE_PO_*_ALLOW_STALE_SEC` / `0DTE_PO_CACHE_QUOTE_MAX_AGE_SEC` override only when set.
> - **Owned UL cache.** `_ul_refresh_loop` calls `refresh_underlying_spot_cache_async` every 2s (owned names, cap 25, oldest-first), stamped with the broker `date_time_utc`. **On `00625-wwv`:** the loop does not `wait_for`-cancel at 4s or 30s (`ul_refresh_slow` / `ul_refresh_hung`, cancel=false); in-tick join only when the UL loop task is down; OCC inner waves yield this lane between batches. Rollback `00624-8bn` still 4s-cancels. Each evaluate tick resets UL labels via `begin_underlying_tick`. Evaluate reads that cache: ≤4 s usable, ≤8 s last_good, >8 s unavailable. `UNIFIED_0DTE_MONITOR` now logs `ul_age_ms_p50` / `ul_age_ms_p95` / `ul_unavailable`.
> - **Tick clock.** Path `timestamp` is the monitor/write clock. `eval_now` / `eval_age_s` are the clock passed to `evaluate_path_v2_tick` (replay uses these; historical rows without `eval_now` keep `timestamp`). `broker_quote_timestamp` stays the broker field. `observation_timestamp` is write time. `monitor_tick_id` is `u0dte_<ms>` (millisecond epoch, not whole seconds — two calls in the same second get different ids and both evaluate). One evaluate per `(trade_id, monitor_tick_id)`: HOLD stores `out=None` so later loops reuse the same tick.
> - **Stale chain empty.** Discovery `force_live` when the bag is older than 60 s. A fresh bag is not overwritten by a later tick. `call_chain_volume_now` is left empty when the volume is stale; it is not copied from the last tick. Live exits do not read chain data, so emptying `*_now` older than 60 s only changes what is recorded.

> **Oct 13 2026 (14:30) data-quality notes (deployed in `easy-etrade-strategy-00599-lhd`).**
> - **`otm_pct` is SIGNED, a single key:** + OTM, 0 ATM, − ITM. Producers: `convex_chain_skew._otm_fraction`, `breakout_contract_gates.compute_otm_pct`, `session_tech_admit`, `po_preorder_seal._otm_pct`, `po_snapshot_hydration`, the single-TP picker and `admit_contract_band`. No producer clamps or takes `abs()` any more; the band picker and the single-TP picker skip ITM strikes, so a pick is always ≥ 0. The OTM caps (0.055, Breakout 0.035 via `BREAKOUT_RESEARCH_MAX_OTM_PCT`) are upper bounds only, so a negative (ITM) value passes the cap on legacy flag-off paths that can still choose ITM. Live handoffs before 10/13 14:25 PT carry the clamped value (0.0 for ITM).
> - **Stale chain → not an Entry training row:** if the decision freeze's chain age is over its cap (`TP_FREEZE_CHAIN_MAX_AGE_SEC`, 60 s), or any chain key is stale-gated, the handoff/entry envelope (`execution_feature_snapshot` via `po_source_contract.stale_chain_ineligibility`) sets `po_entry_training_eligible=false` and `training_ineligible_reason=stale_chain_over_60s`. It also adds `chain_age_sec`, `chain_asof` and `gate_reason`. The snapshot is kept for audit. Book and mining builders skip ineligible handoffs (`docs/0.ProfitabilityThresholds/10.13/build_confirmed_books_8.19_10.13.py`). On 10/13: 19 TL0 handoffs, all `tp_freeze_stale_defer` with stale chain keys (61–676 s) and none traded; the books are unchanged.
> - **`dir_pct_change_5m` is a research/diagnostics key:**
>   - **When stamped:** when a ring prior aged 4–7 min (strict) or 3–10 min (fallback) exists, with `dir_pct_change_5m_prior_age_sec` and `dir_pct_prior_age_s`. Priors come from the open-seed pre-warm and the path-agnostic sampler (P2, capped). Architecture: §5.3 "`dir_pct_change_5m` / 5m chain keys — architecture".
>   - **How 10.5 treats it:** it never rejects when missing. It is not in any `missing_reject_keys`, and 0.0 counts as missing. It is range-checked only when present, on TL1 PUT, TL2+ PUT and Breakout CALL.
>   - **Fill:** about 1% live on 10/13. It is not a reliable live key.
>   - **t168u (Oct 14; deploy env turns it on at 12/min, rev `00600-fpn`): one path-agnostic pre-sampler.** `central_tech/dir5m_presample.on_discovery_quotes` runs once per unified discovery tick (`discovery_watch_batch.run_unified_0dte_discovery_scan`, after the batch quote) for **every** path and side. It reads ORB1 / TL0–TL6 / ORB2-Breakout candidates through one level interface (`dir5m_levels()` → `trendline.value_at()` → `orb_high`/`orb_low`), ranks in-band symbols closest-first, and queues ≤ 6 P2 chains/min (deploy 12; 1 per symbol per 300 s, 1800 s re-arm) via `chain_fetch_manager`, behind the P0/P1 reserve. The open seed is the chain pre-warm at 06:40 / 06:50 / 07:00 / 07:26 / 07:33. Stamped on every decision (ring v2): `dir_pct_approach_lead_s` (seconds from the first pre-sample or seed to the decision) and `dir_pct_approach_source` (`orb1|tl|orb2:approach`, `…:armed`, `open_seed`, `none`); both are in the freeze `dir5m` block. Flag `DIR5M_APPROACH_SAMPLER_ENABLED` (code default false; deploy true). Replay 9/15–10/14: stamp rate (strict 4–7 min + fallback 3–10 min) 18.6% → **81%** (strict alone 9% → 43%), at about 1,900 sampler chains/day (6/min saturated 06:45–12:30, ≈ 3,800 Market API calls/day) plus about 650 pre-warm chains/day. It stays missing-pass and is not a gate key until the measured rate is ≥ 95–100%. See `docs/0.ProfitabilityThresholds/10.14/grokbot_books/dir5m_unified_t168u.md`.
> - **Performance (Eric):** wide collect can produce 150–200 trades/day. A missing key or handoff during wide collect should first be checked as a load/bottleneck artifact (chain budget skips, stale cached chains). Prefer marking stale or incomplete rows not training-eligible over adding broker calls. The single-TP admit path cuts load (~7–10 trades/session).

> **Oct 13 2026 — Single Trade Profile admit path (**DEPLOYED** 10/13 3:08 PM PT in `easy-etrade-strategy-00599-lhd`: `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`, no `TRADE_PROFILE_SHADOW_VERSION`; the 10.5 JSON is loaded but not the admit; code default `false`).** RESEARCH only. With the flag off (rollback) the app runs exactly as before (legacy wide_collect / GATE_* stack). With the flag on, every signal runs:
>
> 1. **Discovery Scan** (unchanged; 10 s, 24-symbol batches).
> 2. **Must Execute** — feasibility only, never quality. In order: signal geometry (`orb_break`/`orb_range`/`regime`, `tl_break`, `bo_confirm`; ORB `PS`/`top_n` rank only) → cooldown / day-exclude → book-full precheck (`max_open_positions_reached`, no API, no cooldown) → **one** chain fetch via `chain_fetch_manager` → data readiness (`data_wait:<key>` holds the break; after `DIR_PCT_DEFER_MAX_WAIT_SEC` (90 s) it retires as `dir_pct_data_unavailable` / `data_unavailable:<key>`, no cooldown) → **Call / Put Skip Signals** (`GATE_call_ss` / `GATE_put_ss`, same evaluators and `QG_*_SS_*` levels, kept OUTSIDE the Trade Profile; CALL SS may early-out pre-chain on TL/BO and is not re-run; PUT SS runs at chain time) → `skip_signal:<call|put>` → **one shared contract pick** (premium 0.03–2.50, \|delta\| 0.18–0.42 (+tolerance), OTM ≤ 0.055 (Breakout 0.035), contract vol ≥ 50 / OI ≥ 100 when present, one spread rule: spread % of mid ≤ `0DTE_MAX_SPREAD_PCT_ORB1` 8 / `_BREAKOUT` 30 / `_TRENDLINE` 30 (Breakout has its own key) or ask−bid ≤ `0DTE_SPREAD_CHEAP_MAX_USD` ($0.01), re-checked at submit) → `contract_unavailable:<delta_out_of_band|otm|premium_low|premium_high|volume|oi|spread|no_quote>`.
> 3. **Single stamp** (`central_tech/decision_stamp.py`): one immutable dict = the TP decision keys read once from the hydrated row + `call_share`/`put_share` (computed once) + `equity_volume_ratio` (Wide extension) + the **picked** contract's `delta`/signed `otm_pct`/`option_l1_imbalance` (OTM positive, ATM zero, ITM negative; L1 from the picked contract's own bid/ask sizes on the cached chain; representative value kept only when the picked contract has no sizes). `decision_digest = sha256(canonical keys)`. The same dict feeds the TP, the decision record, the handoff snapshot freeze (`po_decision_freeze.decision_digest`) and the TP_DECISION log line; the TP returns `evaluated_digest` and a mismatch rejects `data_unavailable:stamp_parity`.
> 4. **Trade Profile** — exactly one evaluation, `TRADE_PROFILE_VERSION=wide|10.5` (`easy0DTE/configs/trade_profile_wide.json` or `trade_profile_10.5.json`); optional `TRADE_PROFILE_SHADOW_VERSION` evaluates the other version on the same stamp (record only, never blocks).
> 5. **Accept / reject** — `tp_admit:<cell>` or `tp_reject:<key>:<min|max|missing|not_and>`.
> 6. **Sizing / caps** (unchanged) — $600 per position (floor(600/(premium×100)), min 1; a $0.05 contract = 120), max open 50 everywhere incl. TL slots, TL slot persistence/recovery, `capacity:*`.
>
> **Removed from admit when the flag is on:** the GATE_* quality stack (dir%, Vol_Ratio, break_max, pc_oi, path_ss, entry_part, red_day, observe stamps), Layer D contract gates, the post-TP contract-band check, cheap-lotto. dir%, Vol_Ratio and break_max now live in TP Wide lines. PO snapshot writers (handoff, executed, 20p, age_120s, first_red, pullback, exit), discovery, the position monitor and the flight recorder are unchanged.
>
> **Live missing-value policy = the loaded profile JSON (Eric 10/13; no code-side table).** `single_tp_admit` applies no pre-policy. **10.5:** `trade_profile_10.5.json` via `trade_profile._evaluate_10_5` — missing = pass, except each cell's `missing_reject_keys` (ORB1 CALL: PVE 3m, distance_from_vwap_atr, rsi; ORB1 PUT: PVE 3m, rsi; TL0 CALL: distance_from_vwap_atr, rsi; TL0 PUT: PVE 3m, distance_from_vwap_atr, rsi); `zero_is_missing` (dir_pct_change_5m, spy_change_pct) counts as missing; derived share lines pass on missing input; 10.5 never data_waits. **Wide:** `trade_profile_wide.json` `semantics.missing_default` = pass, per-cell `missing_reject_keys` = [equity_volume_ratio] (legacy Vol_Ratio), `missing_defer_keys` = the share key → `data_wait` (legacy dir% defer); break_distance_pct missing passes (legacy break_max skip). Verified: single-TP flag-on 10.5 8/19–10/13 = 216 / 167W/49L / +$96,369.82, row-for-row identical to the confirmed book; wide parity 257/257.
>
> **TP Wide** (`trade_profile_wide.json`, built by `build_tp_wide.py`): per cell `call_share`/`put_share` > 0.10 (strict, same side); `break_distance_pct` ≤ 0.025 (ORB1, Breakout); **`equity_volume_ratio` ≥ 0.012 inclusive on all ten cells** (Nov 2 2026; was 0.12 on ORB1/Breakout). No spread line (picker only), no Skip-Signal lines (outside TP). Picker `no_quote` means bid or ask ≤ 0 (no two-sided book), not a premium below $0.03. Premium band remains **$0.03–$2.50**. An empty two-sided cache is treated like a stale PO stamp: `force_live` (`no_two_sided_quotes`). A `no_quote` pick on a cache that is not a just-live fetch holds `data_wait:chain_quotes` (no 1h cooldown). Same Demo/Live door. Local until `deploy now`.
>
> **Rollback:** traffic to `easy-etrade-strategy-00598-tr4`, or `SINGLE_TP_ADMIT=false` (legacy stack is still in the image). Replays (Oct 13, JSON missing policy): single-TP flag-on 10.5 8/19–10/13 = **216 / 167W/49L / +$96,369.82**, row-for-row identical to the confirmed book (`docs/0.ProfitabilityThresholds/10.13/`); Total 4,395 / −$158,470.91. Wide parity on 10/13 decision records: 257/257.


**This copy:** RESEARCH APP · GCS `research_app/` · Cloud Run `easy-etrade-strategy`  
**Serving now (Nov 5 2026, `00625-wwv`):** `easy-etrade-strategy-00625-wwv` · `MODE=demo` · `SINGLE_TP_ADMIT=true` · `TRADE_PROFILE_VERSION=wide` · $100,000 seed · `OPTION_PATH_V2_20P_SAVE_ENABLED=true` (CALL-only) · sequential OCC + UL yield, reusable-gated OCC defer, eval JSONL engine/qty/fee keys. Rollback of this image `00624-8bn`. `00599-lhd` was the 10/13 first pin of that admit. FINAL Live is retired. **Local Nov 6 pack is not on this revision** (honest pret helper, leftover skip, discovery scan slot, notification-only Telegram, Bid vs Mark MFE).  
**Scope:** RESEARCH is the canonical Easy 0DTE app. Legacy FINAL references in older history are archival only and are not the active work target.

**Oct 9 2026 admit note:** Research Demo (`easy-etrade-strategy-00596-sqc`) admits on the **wide collection** `GATE_*` stack (`QUALITY_GATE_ADMIT=wide_collect`; dir% >0.10, vol floor, break_max 2.50, contract band). The 10.5 Trade Profile still computes, stamps and records every decision (decision freeze and handoff keys unchanged) but is observe-only. See [Configs.md](Configs.md) Oct 9 and [0.TradeProfiles.md](0.TradeProfiles.md).

**Last updated:** November 6, 2026 (local runtime pack not on serving: this-tick bid pret + HOLD, leftover OCC skip while owned, discovery scan vs watch, notification-only Telegram, 5s PATH_V2 safety — [§7](#7-hold--monitor--path-refresh)) · November 5, 2026 (serving `00625-wwv`: sequential OCC + UL yield, reusable-gated OCC defer, eval JSONL engine/qty/fee keys; rollback `00624-8bn` — [§7](#7-hold--monitor--path-refresh)) · November 4, 2026 (in `00624-8bn`: `_monitor_loop` pause + empty-book force-flag clear; `u0dte_<ms>` tick ids; UL labels reset each tick; JSONL 5s / daily 45s; locked `_LINE_COUNTS`; PATH_V2 restore keeps entry time / live HWM — [§7](#7-hold--monitor--path-refresh); PATH_V2 eval clock: `eval_now` / `eval_age_s` on each evaluated JSONL row; one eval per `(trade, tick)` with HOLD reuse; replay uses `eval_now`; path `timestamp` = monitor clock, `broker_quote_timestamp` separate; one scored freeze + matching `po_profile_decision` digest; one `max_age_by_source` table 60/120/45/90; stale gated key waits after one refresh; owned UL 2s loop, age from the stored clock; stale chain `*_now` empty — [§5.3](#53-time-correct-data-for-trade-profile-decisions) · [§7](#7-hold--monitor--path-refresh); Save ON CALL-only) · November 2, 2026 (calendar skip is computed `should_skip_trading` — previous **session**, no double Day Before — [Market calendar data](#market-calendar-data); Wide `equity_volume_ratio` ≥ **0.012** all cells; cache with no two-sided quotes `force_live` `no_two_sided_quotes`; picker `no_quote` on a stale cache is `data_wait:chain_quotes`; listed-expiry identity: fill OCC follows `selected_expiry`, never remap a later listed payload onto session today; 1002 spelling recovery is skipped when that day is memo-empty; OCC repair may rewrite a same-day stamp to the fill hint / `nearest_for` listed day — [Broker](#broker-authoritative); local until `deploy now`) · October 30, 2026 (PO persist clocks: bar-close session tech, executed freeze lock, first_red live OCC age, FILL_PCT skip, OCC greeks, stale/no-read chain null, owned 5m from OCC samples, one flatten bar prime — [§8](#8-priority-optimizer-snapshots); local until `deploy now`) · October 27, 2026 (owned-tick flight JSONL: PVE3/BB/VWAP-ATR/`otm_pct`/shares/tape dir%/L1 from cache — [§7 Monitor](#7-hold--monitor--path-refresh); local until `deploy now`) · October 27, 2026 (desk writer: `00611-9zz` still fill/close only; local `desk_snapshot_pulse` adds 1.0s off-thread open-book marks until `deploy now`) · October 26, 2026 (single chain door `fetch_0dte_intent_chain`; same-day empty memo; local until `deploy now`) · October 24, 2026 (serving `00611-9zz`; Cloud Run writes `desk_public/snapshot.json` + `head.json`) · October 23, 2026 (50p snapshot retired; remaining families: handoff, executed, 20p, age_120s, first_red, pullback, exit) · October 22, 2026 (Mon–Thu `optionexpiredate` first so empty Thursday does not 10031; same-day if listed else one nearest chain; local until `deploy now`) · October 21, 2026 (same-day 0DTE first, then earliest listed expiry via `optionexpiredate` + empty-day memo; local until `deploy now`) · October 19, 2026 (local schema v8: same 267 densified keys plus mid-trade `lifecycle_event`; eight research families — handoff, executed, 20p, age_120s, first_red, mfe_50p, pullback, exit) · October 13, 2026 deployed `easy-etrade-strategy-00599-lhd`: single Trade Profile admit `wide`, chain fetch manager, dir% defer, cap-first `max_open_positions_reached`, 50 caps, dynamic calendar `MARKET_CLOSED` / `LOW_VOLUME` / `PRE_HOLIDAY` · admit is the single Trade Profile (TP Wide) since 10/13; `wide_collect` 10/9–10/13 (Trade Profile observe-only; before 10/9 the Trade Profile was the admit) · long-used keys are stamped from the session-tech hub and `central_tech` · `dynamic_volume` is reserved for rolling volume and open-interest keys that are not built yet · Priority Optimizer records snapshots and is not the live tech path · decision classifications (`VALID` / `NOT_READY` / `DATA_ERROR` / `NON_APPLICABLE`) · a skipped chain fetch holds the break · GCS `research_app/` · schema 8 · 267 keys  
**Schema:** `SCHEMA_VERSION=8` · `FEATURE_KEYS_0DTE_UNIFIED` **267** in `SNAPSHOT_SCHEMA_ORDER` (stable) · mid-trade envelopes add `FEATURE_KEYS_LIFECYCLE_EVENT` (not densified) · research rank is `USEFULNESS_ORDER` · `FEATURE_KEYS_ENTRY_EXIT_CRITICAL` **107** · `FEATURE_KEYS_ORB_SO` **85**

**Purpose:** Go-to reference for Easy 0DTE **data sources, the twenty Trade Profile keys, runtime caches, broker chain feeds, technical keys, volume kinds, trust flags, gate wiring, path refresh, Priority Optimizer snapshots, and restart-critical persistence**.

**Related docs:** [0.TradeProfiles.md](0.TradeProfiles.md) · [Configs.md](Configs.md) · [Cloud.md](Cloud.md) · [PositionMonitoring.md](PositionMonitoring.md) · [0DTEQualityGates.md](0DTEQualityGates.md) · [0DTERisk.md](0DTERisk.md#22-quality-gate-profitable-trade-settings) · [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) · [0DTETrendline.md](0DTETrendline.md) · [0.POTemporalValidationFillSuccess.md](0.POTemporalValidationFillSuccess.md) · [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md)

---

## Table of Contents

1. [Authority & Do-Not-Mix Rules](#1-authority--do-not-mix-rules)
2. [Data Sources & Broker Policy](#2-data-sources--broker-policy)
3. [Volume Kinds Catalog](#3-volume-kinds-catalog)
4. [Session Tech Hub](#4-session-tech-hub)
5. [Chain Skew · dir% · pc_oi](#5-chain-skew--dir--pc_oi)
5.1. [Admit step](#51-quality-gate-admit-step)
5.2. [Trade Profile keys and runtime caches](#52-trade-profile-keys-and-runtime-caches)
5.3. [Time Correct Data for Trade Profile decisions (decision freeze)](#53-time-correct-data-for-trade-profile-decisions)
6. [Tech Keys at Gates](#6-tech-keys-at-gates)
7. [Hold · Monitor · Path Refresh](#7-hold--monitor--path-refresh)
8. [Priority Optimizer Snapshots](#8-priority-optimizer-snapshots)
9. [Watchlist & ORB Capture](#9-watchlist--orb-capture)
10. [GCS · Persistence · Secrets · Telemetry](#10-gcs--persistence--secrets--telemetry)
11. [Appendix A — Technical Key Priority Catalog](#appendix-a--technical-key-priority-catalog)
12. [Appendix B — Legacy Notes](#appendix-b--legacy-notes)

---

## 1. Authority & Do-Not-Mix Rules

### Canonical modules

| Role | Path |
|------|------|
| Volume catalog | `modules/volume_data.py` |
| Session tech hub | `modules/session_tech_indicators.py` |
| Entry chain skew | `easy0DTE/modules/convex_chain_skew.py` |
| Path / review skew | `profit_optimizer/path_chain_skew.py` |
| Quality Gate Stack | `easy0DTE/modules/quality_gate_stack.py` |
| Trade Profile admit | `easy0DTE/modules/trade_profile.py` |
| Mined profile rules | `easy0DTE/modules/greedy_mined_profile_rules.csv` |
| Live option chain door | `easy0DTE/modules/options_chain_manager.py` `fetch_0dte_intent_chain` (only app HTTP+expiry entry) |
| Chain budget / queue | `central_tech/chain_fetch_manager.py` (P0–P3; then calls the door) |
| Empty / nearest expiry memo | `modules/option_expiry_memo.py` |
| Live option chain impl | `easy0DTE/modules/options_chain_manager.py` (`chain_cache`, TTL 300s) |
| Chain HTTP | `easy0DTE/modules/etrade_options_api.py` (no expiry policy) |
| Equity spots / candles | `modules/prime_data_manager.py` (no option chains) |
| Pre-order contract seal | `modules/po_preorder_seal.py` (after the admit; does not re-score) |
| Decision classification | `easy0DTE/modules/decision_required.py` (`VALID` · `NOT_READY` · `DATA_ERROR` · `NON_APPLICABLE`; zero is VALID) |
| QG / SS admit hydrate | `modules/session_tech_admit.py` (`stamp_admit_tech`) and `central_tech/` |
| Watch FSM persist | `modules/discovery_watch_fsm_persist.py` |
| Convex eligibility | `easy0DTE/modules/convex_eligibility_filter.py` |
| HardGate | `easy0DTE/modules/prime_0dte_strategy_manager.py` |
| PO schema / densify | `modules/po_feature_schema.py` · `modules/po_snapshot_hydration.py` |
| Path samples | `profit_optimizer/trade_flight_recorder.py` · `path_jsonl.py` · `trading_hooks.py` (`stamp_live_rsi`, `stamp_owned_monitor_review_keys`) · `modules/owned_tick_observe.py` |
| App GCS namespace | `modules/runtime_mode.py` (`gcs_namespace()`) |
| Market API lane pacing | `modules/broker_api_pacing.py` (`amarket_api_request_slot` · open exact OCC pri 0) |

### Do not mix

| Wrong | Right |
|-------|-------|
| `orb_volume` / contract `.volume` / Yahoo daily vol into equity hard gate | Hub `volume_ratio` + `volume_ratio_missing` |
| `day_seed` / `intraday_15m` / `pad_anchor` volumes into `volume_ratio` | Usable minute quote-delta bars only |
| Treating `direction_volume_pct` as OI | `put_call_oi_ratio` is OI; dir% is **chain volume** share |
| Treating `representative_volume` as monitor depth | `cumulative_volume` is exact-contract quote depth |
| Treating invent `volume_ratio=1.0` / legacy `rsi=50` as real | Check `volume_ratio_missing` / `rsi_missing` / `macd_missing` first |
| Raw `ETradeOptionsAPI.fetch_options_chain` / SelectedED from an executor or Data Manager | `fetch_0dte_intent_chain` only. Data Manager is equity spots |
| Treating TL A1 time as always 06:30 PT | That is only a **pre-backfill placeholder**. After Yahoo 1m, expect `orb_*_ts_source=structure_1m_match` and A2 from the binding 1m bar |
| Multi-day Yahoo VWAP/MACD for session gates | Session hub (`tech_source=session_1m_rth`) — broker quote minutes + optional Yahoo RTH 1m **history** backfill |
| Entry-meta ATR / Bollinger / `rs_vs_spy` on path ticks | Current session bars + live broker RS |

### Invent / trust doctrine

Placeholders are allowed **only** with trust flags. **Aug 4:** never stamp invent RSI-50 / MACD-0 into PO or gate rows as if trusted:

- `volume_ratio=1.0` + `volume_ratio_missing=true` (gate fail-closed / soft-skip uses the **flag**)
- `rsi=null` + `rsi_missing=true` (do **not** invent `50`)
- `macd_histogram=null` + `macd_missing=true` (do **not** invent `0.0`)

`apply_session_tech` / `_stamp_session_tech_on_po_row` **clear** invent RSI/MACD when missing flags are set. Feature extract (`execution_feature_snapshot`) ignores invent values when `*_missing`. Gates and research must read flags **before** comparing numeric floors.

---

## 2. Data Sources & Broker Policy

### Broker-first doctrine (read this first)

The Easy 0DTE apps are designed as **broker data only** for trading truth: quotes, ORB/ORB2 **prices**, option chains, fills, account state, and execution. Default today: E*TRADE (`BROKER=etrade`, `BROKER_DATA_ONLY=true`). Roadmap adapters: Interactive Brokers, TastyTrade, Charles Schwab — same doctrine when certified.

**Third-party (yfinance) is not a quote fallback.** It is an **approved, bounded exception** for **intraday candle timelines** that E*TRADE does not expose on request for the full 118-symbol 0DTE list. Without 1m (or 5m) bars we cannot place A1/A2 at the correct minutes, so Trendline **slope geometry** would be wrong even when broker ORB2 high/low **prices** are correct.

All production Yahoo 1m structure requests now route through `YahooBackfillManager` in `modules/prime_data_manager.py` when `YAHOO_BACKFILL_MANAGER_ENABLED=true`. The manager sits **behind** the existing prime data manager boundary: strategy code asks for TL0, TL1, watch, or session-tech bars; the manager coalesces, caches, paces, and backs off before the underlying batched `yf.download` helper runs.

**After Yahoo 1m backfill (production path):**
- **TL1** — 1m data identifies the correct **A1** minute (ORB2 H/L print) **and** the **A2** bar → correct TL1 slope
- **TL2** — watch 1m backfill identifies correct **A1** (ORB2 opposite edge @ verified ts) **and** **A2** → correct TL2 slope
- **TL3** — watch 1m backfill identifies correct **A1** session-extreme timestamp **and** **A2** → correct TL3 slope
- **TL4** — same watch-managed 1m path supplies the later ORB2-extension structure when the extension slot is eligible

Drawn lines do **not** use 06:30 as A1 once that backfill succeeds.

| Must stay broker | May use Yahoo (yfinance) 1m RTH |
|------------------|----------------------------------|
| Equity last / OHLC session prices for gates | Session-tech **history depth** when quote minutes are RSI-thin |
| ORB2 **high/low prices** (A1 price) | TL1 **A1 + A2 timestamps** (match / hull on 1m bars) |
| Option chains, OCC, fills, monitor quotes | TL2/TL3/TL4 **A1 + A2** when watch cache needs 1m backfill |
| Trusted `volume_ratio` | Never — volume stays broker minute deltas |

When a future broker supplies requestable 1m/5m equity candles for the full list, **prefer broker bars** for structure; keep Yahoo as timestamps-only supplement. **Do not** use Yahoo quote ticks or synth A2 for A1/A2. Cross-ref: [0DTETrendline.md — A1/A2 timestamp authority](0DTETrendline.md#a1-a2-timestamp-authority).

### Broker (authoritative)

- Default: E*TRADE (`BROKER=etrade`, `BROKER_DATA_ONLY=true`).
- Target broker roadmap: E*TRADE, Interactive Brokers, Charles Schwab, TastyTrade. Only E*TRADE is production-complete today; the other brokers are selectable/planned and must fail closed until their adapters, data providers, and credential flows are certified.
- Equity quotes: batch (25/call) — `last` / OHLC / `totalVolume` / `dayVolume` / `change_pct`.
- Options chains: call/put contract `volume` + `open_interest` → chain totals / ratios.
- ORB levels, prices, execution, exact OCC exit quotes: broker only.
- **E*TRADE gap:** session quote OHLC gives ORB2 H/L **prices** at freeze (~7:30) but **no candle timeline**. Broker ingest may write a temporary **06:30** placeholder for `orb_*_extreme_ts`; **TL1 Yahoo 1m backfill replaces that with the real A1 minute** before lines are drawn.
- Contract selection for **ORB 0DTE, Trendline, and Breakout** (single door: `fetch_0dte_intent_chain` → `OptionsChainManager`; `chain_fetch_manager` budgets discovery/dir% then calls that door; **PrimeDataManager is equity spots only**): **same-day 0DTE is the default when the broker lists it**. Fast path (`0DTE_EXPIREDATE_RESOLVE_FIRST=weekday`, default): Mon–Thu one `optionexpiredate` list, then **one** chain for today if listed else the earliest later day (2DTE / 3DTE / 4DTE / weekly) within `0DTE_NEAREST_EXPIRY_MAX_DAYS`. Friday stays same-day-first so a true 0DTE open does not pay an extra list call. Do not walk Thursday→Friday with 10031. Empty days and the chosen nearest expiry are session-memoized (`modules/option_expiry_memo.py`). After a same-day miss, later scans skip that day (Mon–Wed session TTL, Thu–Fri 45m) and reuse the memoized weekly cache instead of re-probing 0DTE. Do **not** skip a valid signal solely because same-day contracts are absent. **Identity:** `contract.expiry`, fill OCC, and the exact-quote wire are the **listed** `selected_expiry` (today or nearest). Never stamp session today onto a later listed payload when SelectedED is missing or echoes the request (`CHAIN_EXPIRY_NEAREST_FROM_PAYLOAD`). Monitor repair does not walk calendar days; if the stamped OCC day is memo-empty it fetches that one listed day (`selected_expiry_ymd` on the fill, else `nearest_for`). E*TRADE `1002` on a memo-empty day is “not listed,” not a spelling miss — skip `EXACT_CONTRACT_QUOTE_1002_RECOVERY` and quarantine (`EXACT_CONTRACT_QUOTE_HARD_FAIL`). No raw `ETradeOptionsAPI` / SelectedED fallback. ORB SO is not an options path and does not use this selector.

### Market API lane pacing (E*TRADE)

All Market-module quote/chain calls share `modules/broker_api_pacing.py` so open-position exact OCC exits are not buried behind discovery / equity batch / chain enrich.

| Rule | Detail |
|------|--------|
| **Throttle** | Default `ETRADE_MARKET_API_INTER_REQUEST_SEC=0.28` (~3.5 req/s under the 4/s Market API limit) |
| **Batch size** | `ETRADE_BATCH_QUOTE_SIZE` capped at **25** |
| **Priority** | `open_exact_occ` (0) → recovery / broker sync → PO snapshot → watch equity / TL build → pc_oi / other |
| **Gate** | `amarket_api_request_slot` acquires off-loop (`asyncio.to_thread`); **Sep 4:** holder stack recomputes active priority on release (fixed stale high-pri after open-exact leave) · release **before** pace sleep so peers are not starved by the 0.28s throttle |
| **Telemetry** | `BROKER_LANE_COUNTERS` · `BROKER_LANE_DEFERRAL` — Sep 4 Demo soak under load showed ~11k deferrals / 700 reqs on the pre-fix revision |

Owned exact OCC (`lane=open_exact_occ`) is priority 0 on the pacer. The 24-name discovery scan and the ORB1, Trendline, and ORB2 watches still run when that quote is slow. Cross-ref: [Configs.md](Configs.md) · [PositionMonitoring.md](PositionMonitoring.md).

### Data plane contract

| Plane | Authority today | Future broker rule | Third-party allowed? |
|-------|-----------------|--------------------|----------------------|
| Account, buying power, positions, order status | E*TRADE | Selected broker/account slot | No |
| Option chains, OCC contract metadata, fills, exact-contract monitor quotes | E*TRADE | Selected broker/account slot | No |
| ORB2 box prices / A1 **price** | E*TRADE session high/low at ~7:30 | Selected broker market-data plane | No |
| ORB2 A1 **timestamp** (extreme minute) | Yahoo 1m match to broker H/L (`structure_1m_match`) — **correct A1 time for drawn TL** | Prefer broker 1m candles when available | Yes — **time only**; prices stay broker |
| Equity last/OHLC/session volume for gates | E*TRADE batch quotes + quote-minute deltas | Selected broker market-data plane | No fallback for trusted volume |
| Session technical depth (RSI/MACD/VWAP history) | Broker quote minutes, optionally Yahoo 1m backfill | Broker minutes first; approved historical supplement only when thin | Yes, bounded history only |
| Trendline A2 / TL2 / TL3 / TL4 structure path | Yahoo RTH 1m where broker lacks candle timeline | Prefer selected broker 1m candles when available; otherwise approved historical supplement | Yes, bounded structure only |

### Why yfinance for Trendline geometry (implementation)

1. **ORB2 freeze** — E*TRADE session H/L → broker `orb_high` / `orb_low` (prices). Extreme ts may temporarily be 06:30 until step 3.
2. **TL1 Yahoo 1m backfill** — managed batched `yf.download` [06:30–07:30] (`TRENDLINE_BUILD_YF_1M_*`) for the ORB2 universe (~20 symbols/chunk).
3. **Correct TL1 A1** — `refine_orb2_extreme_timestamps_from_bars` matches 1m high/low to broker ORB2 prices → stamps the **real A1 minute** (`structure_1m_match`). **Does not change prices.**
4. **Correct TL1 A2** — same 1m path feeds `_fit_structure_envelope_anchor_two` (A2 ts = binding bar) → **correct TL1 slope**.
5. **Correct TL2 / TL3 / TL4 A1+A2** — watch hydrate (`TRENDLINE_WATCH_YF_1M_*`) before inject when thin / stale / near break: TL2 uses verified ORB2 opposite-edge A1 + hull A2; TL3/TL4 use extension/session-extreme structure bars + hull A2 → **correct slopes**.

**Degraded only if backfill fails:** Yahoo off or no price match → temporary 06:30 / thin seed may remain. Log: `orb2_ts_refined`, `tl1_structure_yf_1m`, `yf_1m_structure`.

### YahooBackfillManager (third-party request manager)

`YahooBackfillManager` is the required third-party request manager for Yahoo 1m RTH backfills. It does **not** authorize new Yahoo data uses; it only manages approved allow-list uses so the 10-second discovery cadence and 118-symbol watchlist do not stampede Yahoo or the app event loop.

| Managed lane | Async entry point | Priority | Cache TTL | Purpose |
|--------------|-------------------|---------:|----------:|---------|
| TL0 build | `_yf_tl0_structure_1m_bars` | 1 | `YAHOO_BACKFILL_BUILD_CACHE_TTL_SEC` | ORB1/TL0 structure [06:30–06:45] |
| TL1 build | `_yf_tl1_structure_1m_bars` | 1 | `YAHOO_BACKFILL_BUILD_CACHE_TTL_SEC` | ORB2/TL1 structure [06:30–07:30] |
| TL watch / near-break | `_yf_trendline_watch_intraday_bars(..., priority=2)` | 2 | `YAHOO_BACKFILL_WATCH_CACHE_TTL_SEC` | Thin / stale / near-ORB2-break refresh |
| TL2 / TL3 / TL4 force-inject | `_yf_trendline_watch_intraday_bars(..., priority=0, cache_ttl_sec=0)` | 0 | **0** (live fetch) | Hydrate-before-inject; must include bars after 07:30 PT |
| Session-tech thin RSI backfill | `_yf_trendline_watch_intraday_bars(..., priority=3)` | 3 | `YAHOO_BACKFILL_WATCH_CACHE_TTL_SEC` | RTH history depth under broker quote minutes |

Manager guarantees:

- **Coalescing:** concurrent requests with the same date/window/interval/batch config wait briefly (`YAHOO_BACKFILL_COALESCE_MS`) and dispatch once for the unique symbol set.
- **In-process cache:** recent per-symbol bars are reused by lane/window so repeated 10-second scans do not refetch the same structure immediately. **TL2/TL3/TL4 force-inject uses `cache_ttl_sec=0`** so a first fetch that still ends at 07:30 cannot block post-07:30 bars for the watch TTL.
- **Global pacing:** every actual `yf.download` batch passes the process-wide `YAHOO_BACKFILL_MAX_DOWNLOADS_PER_MIN` limiter, including legacy blocking callers.
- **Bounded concurrency:** `YAHOO_BACKFILL_MAX_CONCURRENT_DOWNLOADS` caps active Yahoo dispatches. Default is `1` to keep Cloud Run load predictable.
- **Priority:** recorded on each request and kept as the min when coalescing the same lane. With concurrency=1, dispatch is FIFO by flush-task start. The 06:45 / 07:30 scheduler still runs TL0/TL1 builds before watch hydrate.
- **Backoff:** slow, empty, or failed downloads trigger `YAHOO_BACKFILL_SLOW_MS` / `YAHOO_BACKFILL_BACKOFF_*` pressure handling.
- **Telemetry:** Cloud logs use `YAHOO_BACKFILL_MANAGER | action=fetch_return|dispatch_done|pressure|backoff_wait|pace_wait`.

The underlying download remains `_yf_batch_intraday_rth_bars_blocking`: `yf.download`, `group_by="ticker"`, `threads=False`, `prepost=False`, chunked by the configured batch size. The manager is an efficiency and protection layer, not a new pricing authority.

### Session bars (central hub — all paths)

Today’s RTH **1m** bars live in `system._session_tech_bar_cache` via `ensure_session_tech_bars` / `hydrate_equity_session_tech`:

1. **Broker quote minutes** — cumulative volume deltas (`equity_session_volume_from_quote`) → trusted `volume_ratio` when ≥2 usable minute deltas  
2. **`day_seed`** — broker day OHLC when &lt;2 bars (VWAP/RSI depth only — **never** trusted volume)  
3. **Bounded Yahoo RTH 1m backfill** — when priced closes &lt; **15** (RSI depth), managed `_yf_trendline_watch_intraday_bars(..., priority=3)` → `_yf_batch_intraday_rth_bars_blocking`; seeded **under** live broker minutes; session-tech tick cap **12** symbols  

**Trendline structure path (separate from session-tech volume):** ORB2 **box prices** stay broker session H/L. Yahoo 1m backfill supplies **correct A1+A2 timestamps** for TL1 at build and for TL2/TL3/TL4 at watch hydrate-before-inject (`TRENDLINE_BUILD_YF_1M_*` / `TRENDLINE_WATCH_YF_1M_*`). Empty TL1 still seeds `_bar_cache`. Synth is last-resort only.

Prices / ORB levels / chains / execution remain **broker-only**.

### Yahoo — allow list only

| Use | Default | Config / gate | What it may influence | What it must not influence |
|-----|---------|---------------|-----------------------|----------------------------|
| Quote / price fallback | **Forbidden** | `BROKER_DATA_ONLY=true` | Nothing | Entries, exits, ORB levels, option quotes |
| Session-tech RTH 1m **history** when quote minutes are RSI-thin | **On** | `0DTE_SESSION_TECH_YF_1M_BACKFILL=true` | RSI/MACD/VWAP depth, seeded under broker minutes | Trusted `volume_ratio`; broker price truth |
| TL1 structure 1m [06:30–07:30] PT | **On** | `TRENDLINE_BUILD_YF_1M_BACKFILL=true` | **Correct TL1 A1 + A2 timestamps** / hull | ORB2 high/low **price**, A1 **price**, options chain |
| TL watch / TL2–TL4 structure when `_bar_cache` thin | **On** | `TRENDLINE_WATCH_YF_1M_BACKFILL=true` | **Correct TL2/TL3/TL4 A1 + A2** before inject | Replacing denser broker/TL1 bars; ORB box prices |
| Daily historical TA last resort | **Off** | `ETRADE_ALLOW_YAHOO_HISTORICAL=1` only | Legacy comprehensive TA fallback only | Session hub authority |
| Index RTH override symbols | **Empty** | `ORB_RTH_INTRADAY_SYMBOLS` (e.g. VIX) if explicitly set | Explicit index RTH slice only | Equity quote fallback |

Yahoo calls must stay managed, batched/chunked (`yf.download`, `threads=False`) and bounded by symbol caps, pacing, cache TTLs, and backoff. Review log markers: `YAHOO_BACKFILL_MANAGER`, `TL0_STRUCTURE_YF_1M`, `TL1_STRUCTURE_YF_1M`, `TRENDLINE_PIPELINE | stage=build_fetch | action=tl1_structure_yf_1m`, `orb2_ts_refined` / `structure_1m_match`, `TRENDLINE_PIPELINE | stage=watch_hydrate | action=yf_1m_structure`, and `0DTE_SESSION_TECH_YF_1M`.

### Key env switches

| Env | Default | Meaning |
|-----|---------|---------|
| `0DTE_CONVEX_SESSION_TECH` | true | Master session-tech switch (ORB / TL / BO) |
| `0DTE_SESSION_TECH_YF_1M_BACKFILL` | true | Yahoo RTH 1m backfill when closes &lt; 15 |
| `0DTE_SESSION_TECH_MAX_AGE_SEC` | 90 | Hub bar TTL |
| `TRENDLINE_BUILD_YF_1M_BACKFILL` | true | TL1: Yahoo 1m → **correct A1 + A2 timestamps** [06:30–07:30] |
| `TRENDLINE_BUILD_YF_1M_MAX_SYMBOLS` | 150 | Build-time cap for TL0/TL1 Yahoo structure universe (covers full 118-name `0dte_list`) |
| `TRENDLINE_BUILD_YF_1M_BATCH_SIZE` | 20 | Symbols per `yf.download` |
| `TRENDLINE_BUILD_YF_1M_BATCH_SLEEP_SEC` | 0.5 | Pause between Yahoo build chunks |
| `TRENDLINE_WATCH_YF_1M_BACKFILL` | true | Watch 1m backfill → **correct TL2/TL3/TL4 A1 + A2** when thin / stale / near break |
| `TRENDLINE_WATCH_YF_1M_MIN_BARS` | 15 | Thin threshold before watch Yahoo merge |
| `TRENDLINE_WATCH_YF_1M_MAX_SYMBOLS` | 150 | Watch hydrate cap for ORB2 universe (covers full 118-name `0dte_list`) |
| `TRENDLINE_WATCH_YF_1M_BATCH_SIZE` | 20 | Symbols per watch `yf.download` chunk |
| `TRENDLINE_WATCH_YF_1M_BATCH_SLEEP_SEC` | 0.5 | Pause between Yahoo watch chunks |
| `TRENDLINE_WATCH_YF_1M_MAX_STALE_SEC` | 180 | Force hydrate when last structure bar older than this |
| `TRENDLINE_WATCH_YF_1M_NEAR_BREAK_BUFFER_PCT` | 0.0010 | Near ORB2 break → prioritize Yahoo hydrate |
| `TRENDLINE_WATCH_HYDRATE_QUOTE_CAP` | 40 | Broker quote hydration cap before TL2+ inject |
| `YAHOO_BACKFILL_MANAGER_ENABLED` | true | Route approved Yahoo structure/session-tech requests through the central manager |
| `YAHOO_BACKFILL_PACING_ENABLED` | true | Enable process-wide pacing for actual `yf.download` batches |
| `YAHOO_BACKFILL_MAX_DOWNLOADS_PER_MIN` | 30 | Sliding-window cap on Yahoo batch downloads |
| `YAHOO_BACKFILL_MAX_CONCURRENT_DOWNLOADS` | 1 | Max concurrent Yahoo dispatches |
| `YAHOO_BACKFILL_COALESCE_MS` | 1200 | Short wait to combine same-window symbol requests |
| `YAHOO_BACKFILL_CACHE_TTL_SEC` | 120 | Default manager cache TTL |
| `YAHOO_BACKFILL_WATCH_CACHE_TTL_SEC` | 120 | Watch/session-tech per-symbol bar reuse TTL |
| `YAHOO_BACKFILL_BUILD_CACHE_TTL_SEC` | 21600 | TL0/TL1 build window reuse TTL for the trading day |
| `YAHOO_BACKFILL_SLOW_MS` | 15000 | Slow-download threshold for pressure/backoff logs |
| `YAHOO_BACKFILL_BACKOFF_BASE_SEC` | 2 | Base backoff when Yahoo slows/fails/empties |
| `YAHOO_BACKFILL_BACKOFF_MAX_SEC` | 20 | Max manager backoff wait |
| `TRENDLINE_ORB2_TS_REFINE_ABS_TOL` | 0.05 | Absolute $ tol matching Yahoo 1m extreme to broker ORB2 price |
| `TRENDLINE_ORB2_TS_REFINE_PCT_TOL` | 0.0002 | Relative tol for same match |
| `ETRADE_ALLOW_YAHOO_HISTORICAL` | 0 | Yahoo **daily** hist opt-in (comprehensive TA last resort — not session hub) |
| `0DTE_PATH_CHAIN_SKEW_REFRESH_SEC` | 45 | Path chain refresh TTL (discovery-async while held) |
| `0DTE_PATH_SESSION_TECH_REFRESH_SEC` | 60 | Path session-tech TTL |
| `0DTE_PATH_*_REFRESH_MAX_PER_TICK` | 2 | Session-tech budget on eval; chain live budget is per discovery |
| `0DTE_PATH_CHAIN_SKEW_REFRESH_MAX_PER_DISCOVERY` | 2 | Live path chain fetches per discovery scan (1 when ≥20 open) |
| `0DTE_QG_CHAIN_CACHE_WHILE_OPEN_SEC` | 600 | Layer C may reuse a cached chain this long while positions are open |
| `0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN` | 2 | Live Layer C chain fetches per discovery scan at `0DTE_QG_LIVE_CHAIN_TIGHT_AT_OPEN` (7)+ opens. Since 10/13 (live `easy-etrade-strategy-00599-lhd`) only when `CHAIN_FETCH_MANAGER_ENABLED=false`; the manager uses `CHAIN_FETCH_SCAN_CEILING=24` |
| `0DTE_PO_CACHE_QUOTE_MAX_AGE_SEC` | unset → **120** (`TP_FREEZE_QUOTE_MAX_AGE_SEC`) | PO hydrate quote reuse. Same table as the gate. Set the env only to override |
| `0DTE_PO_ENTRY_CHAIN_ALLOW_STALE_SEC` | unset → **60** (`TP_FREEZE_CHAIN_MAX_AGE_SEC`) | Entry PO reuses Layer C / in-process chain this long. Older is empty, not scored |
| `0DTE_PO_EXIT_CHAIN_ALLOW_STALE_SEC` | unset → **60** (Shared.env `60`) | Mid-trade / exit reuse a chain stamp this long. Exact OCC stays on the monitor |
| `0DTE_POSITION_MONITOR_INTERVAL_SEC` | 2 | Unified OCC monitor |

---

## 3. Volume Kinds Catalog

**Request through** `modules/volume_data.py` so callers know which kind they use.

| Kind | Keys | Request API | Use for |
|------|------|-------------|---------|
| Equity session tech | `vwap`, `rsi`, `macd*`, `volume_ratio`, `*_missing`, ATR/BB/mom, `tech_source` | `hydrate_equity_session_tech` → `request_equity_session_tech` → `apply_equity_session_tech` | Underlying session stamp |
| Equity session ratio | `ratio`, `missing`, `source` | `resolve_equity_volume_for_gate` | HardGate / QGS / TL / BO equity vol |
| Session vs ADV (sva) | `session_vs_avg_volume` / `session_vs_avg` | Cached 1m bars + quote `average_volume` (`stamp_admit_tech`) | Skip Signals / suggested QG — **not** a new broker fetch |
| Quote cumulative | `totalVolume` / `dayVolume` | `equity_session_volume_from_quote` | Minute-bar builder **only** |
| Chain side | `call_chain_volume`, `put_chain_volume`, ratios, OI sides | `chain_side_volumes_from_chain` | Tape / skew |
| Direction volume % | `direction_volume_pct` | `direction_volume_pct_from_signal` | Convex / QGS / Hold |
| pc_oi (OI, not volume) | `put_call_oi_ratio` | Chain skew / Convex helper | Call-vs-Put OI confirmation |
| ORB window | `orb_volume` | ORB capture | Structure telemetry |
| Contract liquidity | contract `.volume` | Entry liquidity | Strike pick — not equity gate |
| Research bundle | `VOLUME_RESEARCH_KEYS` (17) | `ensure_volume_research_fields` | PO densify / entry cache |

### Volume key doctrine (quick)

| Key | Meaning |
|-----|---------|
| `volume` | Underlying share volume on ORB/underlying rows (overloaded historically) |
| `volume_ratio` / `orb_volume_ratio` | Equity participation from hub; gate only when `volume_ratio_missing=false` |
| `session_vs_avg_volume` (sva) | Session volume ÷ quote `average_volume`; Skip Signal / QG key at **admit** |
| `call_chain_volume` / `put_chain_volume` | Broker options-chain side volume |
| `put_call_volume_ratio` | Put chain vol ÷ call chain vol |
| `direction_volume_pct` (`dir%`) | Same-side chain vol share (CALL→call/total; PUT→put/total) |
| `put_call_oi_ratio` | Put OI ÷ call OI — **not** dir% |
| `representative_volume` | Selected / representative contract volume at enrich |
| `cumulative_volume` | Exact owned-contract quote cumulative (monitor / path) |
| `orb_volume` | ORB-window underlying volume |
| `exec_volume_ratio` | ORB SO telemetry only |

`dir%` and `pc_oi` are **not duplicates** — volume tape vs open interest.

---

## 4. Session Tech Hub

**Path:** `ensure_session_tech_bars` → `get_session_tech` (live) or `get_session_tech_asof` / `read_session_tech_at` (PO persist) → `apply_session_tech`  
**Callers:** ORB Convex (`apply_session_tech_for_convex`) · Trendline (`hydrate_equity_session_tech` + `_hub_session_tech`) · Breakout (`hydrate_breakout_session_bars` → same hub) · admit (`stamp_admit_tech`) · live Path V2 / flight JSONL (`get_session_tech`, current hub including the forming minute) · PO snapshot copy (`_stamp_session_tech_on_po_row` — when the row has `po_snapshot_event_at`, bars whose minute close is ≤ that event)  
**Provenance:** `tech_source` / `volume_ratio_source` / `vwap_source` = `session_1m_rth`  
**As-of:** `session_tech_asof_at` is the minute close (stamp floored to the minute + 60s). A future session-tech clock on a PO row nulls RSI/MACD/Bollinger/VWAP with the clock (`stamp_provenance_clocks`). Missing clock with values still present is a gap, not a pass.

### Computed from current 1m bars

| Field | Rule |
|-------|------|
| `vwap`, `vwap_distance_pct` | Session VWAP; `vwap_missing=false` when stamp exists |
| `volume_ratio` | Usable minute vols (≥2); else `1.0` + `volume_ratio_missing=true` (gates use the flag) |
| `rsi` | Needs ≥15 closes; else **`null`** + `rsi_missing=true` (no invent 50) |
| `macd` / `macd_signal` / `macd_histogram` | Needs ≥26 closes; else **null** + `macd_missing=true` (no invent 0) |
| MACD pack | `macd_above_zero`, `macd_cross`, `macd_hold_ok_call`, `macd_exit_signal_call`, `macd_hist_sign` |
| `sma_20` / `sma_50` / `ema_12` / `ema_26` | Session closes |
| `atr`, `momentum_10`, Bollinger u/m/l/width | Same bars (null when thin) |
| `session_bar_count` | Bars behind the stamp |

`apply_session_tech` also sets `orb_volume_ratio` alias, mirrors trust flags into `metadata`, and **clears** invent RSI/MACD on dict and object targets when missing. E\*TRADE comprehensive / fallback must **not** re-inject invent 50/0/1.0 over hub authority (`data_quality=placeholder` refused).

---

## 5. Chain Skew · dir% · pc_oi

### Entry writer — `convex_chain_skew`

Stamps: `put_call_oi_ratio`, `put_call_volume_ratio`, `call/put_chain_volume`, `call/put_chain_open_interest`, plus ATM **representative IV / |delta| / strike** of the side (`representative_iv` / `implied_volatility` / `chosen_delta` / `otm_pct` via `setdefault` — does not overwrite a later chosen-contract IV/delta/otm).

**One chain fetch at Layer C** feeds dir%, admit IV, ATM |delta|, strike, and `otm_pct`. Do **not** fetch a second chain for Skip Signals.

<a id="51-quality-gate-admit-step"></a>
### 5.1 Admit step (after signal, before enter/skip)

Runs inside `confirm_chain_skew_before_execution` (`easy0DTE/modules/quality_gate_stack.py`). `confirm_dir_pct_before_execution` is the same function. This is the enter-or-skip decision for every ORB1, Trendline, and Breakout signal. The stack is `trade_profile`. Call SS, Put SS, and Path SS are not on the stack. Dir% share, put/call open-interest ratio, equity volume ratio, and the contract spread are not a second admit. The order path still last-looks the contract spread.

| Order | What | Source | Persist |
|-------|------|--------|---------|
| 1 | `stamp_admit_tech` | Cached RTH 1m bars + last equity quote + the session-tech L1 ring. Five-minute chain deltas and option L1 come from `central_tech`. No chain fetch. | Keys on the signal row |
| 2 | `QG_ADMIT_REBUILD` when local bar stamps are still missing | Bars already in `_session_tech_bar_cache`, else one-symbol `hydrate_equity_session_tech` | Same |
| 3 | `collect_qg_admit_snapshot` | Compact JSON of `QG_ADMIT_KEYS` | `row["qg_admit"]` · signal metadata · `setup_payload` · **`watch_fsm`** in `{ns}/daily_markers/{date}.json` |
| 4 | `enrich_signal_with_chain_skew` | **One** chain read through `chain_fetch_manager` → `fetch_0dte_intent_chain`. Yields to the 2s monitor first. Uses `OptionsChainManager.chain_cache` when the age is inside `cache_ttl` (300s) or `0DTE_QG_CHAIN_CACHE_WHILE_OPEN_SEC` (600s) while positions are open. With the manager on, the live scan ceiling is `CHAIN_FETCH_SCAN_CEILING` (24); `0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN=2` is manager-off only. | Call/put chain volume and open interest, dir%, representative IV, ATM delta, strike, `otm_pct` |
| 4b | restamp `stamp_admit_tech` + re-collect `qg_admit` | Same cached bars/L1 plus the chain fields just merged | `qg_admit` includes the chain fields when the chain returned them |
| 5 | `chain_attempt` | `cached` or `fetched` when a side volume is present. `empty` when the read returned no volume. `skipped_qg_live_budget` when there is no usable cache and this scan already used its live pulls. | On the row |
| 6 | `evaluate_chain_skew_from_row` | `trade_profile` | `po_profile_decision` and `quality_gate_report` |
| 7a | **Pass** | Continue | Handoff snapshot, then the order. The pre-order seal reads the selected contract and does not re-score the profile. |
| 7b | **Reject** | Stop | No order. The decision record stays on the row. |
| 7c | **Not ready** | Same break, held | `required_data_not_ready` when the chain was not fetched and same-side chain volume is absent. Zero after a completed fetch is scored. |

`QG_ADMIT_KEYS`: `session_vs_avg_volume` / `session_vs_avg` · `implied_volatility` / `iv` / `representative_iv` · `seconds_since_break` · `direction_volume_pct` · `vwap_distance_pct` · `vwap_position` · `vwap_with` · `volume_ratio` · `rsi` · `bollinger_position` · `l1_delta_5s_with` · `underlying_l1_imbalance_delta_5s` · `volume_vs_prior_1m` · `average_volume` · `trendline_slot` · `break_timestamp` · `close_at_break` · `material_break_at` · `break_distance_atr` · `price_volume_efficiency_1m` / `_5m` · `entry_bar_body_pct` · `entry_bar_upper_wick_pct` / `entry_bar_lower_wick_pct` · `entry_bar_close_position` · `wick_against` · `close_with` · `otm_pct` · `delta` / `chosen_delta` · `representative_delta` · `representative_strike`.

**Notes:** Compact `qg_admit` is collected before the chain read and again after the merge. The Trade Profile sees the post-chain row. Session bars and the L1 ring stay in process. After hydration, each required input is **VALID**, **NOT_READY**, **DATA_ERROR**, or **NON_APPLICABLE**. Zero is VALID. A skipped chain fetch with no cache is **NOT_READY** and holds the break. An empty completed fetch does not invent contracts. ATM delta, strike, and `otm_pct` come from that same chain object. The pre-order seal of the selected contract happens after this admit and does not change it. Layer C stays on the discovery loop so exact OCC exit quotes keep the Market API lane.

Dir% floors, put/call open-interest ratio, and the old entry-volume floors (CALL 170,000 / PUT 11,000) are not the live admit. Chain volume cuts live inside the Trade Profile. Call SS and Put SS are not on the stack. Skip Signal keys: [Configs.md](Configs.md#absolute-call--put-skip-signals--live-keys). Profile cuts: [0.TradeProfiles.md](0.TradeProfiles.md).

<a id="52-trade-profile-keys-and-runtime-caches"></a>
### 5.2 Trade Profile keys and runtime caches

The twenty keys are `TRADE_PROFILE_KEYS` in `easy0DTE/modules/trade_profile.py`. The order and the groups below are the same list as [0.TradeProfiles.md](0.TradeProfiles.md). Chain participation and chain volume come first. This is not `USEFULNESS_ORDER` and not the 267-key snapshot column order. The admit reads the signal row in this sequence. It does not open a second broker call, and it does not reorder the keys or the groups.

| # | Group | Concept | Canonical key | Filled from |
| --- | --- | --- | --- | --- |
| 1 | Participation | CALL chain volume | `call_chain_volume` | Live E*TRADE chain, or `OptionsChainManager.chain_cache` |
| 2 | Participation | PUT chain volume | `put_chain_volume` | Same chain read |
| 3 | Participation | CALL chain OI | `call_chain_open_interest` | Same chain read |
| 4 | Participation | PUT chain OI | `put_chain_open_interest` | Same chain read |
| 5 | Setup | Break structure | `break_distance_pct` | Signal geometry at admit (`stamp_admit_tech`) |
| 6 | Setup | Price/volume efficiency | `price_volume_efficiency_3m` | Cached session 1m bars |
| 7 | Setup | ATR | `atr` | Session tech hub, current 1m bars |
| 8 | Setup | VWAP distance | `distance_from_vwap_atr` | Session tech hub, `(price − vwap) / atr` on the cached 1m bars |
| 9 | Setup | RSI | `rsi` | Session hub. Blank with `rsi_missing` until 15 closes |
| 10 | Momentum | Directional % change | `dir_pct_change_5m` | `central_tech/chain_ring.py` on `system._central_tech_chain_ring`. Blank until a same-symbol chain sample 4–7 min older exists (3–10 min fallback). The priors come from the pre-warm open seed and, when the flag is on, the t168u path-agnostic sampler (`central_tech/dir5m_presample.py`). Telemetry: `dir_pct_prior_age_s`, `dir_pct_prior_window`, `dir_pct_missing_reason`, `dir_pct_approach_lead_s`, `dir_pct_approach_source`. A missing value does not reject |
| 11 | Market context | SPY change | `spy_change_pct` | `central_tech/spy_change.py` from the discovery quote cache and the session SPY pin. A missing quote stays missing |
| 12 | Microstructure | Underlying L1 | `underlying_l1_imbalance` | Equity quote bid size and ask size, L1 ring |
| 13 | Microstructure | Option L1 | `option_l1_imbalance` | `central_tech/option_l1.py` from the chain row already on the signal. Optional when missing |
| 14 | Liquidity | Chain spread | `chain_sanity_best_same_side_spread_pct` | Same chain read, best same-side spread |
| 15 | Contract | Delta | `delta` | Representative field on that chain read. Optional when missing. The bought-contract seal is later |
| 16 | Contract | Signed OTM % | `otm_pct` | Same chain read. Positive = OTM, 0 = ATM, negative = ITM. Optional when missing |
| 17 | Contract | IV | `iv` | Same chain read. Also reads `implied_volatility` when `iv` is blank. Optional when missing |
| 18 | Contract | Theta | `theta` | Same chain read. Optional when missing |
| 19 | Contract | Gamma | `gamma` | Same chain read. Optional when missing |
| 20 | Contract | Vega | `vega` | Same chain read. Optional when missing |

The six same-snapshot chain ratios (`call_chain_volume_share`, `call_put_chain_volume_ratio`, `put_call_chain_volume_ratio`, `call_chain_vol_oi`, `put_chain_vol_oi`, `total_chain_vol_oi`) are written by `central_tech/chain_ratios.py` from the chain totals already on the row. They are not the rolling Dynamic Volume averages. A missing input leaves the ratio unset.

In-process caches the admit and the monitor share:

| Cache | Where | What it holds | Lifetime |
| --- | --- | --- | --- |
| Session 1m bars | `system._session_tech_bar_cache` | Today's RTH bars per symbol. Broker quote minutes first. Yahoo 1m only when RSI depth is thin | Process. Rebuilt on restart from the day-freeze and new quotes |
| Option chain | `OptionsChainManager.chain_cache` | Last E*TRADE chain payload per symbol | 300s TTL. Admit may reuse up to 600s while a position is open |
| Same-day chain hit | `OptionsChainManager._same_day_chain_hits` | Expiry that already returned contracts, so later reads skip a DTE+N API | Process |
| Empty / nearest expiry memo | `modules/option_expiry_memo.py` | 10031 days, expire-date list, nearest listed ≥ today | Process. Same-day empty Mon–Wed 4h / Thu–Fri 45m unless `0DTE_EMPTY_SAME_DAY_TTL_SEC` is set; other empty / nearest / expire dates 4h. Owned exact OCC and OCC repair read this so a Monday weekly is not re-quoted as today’s missing contract |
| Equity quotes | Discovery quote cache | Last, OHLC, session volume, bid/ask size, SPY change | Batch of 25, paced by `broker_api_pacing` |
| L1 ring | `session_tech_admit` equity L1 ring | Underlying size imbalance over a few seconds | Process, not a GCS object |
| Chain trajectory ring | `central_tech.chain_ring.record_chain_skew_sample` on `system._central_tech_chain_ring` | Call/put volume samples used for `dir_pct_change_5m` and the five-minute chain deltas | Process |
| Entry feature cache | `_orb_0dte_po_entry_features_by_symbol`, `_breakout_po_entry_features_by_symbol`, `_trendline_po_entry_features_by_symbol`, `_trendline_po_entry_features_by_trade_id` | Deep copy of the handoff research row, including `quality_gate_report`, so a fill can restore the decision | Process. The durable copy is the handoff snapshot |
| Owned premium prints | `system._unified_0dte_premium_snapshots_by_trade` | Last exact OCC quote while a position is open | Process. The 2s shadow tape may reuse the last print while a quote batch is in flight, and it keeps the broker print time |
| Yahoo bars | `YahooBackfillManager` in-process cache | Approved 1m structure and thin-RSI history only | Lane TTL. Force-inject uses TTL 0 |

Live broker feeds that stay broker-only: equity quotes, option chains, OCC quotes, fills, and account state. E*TRADE Market API calls share `modules/broker_api_pacing.py`. Open exact OCC is priority 0. Discovery equity still scans 24 names every 10s; option-chain pacing trips only when exit-quote latency is over `0DTE_DISCOVERY_MAX_OPEN_QUOTE_LATENCY_MS` (4000). Yahoo never supplies a chain, a quote, or a fill.

<a id="53-time-correct-data-for-trade-profile-decisions"></a>
### 5.3 Time Correct Data for Trade Profile decisions (decision freeze)

**Added 10/7/2026.** Settings Eric chose on 10/7 at 17:25 PT:
- strict 5m window (nearest prior aged 240–420 s; Oct 13);
- default refresh budgets;
- stale policy = **defer**;
- missing-key rule unchanged.

Code:
- `central_tech/decision_freeze.py`: the freeze object, source ages, targeted refreshes and `RefreshBudget`.
- `easy0DTE/modules/quality_gate_stack.py`: `confirm_chain_skew_before_execution` and `_eval_trade_profile`.
- `modules/orb1_0dte_watch.py`: `orb1_decision_chain`.
- `easy0DTE/modules/convex_chain_skew.py`: `force_live` and the chain clock.
- `modules/po_priority_snapshot.py`: the handoff seal.
- `modules/execution_feature_snapshot.py`: the envelope.
- `modules/tp_decision_record_store.py`: decision records.

Env keys are listed in [Configs.md](Configs.md#trade-profile-decision-freeze-oct-7-2026).

#### Purpose

The quality gate is the Trade Profile, so the accept or reject decision is only as good as the tech data it reads. Every value a cell gates must describe the market **at the accept/reject moment**. A value from when the signal was created, or from the last time discovery happened to cache it, is not good enough.

The handoff snapshot is the training record of that moment. Future Trade Profiles are mined from it, so it must hold exactly the values the gate scored, together with the age and clock of each source.

#### Flow

| Step | What | Where |
| --- | --- | --- |
| 1 | **Discovery.** Broker equity quotes every **10 s** in rotating batches of **24** symbols, plus the discovery chain reads. Unchanged. | `discovery_watch_batch.py` |
| 2 | **Central tech hub cache.** Session 1m bars, the equity L1 ring, the SPY session pin, the option chain cache and the chain trajectory ring. | `_session_tech_bar_cache` · L1 ring · `spy_session_quote` · `OptionsChainManager.chain_cache` · `central_tech/chain_ring.py` |
| 3 | **Signal.** An ORB1, Trendline or Breakout break. | Path watch |
| 4 | **Age check and targeted refresh.** Each source the cell gates is measured against its cap. When one is too old, there is at most one refresh per source for that symbol (see below). | `confirm_chain_skew_before_execution` (TL/BO) · ORB1 watch |
| 5 | **Clear and re-read.** Values carried from signal creation are removed. `stamp_admit_tech` and the chain enrich then re-stamp them from the hub. | same |
| 6 | **Freeze.** `single_tp_decide` builds one stamp, then one freeze: the exact values it scores (post-pick), each source's age and clock, `gate_at`, and `decision_digest`. A source older than `max_age_by_source()` is emptied before the digest. | `build_decision_stamp` · `apply_source_age_caps` · `build_decision_freeze` |
| 7 | **Gate.** The Trade Profile runs on those values and admits, rejects or defers. It logs `TP_DECISION_VALUES` with the freeze ages and appends one decision record. | `quality_gate_stack.py` |
| 8 | **Handoff snapshot.** Reads the **same** freeze. `apply_freeze_to_row` writes those values, clocks, `po_decision_freeze`, and `po_profile_decision` (same keys, same digest). Snapshot clock is `gate_at`. It logs `TP_FREEZE_PARITY`. | `_seal_entry_snapshot_tech` → `apply_freeze_to_row` |

These are not changed: the discovery loop cadence, the open-position monitor, the shadow / flight recorder cadence, and the premium / underlying time series.

#### Per-source age caps

A source is checked only when the cell gates one of its keys (has a range line for it; `cell_gated_keys`).

| Source | Keys it feeds | Cap at the gate | Clock used | Env |
| --- | --- | --- | --- | --- |
| Chain | 4 chain volume / OI keys · 3 vol/OI ratios · `call_share` / `put_share` · `chain_sanity_best_same_side_spread_pct` · `delta` · `otm_pct` · `iv` · `theta` · `gamma` · `vega` · `option_l1_imbalance` | **60 s** (also live-refresh if the cache has no two-sided bid/ask, any age) | Chain cache fetch time (`cached_chain_age_seconds`). A live fetch stamps `tp_chain_age_sec` and `tp_chain_observed_epoch`. Empty book: `plan_chain_refresh` reason `no_two_sided_quotes`. | `TP_FREEZE_CHAIN_MAX_AGE_SEC` |
| Quote | `underlying_l1_imbalance` | **120 s** (scan quote stays the decision quote this long) | Receipt time of the newest L1 ring sample | `TP_FREEZE_QUOTE_MAX_AGE_SEC` |
| SPY | `spy_change_pct` | **45 s** | SPY pin receipt time | `TP_FREEZE_SPY_MAX_AGE_SEC` |
| Session tech | `atr` · `rsi` · `distance_from_vwap_atr` · `price_volume_efficiency_3m` | **90 s** | Last 1m bar upsert for the symbol (`_session_tech_bar_fetched_at`) | `TP_FREEZE_SESSION_TECH_MAX_AGE_SEC` |
| 5m prior | `dir_pct_change_5m` | Nearest prior chain-ring sample aged **240–420 s (4–7 min)** before the decision chain (Oct 13; was 300 s ± 90 s), **strict**; only samples at or before the freeze | Ring sample time, which is the chain observed time | `TP_DIR5M_PRIOR_MIN_AGE_SEC` (240) · `TP_DIR5M_PRIOR_MAX_AGE_SEC` (420) · `TP_FREEZE_5M_STRICT` · set the two age vars empty to restore 300 ± `TP_FREEZE_5M_PRIOR_TOL_SEC` |
| Break | `break_distance_pct` | none (signal geometry at the break) | Break | — |

- **5m window (strict + fallback, t166u).** Strict: nearest prior 240–420 s (4–7 min). If empty, the F2 fallback takes the nearest prior 180–600 s (3–10 min) and stamps `dir_pct_prior_window=fallback`. Never an any-age "oldest sample". No prior → `None` + `dir_pct_missing_reason`, never 0.0; a measured 0.0 is stamped 0.0 (the 10.5 JSON's `zero_is_missing` then treats it as missing = pass). Prior ages: `dir_pct_change_5m_prior_age_sec`, `chain_volume_delta_5m_prior_age_sec`, `dir_pct_prior_age_s`. **Fill:** 1 % live on 10/13 (pre-sampler, decision usually the symbol's first chain read). Replay 9/15–10/14 with the sampler at 6/min: 81 % strict+fallback (strict alone 43 %); the 12/min deploy rate is to be measured live. Architecture: "`dir_pct_change_5m` / 5m chain keys — architecture" below.
- **SPY fallback.** `central_tech/spy_change.py` now applies the same 45 s TTL to its discovery-quote fallback. It used to be 120 s.

#### Targeted refresh, dedupe and budgets

Nothing polls on a new cadence. Refreshes happen only at a gate and only for one symbol.

- **Chain** (`plan_chain_refresh`):
  - Cache age ≤ 60 s: the cached chain is used.
  - Cache missing, or older than the reuse window (300 s, or 600 s while a position is open): the **pre-existing** live fetch runs, as before. It is not charged to the freeze budget.
  - Age between 60 s and the reuse window (`tp_age_cap`): **one** extra live chain fetch for that symbol, with the cache bypassed (`force_live`). It is charged to the chain budget.
- **Quote / session tech / SPY** (`ensure_fresh_quote_sources`): a discovery scan quote younger than **120 s** is reused (`scan_quote`). Otherwise the symbol is added when the quote is older than `quote_max_age_sec()` (120 s) or session tech older than 90 s, and SPY is added when the SPY pin is older than 45 s. Then **one** `get_quotes([symbol, SPY])` call updates the L1 ring, the 1m bar cache and the SPY pin. It is charged to the quote budget.
- **Dedupe.** There is a per-symbol async lock, and the age is checked again inside the lock. A second signal on the same symbol uses the chain or quote the first one just fetched (`deduped`, no call).
- **Budgets** (`RefreshBudget`: per process, reset at the PT day roll):

  | Refresh | Per session | Per minute |
  | --- | --- | --- |
  | Chain | 300 | 8 |
  | Quote | 300 | 10 |

  No extra refresh runs while the OPTION_API hard cap (140 real calls/min) is throttling; the reason is `hard_cap_throttle`. The existing per-scan live-chain cap while a position is open (`0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN`) is still checked first. With the chain fetch manager on (live `00599-lhd`, 10/13), the shared chain bucket replaces that cap and a `tp_age_cap` refresh is charged once to the bucket.
- **Logs:**
  - `TP_FREEZE_REFRESH`: action `live`, `failed` or `deduped`, with the ages before the refresh.
  - `TP_FREEZE_BUDGET`: `budget_exhausted` with reason `budget_session`, `budget_minute` or `hard_cap_throttle`.

#### Defer policy (`TP_FREEZE_STALE_POLICY=defer`)

| Case | Result |
| --- | --- |
| Chain refresh denied by budget, or the chain fetch failed | `chain_attempt` = `skipped_tp_freeze_budget` / `skipped_chain_unavailable`. With no same-side chain volume, the gate defers `required_data_not_ready` (record stage `tp_defer`). The break is held and evaluated again on a later scan. |
| A gated source is still over its cap at evaluation | `TP_FREEZE_STALE … action=defer stale=[key:source:age]`, then defer `required_data_not_ready` with `tp_freeze_stale` set (stage `tp_stale_defer`). |
| Policy `reject` (not the current setting) | Budget denials reject `tp_freeze_budget_exhausted` (stage `tp_budget_reject`), and stale sources reject `tp_freeze_stale_source` (stage `tp_stale_reject`). |

A decision is never scored on cleared or stale data. A missing value is not the same as a stale one: a missing key follows the missing-key rule.

#### What is cleared and re-read

Before the gate re-stamps, values carried from signal creation or discovery are removed from the row. They are also removed from the row-level copies of `metadata`, `setup_payload` and `qg_admit`, because otherwise `promote_qg_admit_fields` would lift them back. The original objects are not changed.

| Family | Keys (code tuple) |
| --- | --- |
| Session tech (`SESSION_REFRESH_KEYS`) | `rsi`, `rsi_14`, `atr`, `vwap`, `vwap_distance_pct`, `abs_vwap_distance_pct`, `distance_from_vwap_atr`, `price_volume_efficiency_1m/_3m/_5m`, `price_change_1m/3m/5m_pct`, `volume_1m/_3m/_5m`, `session_tech_bar_at` |
| Quote (`QUOTE_REFRESH_KEYS`) | `underlying_bid_size`, `underlying_ask_size`, `underlying_l1_imbalance`, `underlying_l1_imbalance_delta_5s/_15s`, `underlying_spread_pct`, `underlying_quote_age_sec`, `underlying_quote_stale` |
| SPY (`SPY_REFRESH_KEYS`) | `spy_change_pct`, `rs_vs_spy` |
| Chain (`CHAIN_REFRESH_KEYS`, cleared right before the chain enrich) | chain volume and OI (including the desk aliases `call_volume` / `put_volume`), totals, `put_call_volume_ratio`, `put_call_oi_ratio`, the 3 vol/OI ratios, `iv` / `implied_volatility` / `representative_iv`, `delta` / `representative_delta`, `otm_pct`, `representative_strike`, greeks, spread keys, option L1 keys, `direction_volume_pct`, plus the 5m prior ages |

**Carry exception** (`CARRY_RESTORABLE_KEYS`: `rsi`, `atr`, `vwap_distance_pct`, `distance_from_vwap_atr`, `price_volume_efficiency_3m`, `price_change_3m_pct`, `volume_3m`). When central_tech cannot re-read one of these keys, the carried value is restored only if `seconds_since_break` is 90 s or less (the session tech cap). Restored keys are listed in the freeze `carry_keys`. Dropped keys log `TP_FREEZE_CARRY action=drop_stale_carry`.

#### ORB1 `iv` / `delta` / `otm_pct`

ORB1 now gets its decision chain from `orb1_decision_chain`, with the same 60 s cap, dedupe and budget. So `iv`, `delta`, `otm_pct` and the other chain keys come from a chain no older than 60 s at the gate. When the budget is spent or the fetch fails, `chain_attempt` is `skipped_tp_freeze_budget` or `skipped_chain_unavailable` and the signal defers. The freeze is copied onto the ORB signal in `_build_dte0_signal`, so the ORB handoff reads the same object.

#### Vol/OI ratio keys

`total_chain_vol_oi`, `call_chain_vol_oi` and `put_chain_vol_oi` are 23-key template keys. `central_tech/chain_ratios.py` derives them from the same decision chain.

- They are now in `QG_ADMIT_KEYS` (`modules/qg_admit_snapshot.py`, `modules/po_observe_trajectory.py`), the Path SS feature keys, and `ENTRY_LOCK_KEYS` (`TP_RATIO_LOCK_KEYS` in `modules/po_temporal_contract.py`).
- They are cleared and re-derived with the chain.
- The snapshot schema is unchanged (267 keys).

#### Freeze fields on the handoff snapshot and in the logs

**Freeze object** (`po_decision_freeze`, schema `tp_decision_freeze_v1`):

| Field | Meaning |
| --- | --- |
| `gate_at` | Decision clock (UTC ISO) |
| `symbol` · `path` · `side` · `slot` · `trade_id` | Signal identity |
| `profile` · `profile_version` | Profile that decided |
| `admitted` · `reason` · `stage` | `true` / `false` / `null` (defer), reason code, record stage |
| `values` | The scored stamp: template keys of the side, shares, Wide extras, and the **picked** contract's `delta` / `otm_pct` / `option_l1_imbalance`. Families over `max_age_by_source()` are empty |
| `decision_digest` | `sha256` of `values`. Same string as `po_profile_decision.decision_digest` and `single_tp.evaluated_digest` |
| `cell_keys` · `missing` · `missing_gated` · `stale_gated` | Keys the cell gates, missing keys, gated keys that were missing, gated keys over their cap |
| `ages_sec` | `chain`, `quote`, `spy`, `session_tech`, `dir5m_prior` |
| `asof` | Source clocks: `chain`, `quote`, `spy`, `session_tech` |
| `max_age_sec` · `quote_exchange_age_sec` | Caps in force, and the exchange quote age (for information) |
| `chain_source` · `refresh` · `carry_keys` | `chain_attempt`, refresh actions (`chain`, `chain_action`, `quote`), restored carry keys |
| `context_measured` | `false` when ages were not measured (`TP_FREEZE_ENABLED=false`) |

**Handoff row** (written by `apply_freeze_to_row`):
- The frozen values. `iv` is also written to `implied_volatility`.
- `po_decision_freeze` and `po_profile_decision` (same `keys`, same `decision_digest`).
- `po_decision_freeze_at` = `po_decision_gate_at` = `gate_at`.
- `tp_age_chain_sec`, `tp_age_quote_sec`, `tp_age_spy_sec`, `tp_age_session_tech_sec` and `tp_age_dir5m_prior_sec`.
- Source clocks: `po_chain_asof_at` / `chain_observed_at`, `po_quote_asof_at` / `quote_observed_at`, `po_spy_asof_at` / `spy_observed_at`, `po_session_tech_asof_at` / `session_tech_bar_at`.
- `po_freeze_parity_diffs`: the number of keys whose pre-seal row value differed from the freeze.

The execution snapshot envelope carries `po_decision_gate_at`, `po_decision_freeze`, `po_freeze_parity_diffs` and `po_handoff_link`.

**`TP_DECISION_VALUES` suffix** (on every line):

```
freeze gate_at=<UTC ISO> ages=chain:<s>,quote:<s>,spy:<s>,session_tech:<s>,dir5m_prior:<s> chain_src=<chain_attempt> refresh=chain:<none|tp_age_cap|missing_or_expired|tp_freeze_budget:…>,quote:<none|live|deduped|failed|budget_exhausted:…> stale=[…] missing_gated=[…] carry=[…]
```

**Other greps:**
- `TP_FREEZE_REFRESH` · `TP_FREEZE_BUDGET` · `TP_FREEZE_STALE` · `TP_FREEZE_CARRY`
- `TP_FREEZE_MISSING_GATED`
- `TP_FREEZE_PARITY`, with `replaced=<n>`. `freeze=missing` is greppable as `TP_FREEZE_MISSING`.
- `PO_HANDOFF_LINK_MISSING`

#### Decision records (every evaluation)

- **Content.** One JSON line per evaluation: the freeze object plus `record_kind` and `recorded_at`. Kinds:
  - `tp`: scored admit or reject, or an eval error;
  - `tp_defer`;
  - `tp_stale_defer` / `tp_stale_reject`;
  - `tp_budget_reject`;
  - `contract_band`.

  About 2 KB per line.
- **Local daily file.** `tmp/tp_decision_records/{YYYY-MM-DD PT}.jsonl` under the app root. `TP_DECISION_RECORD_DIR` overrides the folder. Unit tests write to the system temp folder.
- **GCS.** The whole daily file goes to `{ns}/profit_optimizer/tp_decision_records/{day}/{HHMMSS_pid}.jsonl`, at most once per **300 s**. The interval is checked on the next append, and there is also a flush at process exit. Each process writes its own object, so a Cloud Run restart cannot overwrite earlier records. Upload needs `GCS_PERSISTENCE_ENABLED` and `TP_DECISION_RECORD_GCS` (both default true).
- **Safety.** Record failures never reach the trading loop.

#### `entry_premium` and `underlying_price` at handoff

`entry_premium` is **null** on the handoff snapshot, because there is no option fill yet. At the handoff stage, `po_snapshot_hydration` no longer copies a stock price into it. The stock price at the decision is in `underlying_price`. The option fill premium comes with the executed snapshot.

#### Executed → handoff link

The executed snapshot finds its handoff by `trade_id`, then by the gate clock (`po_decision_freeze_at`). It never restamps the freeze clock to the fill time.

If no handoff row is found, the row gets `po_handoff_link=missing` and `po_entry_training_eligible=false`, the envelope is not training-eligible, and `PO_HANDOFF_LINK_MISSING … action=no_restamp` is logged.

#### Missing-key rule (the loaded JSON is the only source; corrected 10/13 against the file)

Source: `easy0DTE/configs/trade_profile_10.5.json` (sha256 `ac50626e…` on 10/7; `32792b3f…` deployed 10/8 as `easy-etrade-strategy-00594-zn5`, same rule plus ORB1 PUT `rsi`). Missing passes by default. The exceptions are `missing_reject_keys`: the warm-up keys that the cell gates.

| Cell | Rejects when missing |
| --- | --- |
| ORB1 CALL | `price_volume_efficiency_3m`, `distance_from_vwap_atr`, `rsi` |
| ORB1 PUT | `price_volume_efficiency_3m`, `rsi` (from 10/8) |
| TL0 CALL | `distance_from_vwap_atr`, `rsi` |
| TL0 PUT | `price_volume_efficiency_3m`, `distance_from_vwap_atr`, `rsi` (10/9 t146u: `dir_pct_change_5m` removed) |
| TL1 / TL2+ / Breakout | nothing (missing passes) |

- **What counts as missing:** None or NaN, or exactly `0.0` for the zero-is-missing keys (`dir_pct_change_5m`, `spy_change_pct`). The reason is `tp105_{slug}_missing_{key}`.
- **What the freeze does:** it does not change the rule. It only fills keys from the hub and logs any gated key that is still missing (`TP_FREEZE_MISSING_GATED`, freeze `missing_gated`).
- **Effect of the 5m window:** none on admits. Since 10/9 (t146u) no profile lists `dir_pct_change_5m` in `missing_reject_keys`, so a missing value passes (was: TL0 PUT rejected `tp105_tl0_put_missing_dir_pct_change_5m`).

#### Rollback (env only)

| Env | Effect |
| --- | --- |
| `TP_FREEZE_ENABLED=false` | Turns off the age checks, targeted refreshes, key clearing, carry rule and stale defers, so data reaches the gate as it did before 10/7. These stay on: the freeze object (values and `gate_at`, no ages), handoff value parity, decision records, null `entry_premium`, the handoff link, the 45 s SPY fallback and the ratio-key locks. |
| `TP_FREEZE_5M_STRICT=false` | Old 5m prior selection: the nearest sample within max(90 s, 0.75 × horizon), otherwise the oldest ring sample. |
| `TP_DECISION_RECORD_ENABLED=false` · `TP_DECISION_RECORD_GCS=false` | No decision records, or local files only. |
| Full | Route traffic back to the previous revision, or revert `decision_freeze_1007.patch`. |

#### Chain fetch manager and budget (Oct 13 2026)

`central_tech/chain_fetch_manager.py`, flag `CHAIN_FETCH_MANAGER_ENABLED` (true). It replaces the per-scan live chain cap (`0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN=2`). On 10/13 that cap caused 123 skipped TL chain pulls and 103 `dir_vol_missing_chain_volume` rejects.

- **Budget.** One token bucket: `CHAIN_FETCH_BUDGET_PER_MIN=24` chains (×2 = 48 real calls), burst 8.
  - On 10/13 the non-chain peak was 54/min plus up to 10/min of freeze quote refreshes, so the projected peak is 112/min against the 140/min cap.
  - Gate/position requests stop at a live minute of 130 real calls; low-priority requests stop at 100.
  - Since the maxpos change (`CHAIN_CALLS_COUNT_TOWARD_HARD_CAP=true`), live chains are recorded in `real_broker_total` (`lane=chain`, × 2), so the 140/min hard cap sees them. With the flag off, the manager adds `_option_api_usage_chain_calls` × 2 itself.
  - `CHAIN_FETCH_BUDGET_PER_MIN=24` is only the starting rate; the adaptive budget (below) resets it every minute.
- **Priorities:**
  - P0: open-position 5m resample (20p/exit ring prior). Same empty-book / unknown-age `force_live` as P1.
  - P1: signal gate/handoff — `force_live` when the cached chain is older than `TP_FREEZE_CHAIN_MAX_AGE_SEC` (60 s), **age is unknown**, or the cached chain has **no two-sided bid/ask** (`no_two_sided_quotes`). Demo and Live use the same door.
  - P2: dir5m approach / keepalive / held resample — **reuse cache** for the cadence (210–300 s). Do not `force_live` at 60 s. A reused chain is recorded at its **original fetch asof**, never the reuse clock.
  - P3: prewarm.
  - P2/P3 keep a reserve of 6 tokens for P0/P1. Drain stays **2 in flight / 6 per tick** (2c06b26 / t217u handoff-first). A Wide book being open does not serialize P2. Yield to the monitor after each batch so OCC keeps the 2s tape.
  - Every hub/skew fetch goes through `fetch_0dte_intent_chain` (same-day if listed, else earliest listed; Mon–Thu `optionexpiredate` first; empty-day memo resets each US/Eastern session). Cache hits do not count toward the option-API cap.
  - dir% now and prior must share the same listed expiry; a mix is `dir_pct_missing_reason=expiry_mismatch`.
- **Dedupe.** One chain serves both sides. The queue keys on symbol/expiry and merges the requested sides. A chain within `TP_FREEZE_CHAIN_MAX_AGE_SEC` (60s) **with two-sided quotes** is reused; a freeze `tp_age_cap` or empty-book refresh is charged once to the bucket, not to the 8/min freeze budget. A picker `no_quote` on a reused (not just-live) cache is `data_wait:chain_quotes`, not a 1h `contract_unavailable` cooldown.
- **Ring.** Every manager fetch records a chain-ring sample, and gate fetches already record one via `enrich_signal_with_chain_skew`. The sampler (15s tick, ≤3 per tick) resamples held positions and gate candidates from the last 15 min every 285s, so the 5m prior exists. The sampler only runs when there is headroom.
- **Gate.**
  - If no token is available, the chain is queued at P1 and `chain_attempt=skipped_chain_budget_queued` is set.
  - dir% (`_eval_chain_dir_pct`) returns DEFER `required_data_not_ready` when the chain is missing, skipped, queued or stale (`tp_chain_age_sec` > cap). It never rejects in that case and never arms a cooldown. TL rearms the same break and BO holds it.
  - After `DIR_PCT_DEFER_MAX_WAIT_SEC=90` the outcome is `dir_pct_data_unavailable`. It is reason-coded and has no cooldown. Since the maxpos change below, the break is retired: TL calls `_trendline_reject_current_exec_signal(..., arm_slot_side_cooldown=False)` and BO calls `_rearm_breakout_to_wait_break`. The next break re-arms.
  - Only a real value ≤ floor (0.10) rejects, labelled `dir_pct_filter_reject` (the same terminal class as before, no longer `trade_profile_reject`).
  - In observe mode, a dir_pct defer does not block.
- **Stamping.** When the chain is not fetched, the chain keys and `direction_volume_pct` are None on the row, metadata and setup, with `chain_unavailable_reason` set. A carried or over-age chain is never stamped as current.
- **Same HTTP door:** discovery cadence, monitor last-look, executor contract pick, and ORB1 now call `fetch_0dte_intent_chain` (no raw E*TRADE fallback). `chain_fetch_manager` still budgets hub/skew/dir% only; those other callers count toward the live-minute headroom but do not re-implement expiry.
- **Logs:** `CHAIN_FETCH_MGR`, `QG_CHAIN_YIELD action=queue_live_fetch`, `DIR_PCT_DEFER`, `DIR_PCT_DATA_UNAVAILABLE`.
- **Rollback:** `CHAIN_FETCH_MANAGER_ENABLED=false` (config file, not a deploy env flag).
- **Limits (10/13 review):** with the manager on, the per-scan cap is `CHAIN_FETCH_SCAN_CEILING=24`; `0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN=2` (at ≥7 open) applies only with the manager off. E*TRADE Market API (typical consumer key): 4 requests/s, 14,000/h; 1 chain = 2 real calls; our 140/min cap = 60% of both. Sim of the 10/13 06:40–07:50 demand at 50 open (expected load): 0 retired, wait p90 13 s, peak 119 calls/min. **Local recommendation, not deployed:** `CHAIN_FETCH_ADAPTIVE_FLOOR_PER_MIN=10` (live 6) and `0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN=8` (manager-off safety net). `dir_pct_change_5m` missing = no prior chain sample 4–7 min old at decision time.

#### Max open positions first, adaptive chain budget, 50-position book (Oct 13 2026, maxpos)

- **The cap is checked first.** `max_open_positions_precheck(system, path)` lives in `modules/unified_0dte_portfolio_risk_manager.py`. The flag is `MAX_OPEN_POSITIONS_PRECHECK_ENABLED` (true), and the check only runs when `UNIFIED_0DTE_RISK_ENABLED` is on.
  - It makes no API call. It counts in-process open positions plus in-flight reservations against `TOTAL_0DTE_MAX_POSITIONS` and the per-path cap, the same counts as `_check_path_caps`.
  - TL and BO run it at the top of `confirm_chain_skew_before_execution`. ORB1 runs it in `orb1_0dte_watch` right after the side-cooldown check.
  - A capped signal gets reason `max_open_positions_reached` with `cap_kind`, `open_n` and `cap`. That reason appears in the gate report (`gate_id=max_open_positions`), in `*_REJECT` and `GATE_STACK_REPORT` log lines, and as a desk stage (`max_open_positions`).
  - A capped signal pulls no chain, takes no manager token, gets no dir% evaluation and arms no cooldown.
- **Capacity retry stays as it was.** TL uses `_tl_exec_fail("max_open_positions_reached", rearm=True)`, the same as `open_cap_reached` (exec-ready TTL still applies, and the Telegram alert stays silent). BO already checked `slot_cap` before the TP confirm; it now logs `reason=max_open_positions_reached` and holds the break. ORB1 holds the same break.
  - Executor cap codes (`combined_cap_reached`, `trendline_/breakout_/orb_cap_reached`) are now in `TRANSIENT_PATH_EXEC_REJECT_REASONS`, so they retry with no 1h cooldown. Before this change, a TL post-executor `combined_cap_reached` (60 on 10/13) was a candidate-only hard reject.
  - The PO writers are unchanged.
- **10/13 relabel.** Of the 104 `dir_vol_missing_chain_volume` rejects, 88 happened with the book full: 62 had both bracketing `OPTION_POSITION_SUMMARY` points at 7 or more, and 26 were at the edge (7 before, fewer after). These 88 would now be `max_open_positions_reached`. The other 16 were real chain starvation, which the manager now serves.
- **Adaptive chain budget.** `CHAIN_FETCH_ADAPTIVE_BUDGET` (true) sets chains/min = (0.85 × 140 − last minute's non-chain real calls) / 2, clamped to 6–40.
  - `option_api_accounting._maybe_roll_window` feeds `observe_minute` before the counters reset. The bucket refill rate follows it, and `CHAIN_FETCH_BUDGET_PER_MIN` is only the starting rate.
- **Chains count toward the 140 cap.** With `CHAIN_CALLS_COUNT_TOWARD_HARD_CAP` (true), each `_record_option_api_usage(chain_calls=n)` also records n × 2 real calls with `lane=chain`. `real_broker_total` and `OPTION_API_HARD_CAP` now include chains, and the manager no longer adds them a second time.
- **The `open_n >= 7` literal is gone.** `qg_live_chain_cap` (legacy path only, manager off) now uses `0DTE_QG_LIVE_CHAIN_TIGHT_AT_OPEN` (7).
- **50 positions.** `configs/Account0DTE.env` sets `TOTAL_0DTE_MAX_POSITIONS`, `0DTE_SLOT_COUNT`, `ORB_/BREAKOUT_/TRENDLINE_0DTE_MAX_POSITIONS` and `0DTE_MONITOR_MAX_SYMBOLS` to 50, and `configs/Shared.env` sets `MAX_TOTAL_OPTION_POSITIONS=50`. `0DTE_MAX_POSITION_SIZE_SUM=600` still binds per entry, because 1/50 of $100k is more than $600.
  - The monitor eval budget is unchanged: 1200 ms, or `0DTE_MONITOR_EVAL_BUDGET_MS_OPEN20=1800` (`Shared.env`) at 20+ opens, with a hard cap of 2200.
- **Simulation (10/13 full-session replay, 50 cap).** In the base case (2× option quote calls, 1.5× gate demand), peak real calls were 129/min, the median 86, and no minute went over 140.
  - In the stress case (4× option calls), non-chain calls alone reach 130–150/min. The chain budget drops to its floor or zero, and the overage comes from the monitor's quotes, not from chains.
- **Rollback:** set `MAX_OPEN_POSITIONS_PRECHECK_ENABLED=false`, `CHAIN_FETCH_ADAPTIVE_BUDGET=false` and `CHAIN_CALLS_COUNT_TOWARD_HARD_CAP=false`, and put the caps back to 7.

#### Expected broker API cost (10/7 volumes)

The estimate replays 10/7's 716 TP evaluations (TL 468, BO 188, ORB1 60) through the real `RefreshBudget`, 500 times. It uses the stale rates seen at the 10/7 handoffs: chain older than 60 s in 29%, quote older than 20 s in 35%.

| Measure | Expected |
| --- | --- |
| Chain refreshes | ≈ 206 per session (p95 225), ≈ 412 real calls (a chain call counts as 2) |
| Quote calls | ≈ 249 per session (p95 272) |
| Total extra | **≈ 660 real broker calls per session**, +27% over 10/7's 2,418 |
| Worst minute | Adds at most 26/min (8 × 2 + 10), so **≈ 67/min** at 10/7's peak of 41, against the **140/min** OPTION_API hard cap |
| Budget denials | ≈ 1 per session (p95 3), each a defer |

To monitor: count `TP_FREEZE_REFRESH` and `TP_FREEZE_BUDGET`, and watch `OPTION_API_USAGE`.

#### `dir_pct_change_5m` / 5m chain keys — architecture (Oct 14 2026, t166u–t172u)

One shared process feeds every dir% and Trade Profile 5m chain key (`dir_pct_change_5m`, `direction_volume_pct_5m_ago`, `chain_volume_delta_5m`) for every path, side and snapshot.

- **Ring (central tech hub, per symbol).** `central_tech/chain_ring.py` on `system._central_tech_chain_ring`. Every chain read records `call_chain_volume` / `put_chain_volume` at its true observation time (F1; never a cached row at wall-clock now). Zero broker calls of its own.
- **R6 duplicate filter.** Chain volumes are session-cumulative. A sample whose call AND put volumes equal a sample already in the ring is the same observation re-recorded (row seed, cached re-read, entry carry). It is dropped and the original time kept; `force` does not bypass it. Priors that duplicate the now-chain are skipped (no fabricated 0.0).
- **TTL and size.** `DIR5M_RING_TTL_SEC` 3600 s (60 min), pruned on record after R6. Min spacing 15 s. In memory up to 64 samples per symbol; persisted newest 16 per symbol (`DIR5M_RING_PERSIST_MAX_PER_SYMBOL`).
- **Persistence.** `{research_app}/daily_markers/chain_ring_5m/{YYYY-MM-DD}.json` (+ local `DAILY_MARKER_DIR/chain_ring_5m/`), schema `chain_ring_5m_v1`, written debounced 60 s (`DIR5M_RING_PERSIST_DEBOUNCE_SEC`), reloaded on a same-day restart (TTL applied on load).
- **Approach sampler (path-agnostic).** `central_tech/dir5m_presample.on_discovery_quotes`, one call per unified discovery tick (after the 24-symbol batch quote, every 10 s). Reads ORB1 / TL0–TL6 / ORB2-Breakout candidates, CALL and PUT, through one level interface; no path hooks. Band = max(`DIR5M_APPROACH_PCT` 0.004 × level, `DIR5M_APPROACH_ATR` 0.25 × ATR); closest first. Caps: `DIR5M_APPROACH_MAX_PER_MIN` 12 chains per rolling 60 s (deploy env; code default 6), 1 per symbol per 300 s (a symbol with a ring sample that young is skipped with no API), P2 in `chain_fetch_manager` behind the P0/P1 reserve. Flag `DIR5M_APPROACH_SAMPLER_ENABLED` (code false, deploy true).
- **Morning prewarm (open seed, P3).** `CHAIN_RING_PREWARM_TIMES_PT` 06:40 / 06:50 / 07:00 / 07:15 / 07:26 / 07:33, window 240 s, concurrency 4. First slot: all symbols; later slots: only symbols with no ring sample in the last 4 min. Covers the 06:45–07:25 ORB1/TL0 gates (07:15 is the late-window seed) and the 07:25–07:30 ORB2 capture gap / 07:30 TL1 + ORB2 build.
- **Windows.** Strict: nearest distinct prior with a same-side share aged 240–420 s (4–7 min) before the now-chain (`TP_DIR5M_PRIOR_MIN_AGE_SEC` / `TP_DIR5M_PRIOR_MAX_AGE_SEC`). Fallback (F2, `DIR5M_FALLBACK_ENABLED`): 180–600 s (3–10 min) when the strict window is empty. dir% = same-side share now − same-side share at the prior.
- **Stamped fields (every decision and snapshot; also the freeze `dir5m` block).** `dir_pct_prior_age_s` · `dir_pct_prior_window` (`strict` / `fallback`; `legacy` with ring v2 strict ages unset) · `dir_pct_missing_reason` · `dir_pct_approach_lead_s` (seconds from the first pre-sample or seed to the decision) · `dir_pct_approach_source` (`orb1|tl|orb2:approach`, `…:armed`, `open_seed`, `none`).
- **`dir_pct_missing_reason` codes (first match).** `no_current_chain` (no now-chain / share) · `no_prior` (no older sample) · `dup_sample` (only R6 duplicates) · `prior_no_side_share` · `prior_too_young` (all younger than the window) · `prior_too_old` (all older) · `prior_outside_window` (samples on both sides, none inside) · **`expiry_mismatch`** (now and prior listed expiries differ — count separately from shortage; expected if nearest expiry changes mid-morning).
- **Snapshot asof (t172u, `DIR5M_SNAPSHOT_ASOF_ENABLED` default true).** Handoff and `entry_executed` use the decision (freeze) clock. 20p, exit, other mid-trade snapshots and the shadow flight recorder use their own event time (`po_snapshot_event_at` / `po_20p_event_at` / `po_exit_event_at` / `po_tech_asof_at`). Now-chain = the row's chain if its clock is within `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC` (60 s) of the event, else the hub's newest ring sample in that window, else missing `no_current_chain`. Prior = 4–7 min (fallback 3–10) before the now-chain. Never copies the entry value; a carried entry chain clock is never used as "now".
- **Measured API load (Demo, `OPTION_API_USAGE` `real_broker_total`, 10/14 wide).** avg 39/min, p90 50, peak 102/min. With the sampler at 12/min (≈ 24 Market calls/min): ~63 avg / ~126 peak. Limit: E*TRADE Market 4/s = 240/min → peak ≈ 53 % (50–80 % band). The app's own hard cap is 140/min (≈ 90 % at peak); P2/P3 yield first. 10.5 (≤15/day) is lower (estimate from code budgets).
- **t194u (Oct 15; build, next deploy).** 10/15 (first live day, rev 00600-fpn): handoff/entry 70 % stamped, 20p 12 %, exit 14 %. Root cause: the manager's held-position resample read `system._unified_0dte_position_monitor`, which nothing sets, so held symbols were never sampled.
  - **Held positions:** `DIR5M_SAMPLE_OPEN_POSITIONS=true` reads the monitor singleton and queues held symbols at P2, behind the reserve, in the same bucket. It samples 1 per symbol per `DIR5M_OPEN_POSITION_RESAMPLE_SEC` (300), at most `DIR5M_OPEN_POSITION_MAX_PER_MIN` (6) per 60 s, oldest ring sample first. Every other chain read (gate, sampler, prewarm) already lands in the ring at no extra cost.
  - **Exit/20p now-chain:** may be up to `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC` old (deploy 180; code 60), stamped as `dir_pct_now_chain_age_s`.
  - **Aging first:** the sampler ranks armed symbols first, then symbols whose last sample is 240–600 s old (about to age out), then the rest closest-first (`DIR5M_APPROACH_AGING_FIRST`, default true).
  - **Morning boost:** optional `DIR5M_APPROACH_BOOST_MAX_PER_MIN` inside `DIR5M_APPROACH_BOOST_WINDOW_PT` (06:30-09:00). Off by default: at 18/min the coincident peak would exceed the app's 140/min cap.
  - **PO telemetry:** every PO snapshot envelope (handoff, executed, 20p, age_120s, first_red, pullback, exit) carries `po_dir5m`: `dir_pct_change_5m`, `dir_pct_prior_age_s`, `dir_pct_prior_window`, `dir_pct_missing_reason`, `dir_pct_approach_source`, `dir_pct_approach_lead_s`, `dir_pct_now_chain_age_s`, `dir_pct_asof_epoch` and `source` (row / freeze / extra). The 267-key feature rows are unchanged. Mid-trade rows also carry `lifecycle_event` (schema v8).
  - **Not changed:** the legacy `quality_gate_report.results[].fields.dir_pct_change_5m` is the QG stack's own pre-freeze reading (None on 83/122 10/15 handoffs). The freeze and `po_dir5m` are authoritative.
- **t202u/t203u (Oct 15; build on 9258e5f, next deploy).** Goal: `dir_pct_change_5m` on 100 % of handoffs (Trade Profile accept/reject training) and 20p snapshots. Exit is best effort.
  - **Handoff guarantee: discovery scan → must execute.** The armed / READY_TO_EXECUTE state is too short to start sampling there (10/15: armed→handoff median 24 s, 98.6 % < 180 s). The earliest reliable signal before must execute, for every path, is when the candidate registers in its live registry (`LEVEL_SOURCES`):
    - TL0/TL1/TL2+: `trendline_built_at`. TL0 lines build at about 06:45; TL1 lines about 07:30.
    - ORB1: `ORB_0DTE_DISCOVERY_START_PT` 06:45.
    - Breakout: watch activation 07:30:15 (118 symbols).
  - **Keep-alive** (`DIR5M_KEEPALIVE_ENABLED`, in `dir5m_presample.on_discovery_quotes`). From that tick until the candidate executes, expires or retires, a symbol gets one P2 chain sample whenever its newest ring sample is older than its cadence:
    - `DIR5M_KEEPALIVE_SEC` 210 for armed and in-band symbols.
    - `DIR5M_KEEPALIVE_WIDE_SEC` 360 for other live candidates.
    - Any gap of 420 s or less between samples keeps a 180–600 s prior available at every moment.
  - **Must-execute sample:** an armed symbol whose newest sample is older than `DIR5M_ARM_NOW_SAMPLE_SEC` (60) is sampled on that tick.
  - **Order and caps:** armed first, then in-band, then the rest, oldest sample first. The keep-alive has its own cap, `DIR5M_KEEPALIVE_MAX_PER_MIN` (24), and stays behind the P0/P1 reserve. The manager's 285 s drain dedupe is lowered to `DIR5M_KEEPALIVE_DEDUP_SEC` (45) for `dir5m_keepalive` / `dir5m_20p` items.
  - **Telemetry:** the trigger source is `<src>:keepalive` (or `<src>:armed`), and `dir_pct_approach_lead_s` = lead from the first sample before must execute. With the keep-alive on, the lead clock runs up to 7200 s.
  - **`open_short` window** (`DIR5M_OPEN_SHORT_WINDOW_PT`, empty = off; deploy `06:30-06:36|06:45-06:51|07:30-07:36`). Inside those PT minutes only, when strict and fallback find no prior, a prior 60–180 s old is accepted (`DIR5M_OPEN_SHORT_MIN_AGE_SEC` 60). It applies to the decision and to asof snapshots, and is stamped `dir_pct_prior_window='open_short'` so training can filter it out. The two extra windows cover the TL0 line build (~06:45) and the TL1 / Breakout build (~07:30).
  - **20p guarantee, causal (t204u replaces the t202u on-demand fetch).** The 20p now-chain must be observed at or before `po_20p_event_at`; a chain fetched after the cross is never used (the t202u `DIR5M_SNAPSHOT_NOW_POST_SEC` / `DIR5M_20P_FETCH_*` defer is removed: it also let the 20p write slip past the event).
    - **Pre-cross prefetch** (`DIR5M_20P_PREFETCH_ENABLED`, `modules/po_mfe20_snapshot.maybe_prefetch_20p_chain`, every 2 s monitor tick, no broker call of its own). While the trade has not reached +20% and its live return `current_pnl_pct` is in [`DIR5M_20P_PREFETCH_MFE` 0.08, 0.20), one P0 chain fetch (reason `dir5m_20p_prefetch`) is queued whenever the ring's newest chain is older than `DIR5M_20P_PREFETCH_REFRESH_SEC` (30). At most one per `DIR5M_20P_PREFETCH_RETRY_SEC` (15) per trade and `DIR5M_20P_PREFETCH_MAX_PER_MIN` (6) overall. It stops at the event.
    - The 20p now-chain cap is `DIR5M_20P_NOW_MAX_AGE_SEC` (deploy 90; unset = `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC`). Older → `dir_pct_missing_reason=now_chain_stale` with its age in `dir_pct_now_chain_age_s`; no pre-event chain at all → `no_current_chain`. Never a post-event chain.
    - The 20p row carries `dir_pct_20p_prefetch_n` (prefetches queued for the trade).
    - Priors come from held sampling every 180 s (`DIR5M_OPEN_POSITION_RESAMPLE_SEC=180`, `_MAX_PER_MIN=12`; 10/15 peak was 34 concurrent held underlyings).
  - **Exit:** the held 180 s cadence plus `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC=210`. Now-chain and prior observed ≤ `po_exit_event_at`. There is no extra fetch.
  - **Budget** (calls/min, 2 Market calls per chain). Base is deployed rev 00601 (10/14 measured avg 39 / peak 102, plus approach 12/min ≈ 63 / 126).
    - Keep-alive: about 20 chains/min for 118 symbols at 360 s, capped at 24. It displaces most approach samples, so the net is about +30 avg / +36 peak.
    - Held at 180 s: about +2 avg / +11 peak.
    - 20p pre-cross prefetch (t204u): 10/15 replay ≤ 194 chains/day (0.5/min avg), ≤ 6/min by cap → about +1 avg / +12 peak.
    - Total about **96 avg / 185 peak** (coincident worst case, t204u). Against E*TRADE 240/min: avg 40 % (<50 %), peak 77 % (<80 %).
    - The peak is over 85 % of the 140 app cap, so the deploy raises `OPTION_API_HARD_CAP_PER_MIN` (new env; was hard-coded 140) and `CHAIN_FETCH_HARD_CAP_PER_MIN` to 180.
    - P2 limits move with it: `CHAIN_FETCH_ADAPTIVE_CEILING_PER_MIN` 60, `CHAIN_FETCH_LOW_PRIORITY_SAFE_PER_MIN` 150, `CHAIN_FETCH_HIGH_PRIORITY_SAFE_PER_MIN` 170.
    - The sampler drain goes 15 s → 5 s (`CHAIN_RESAMPLE_TICK_SEC=5`). At 3 per tick, the old 12 chains/min drain could not serve the queue.
    - The adaptive budget still holds total chain supply to 0.85 × cap.
  - **10/15 projection (214 executed trades).**
    - Handoff: 70.1 % now → **100 %** projected (98.6 % at a 30 s fetch lag; VZ breakout 07:31:44 is 89 s after activation). With `open_short` at 06:30-06:36 only, it is 212/214 (ASTS TL0 06:48:36, VZ).
    - 20p (t204u causal replay, 10 s fetch lag): pre-event now-chain ≤ 60 s on 32/33 with a clock (≤ 90 s: 32/33; ≤ 120 s: 32/33). The one miss is HIMS-28c-680 (no live premium ticks between entry and the cross in the path file, so the replay could not place a prefetch); QCOM-172.5p-088 has no entry clock in the files. Held-only 180 s would give 9/33 (≤ 60 s) and 16/33 (≤ 90 s). GOOGL-529 (178 s after its TL0 line build) relies on `open_short`.
    - Exit: 13.6 % → about 99–100 %.
    - Lead from the first signal before must execute to handoff: median 1944 s, p10 246 s, p25 930 s. 197/214 (92 %) are over 3 min. The 17 under 3 min are TL0 06:45–06:48 (7), TL1 07:32–07:34 (9) and VZ Breakout (1). 16 of the 17 were already stamped on 10/15 from pre-warm or approach samples.
- **t204u causality (Oct 15; build on a877103, next deploy).** Standard: `docs/0.POTemporalValidationFillSuccess.md` §9.1 (filled + correct + temporally causal).
  - **Handoff / decision.** The now-chain is the decision chain on the row (`tp_chain_observed_epoch`, else `chain_observed_at` / `po_chain_asof_at`). It must be observed ≤ the decision clock and ≤ `TP_FREEZE_CHAIN_MAX_AGE_SEC` (60) old at the freeze. Otherwise the value stays None with `dir_pct_missing_reason` `now_chain_after_freeze` or `now_chain_stale`.
    - Priors are ring samples strictly older than the now-chain observation, and `dir_pct_prior_age_s` is measured from that observation, not from the freeze. Gate stamp and handoff re-derive therefore pick the same prior.
    - A sample recorded after the decision chain (keep-alive, must-execute, or any later fetch before the snapshot write) is never a prior and never "now".
    - The decision chain is seeded into the ring at its own observation clock. Before t204u it was re-stamped at the freeze. A row with no chain clock is not seeded on the freeze path.
  - **20p / exit / other live snapshots.** The now-chain is the row chain or newest ring sample with observed ≤ the stage event and age ≤ the stage cap. Priors are strictly older than the now-chain. No post-event slack.
  - **Clock stamps.** Every derive stamps `dir_pct_asof_epoch` / `dir_pct_asof_at` (the decision or event clock), `dir_pct_now_chain_observed_at`, `dir_pct_prior_observed_at` (ISO UTC), `dir_pct_prior_age_s`, `dir_pct_now_chain_age_s` and `dir_pct_prior_window`. They ride in the envelope `po_dir5m` block (`PO_DIR5M_KEYS`). The decision freeze `dir5m` dict adds `now_chain_age_s`, `now_chain_observed_at`, `prior_observed_at` and `asof_at`.
  - **Causal invariant for certification:** `prior_observed_at < now_chain_observed_at ≤ asof_at ≤ stage event` (the freeze for handoff/executed).
  - **`dir_pct_prior_window`.** `strict` (240–420 s) and `fallback` (180–600 s) are the 5m value. **`open_short` (60–180 s, only inside `DIR5M_OPEN_SHORT_WINDOW_PT`) is causal but NOT a 5-minute value.** Training includes or excludes it explicitly; it is never pooled silently with 5m rows.
- **Trade Profile policy.** `dir_pct_change_5m` stays missing-PASS on every profile (0.0 = missing). It moves to missing-reject only once measured stamping is above 95 %. History: values before 10/8 were any-age priors (not a true 5-minute delta); do not mine them as 5m.

### Path / review — `path_chain_skew`

| Tuple | Contents |
|-------|----------|
| `PATH_CHAIN_SKEW_KEYS` | Chain vols, ratios, dir%, OI, totals |
| `PATH_OWNED_CONTRACT_KEYS` | `delta`, OI, greeks, `representative_*` |
| `PATH_DEPTH_KEYS` | `bid_size`, `ask_size`, `cumulative_volume`, `last` |
| `SESSION_TECH_PATH_KEYS` | MACD pack, SMA/EMA, RSI/VWAP, `volume_ratio`, PVE3 + `price_change_3m_pct` + `volume_3m`, ATR/BB/mom, `distance_from_vwap_atr`, `rs_vs_spy`, `session_tech_bar_at`, `session_tech_asof_at`, `pve3_bar_at`, `session_tech_observed_at`, missing flags |
| `OWNED_TICK_REVIEW_KEYS` | `otm_pct`, `call_share`, `put_share`, `dir_pct_change_5m`, `underlying_l1_imbalance` (derived on the owned tick; no extra API) |
| `REVIEW_MICROSTRUCTURE_KEYS` | Union for desk / PO review |

APIs: `stamp_review_microstructure`, `derive_direction_volume_pct`, `apply_skew_onto_osnap`, `seed_osnap_chain_skew_from_sources`, `overlay_live_session_tech`. Owned-tick copy: `_PATH_COPY_KEYS` includes session tech + `OWNED_TICK_REVIEW_KEYS`. Persist whitelist: `PREMIUM_JSONL_KEYS` in `profit_optimizer/path_jsonl.py`. See [profit_optimizer/README.md](../profit_optimizer/README.md#owned-tick-stamp-open-position-review-keys).

---

## 6. Tech Keys at Gates

| Consumer | Keys | Missing / invent policy |
|----------|------|-------------------------|
| **Convex pc_oi** | `put_call_oi_ratio` | Fail-closed |
| **Convex dir%** | chain vols → `direction_volume_pct` | Fail-closed when hard; live floor **>0.10** (not 0.54) |
| **Skip Signals / path SS** | sva, IV, seconds, dir%, VWAP, RSI, vol, L1d5, L1raw, break ATR, PVE, body/wick/close, ATM delta/otm from the same Layer C chain | **Miss-pass**. Hydrate at admit from bars+quotes + same chain fetch (`stamp_admit_tech`, restamp after merge). No extra chain for 9.2 fields. Log `QG_SS_FIELD_GAP` / `QG_PATH_SS_FIELD_GAP` / `QG_ADMIT_REBUILD` |
| **Convex E14** | `macd_histogram`, `vwap_distance_pct` | **Soft-skip** if `macd_missing` (no invent 0.0) |
| **HardGate volume** | `resolve_equity_volume_for_gate` | **Soft-skip** when missing (`0DTE_HARD_GATE_VOLUME_DATA_GAP`); floor only when trusted |
| **QGS equity.volume** | `volume_ratio` + missing | ORB/TL soft-skip; **BO fail-closed** |
| **QGS equity.vwap** | `vwap_*` | Soft-skip unless fail-closed extras |
| **QGS equity.rsi** | `rsi` + `rsi_missing` | Soft-skip on missing |
| **TL simple quality** | hub `volume_ratio` | Neutral / soft when missing; else floor (~0.80) |
| **BO quality** | hub `volume_ratio` | Default **fail-closed** on missing |
| **Hold@0.55** | `direction_volume_pct` / `hold_exit_dir_pct` | **Fail-closed** if require_dir |
| **ORB priority / momentum volume subscores** | catalog resolve | Missing → lowest / zero invent score (floors unchanged) |
| **Strategy volume boost (≥1.35)** | catalog resolve | Boost **only** when volume trusted (not missing/stub) |

**Hard-gate / quality floor numbers were not changed** by the Jul 31 volume system — only invent/missing handling.

---

## 7. Hold · Monitor · Path Refresh

### Path refresh (while held)

| Refresh | TTL | Where | Writes |
|---------|-----|-------|--------|
| Chain skew | 45s due / **60s** live | **Discovery loop** (async, max 1–2/scan; never owned eval). `force_live` when the bag is older than `chain_max_age_sec()` (60). A bag still inside 60s is not overwritten by a later tick. Older than 60s is emptied (`po_chain_source=stale_over_60s`); `*_now` is not copied from the last tick | call/put vol, OI → `meta.path_chain_skew` |
| Session tech | 60s | Owned eval, **cache-only** (1m hub; no extra equity quotes / Yahoo) | `SESSION_TECH_PATH_KEYS`, including time-stamped 3m PVE |

Owned eval **forward-fills** last `meta.path_chain_skew` only while that bag is ≤60 s old. An older bag is emptied. Hold@0.55 uses that stamp. Exact OCC bids remain the exit mark.

Session tech refresh runs **before** stealth evaluate so Hold sees current VWAP/MACD/RSI/ATR from the 1m hub. ATR/BB/momentum and `price_volume_efficiency_3m` come from **current** 1m bars. PVE retains its source `session_tech_bar_at` plus `session_tech_observed_at`, the receipt/upsert clock from `_session_tech_bar_fetched_at`. The 20% Save exit rejects a missing stamp or a receipt age over the shared `TP_FREEZE_SESSION_TECH_MAX_AGE_SEC` policy (90 seconds currently), rather than using entry-time PVE or treating a bar timestamp as the freshness clock. *(10/22: the retired PVE3 latch measured freshness from the bar time, `pve3_age_s` ≤ 75s. 11/03 replaced that latch with the stall save + UL-retrace; ON on Demo `00624-8bn`, CALL-only — see the Oct 22 / Nov 3 notes at top.)* Live RS `get_quotes` is skipped while the monitor holds the loop. Evaluate-path 20% snapshot capture does not wait 3s for hub (`allow_sync_refresh=False`, on `00625-wwv`).

### Hold

- First −10pp fire: require same-side `direction_volume_pct` ≥ Hold floor (default 0.55) when `OPTION_HOLD_EXIT_REQUIRE_DIR_VOLUME=true`.
- Stamps `hold_exit_dir_pct` onto osnap for PO / diagnostics.
- Equity `volume_ratio` is review-only on Hold — not the Hold admit key.

### Monitor

- Unified interval: `0DTE_POSITION_MONITOR_INTERVAL_SEC=2`. Wide book: `0DTE_MONITOR_WIDE_OPEN_COUNT=20` → `0DTE_MONITOR_WIDE_INTERVAL_SEC=3`. `Unified0DTEPositionMonitor._monitor_loop` always pauses after `maybe_tick` and clears `_0dte_force_monitor_tick` when the book is empty. Tick ids are `canonical_monitor_tick_id` → `u0dte_<ms>`. `_ul_refresh_loop` (2s) calls existing `refresh_underlying_spot_cache_async` so evaluate only reads `resolve_underlying_observation`; each tick resets UL labels. On `00625-wwv`: no 4s/30s cancel (`ul_refresh_slow` / `ul_refresh_hung`, cancel=false); OCC apply-on-complete; sequential inner batches of 25 with `yield_lane_to_equity_batch` between waves; OCC defer only while last exact reusable. **Local:** leftover OCC skip while owned; discovery scan slot free of a running watch; pret from this-tick bid only. Rollback `00624-8bn` still 4s-cancels UL. Flight-recorder samples use `align_recorder_to_monitor_interval` so they match that clock.
- Exact OCC quotes for exits; `cumulative_volume` from owned contract quote.
- Every open is exit-checked (`run_fair_owned_eval_tick`). Eval budget is a warning; it does not skip a name.
- **Persist for review is the Profit Optimizer compact JSONL**, not a separate monitor file. Every PATH_V2 eval → `_0dte_run_stealth_exit_decision` → `record_path_v2_eval_row` → `observe_owned_path_tick` → `stamp_eval_record_fields` → `stamp_live_rsi` + `stamp_owned_monitor_review_keys` → `observe_premium_tick` → `compact_premium_row`. HOLD stores `out=None`; later loops on the same tick reuse (`reuse_owned_tick_eval`) and do not write a second eval row. `PATH_V2_EVAL_COUNT` increments after evaluate, before the write. Row carries `eval_now` / `eval_age_s` (evaluate clock) plus monitor `timestamp`. **Local new rows** also keep `path_v2_peak` / `path_pret` / `path_v2_reason`, `quote_age_ms`, `contracts`, and modeled fees. Replay uses `eval_now` and sorts by parsed epoch. Per-trade JSONL flushes every **5s** (`PROFIT_OPTIMIZER_JSONL_MIN_WRITE_GAP_SEC`); the daily metrics file stays **45s** (`PROFIT_OPTIMIZER_PERSIST_MIN_WRITE_GAP_SEC`). `_line_count` is cached behind a lock and cleared after a GCS JSONL download. Restart rebuilds PATH_V2 (`attach_jsonl_paths` + `_restore_trade_from_dict`) with the fill/path entry time and does not reset a live HWM. Daily JSON is metrics-only (≤32 MiB); JSONL is the replay source. Cache-only session tech and Trade Profile review keys (PVE3, BB, VWAP-ATR, live `otm_pct`, shares, tape 5m dir%, L1 ring). No extra broker call. Cadence and key list: [PositionMonitoring.md](PositionMonitoring.md) · [profit_optimizer/README.md](../profit_optimizer/README.md#owned-tick-stamp-open-position-review-keys).
- **PATH_V2 pret (local):** `live_path_v2_pret_from_tick` uses this tick's option bid / entry − 1. Missing bid → pret None → STEP 0 HOLD. Never mark, `osnap` bid, or `pnl_pct`. Serving `00625-wwv` still assigned `path_pret = current_pnl_pct`.
- `pnl_pct` on the path row is bid vs entry (no fees). Replay / desk book P&L is fee-inclusive settlement (`net_result_pnl_dollars`: `(exit − entry) × contracts × 100 − contracts × $1.30`). Desk `mfe_pct` / `bid_mfe_pct` is the PATH_V2 bid peak; `mark_mfe_pct` is mark HWM. RESEARCH session JSON `exit_premium` is **not** the booked fill. Authoritative book is `docs/0.ProfitabilityThresholds/10.21/grokbot_books/pcv2_enriched_total_book_8.19-10.21.csv` (`contracts`, `entry_premium`, `exit_bid`, `pc_v2_pnl`). Do not invert qty from RESEARCH `exit_premium`. `PATH_END` on replay is end-of-tape HOLD, not a live exit. Historical jsonl may omit `entry_premium` / `path_v2_peak`; `prep_replay_ticks(..., entry_premium=)` joins a book entry at replay and does not rewrite the tape.
- RSI / PVE / BB / `distance_from_vwap_atr` are **1m bar** stamps. Age-gate on `session_tech_bar_at` / `pve3_bar_at`. Sessions 2026-10-23 and 2026-10-26 JSONL predate the PVE/BB/OTM persist whitelist.

---

## 8. Priority Optimizer Snapshots

**Lifecycle:** The live admit does not depend on the Priority Optimizer. `stamp_admit_tech` and `central_tech` fill the long-used Trade Profile keys. `dynamic_volume` is reserved for rolling volume and open-interest keys that are not built yet. A full PO snapshot (`FEATURE_KEYS_0DTE_UNIFIED`, schema v8) records these clocks under `research_app/priority_optimizer/execution_snapshots/`. Mine Entry from the handoff. Mine exit-vs-hold from the mid-trade clocks. Live mode can run with that recording off. Path SS rejects write an **entry-comparable** densified payload (`qg_admit` + `features_entry` + `path_ss_atoms`, local + async GCS) — **no** broker calls (cache / hot-path row only). Before accept/reject, `stamp_admit_tech` fills the signal from cached 1m bars, the last equity quote, the L1 ring, and the **same** Layer C chain fetch already on the row. If sva is still empty and the bar cache is cold (Cloud Run just booted), Layer C rebuilds **that one symbol** via `hydrate_equity_session_tech` on the **admit** path — reject capture itself never brokers. Admit keys stay on the signal row / metadata for the handoff.

| Family | Stage suffix | Clock | When it writes | Training use |
|--------|--------------|-------|----------------|--------------|
| `entry_handoff` | `*_emit_handoff` | `po_decision_freeze_at` | Gate accept | **Only** Entry training row |
| `entry_executed` | `*_options_executed` | copies the handoff freeze | Fill (failed fills skipped) | Fill identity; research keys match handoff |
| `mfe_20p` | `*_options_20p` | `po_20p_event_at` | First bid-return MFE ≥ +20% | First green runner |
| `age_120s` | `*_options_age120` | `po_age_120s_event_at` | First tick with age ≥ 120 s | Early hold / never-green |
| `first_red` | `*_options_firstred` | `po_first_red_event_at` | First tick with bid return `< 0`. Quote age is the live OCC clock on that tick (`po_monitor_quote_at`), not the entry equity clock. | First underwater print |
| `pullback` | `*_options_pullback` | `po_pullback_event_at` | Ladder (Oct 23, 2026): bid high `H ≥ +15%`; fires at `r ≤ H − dip`; next shot at `H ≥ max(30%, H_fire + 5 pp, H_fire × 1.25/1.5/1.5/1.5)`; max 4 shots named `{trade_id}__pullback_s{n}` | Give-back vs continue |
| `exit` | `*_options_exited` | `po_exit_event_at` | Sale | Exit outcome |

Mid-trade files (`age_120s`, `first_red`, `pullback`) are Research record-only (`modules/po_lifecycle_snapshots.py`). They never change Path V2, admits, or sizing. Each write seals `{date}__seal_{family}__{identity}.json` before the canonical object. Cloud Run uses `PO_SNAP_SYNC=true` so the monitor tick finishes the GCS commit. Age is wall-clock since the earliest fill/open (`po_opened_at`); Path V2 `age_seconds` cannot understate it, and persist re-arms from that clock even when evaluate returned early. The running peak never moves down; a pullback row records `H_fire` (the bid high at the fire tick) and the fire return, one frozen hub read per shot, and underlying retrace fields. A trade that reached +15% with no shot before exit carries `pullback_missing` / `late_fallback` on its exit file. Live owned greeks (delta/gamma/theta/vega/iv) ride the exact OCC quote into the stealth snap and overwrite a stale entry-chain overlay on mid-trade files. If that OCC print is missing, the greeks stay null instead of repeating the previous event. A mid/exit chain older than 60 s, or `age_120s` / `first_red` with no live chain, nulls chain volumes (`po_chain_source`). `owned_contract_volume_delta_5m` / `owned_contract_oi_change` use OCC samples recorded on the open trade; they stay null until about five minutes of samples exist. Flatten primes remaining open symbols once before the EOD flag. The per-minute write budget logs `over_min_cap` but does not delay a first-due snapshot past its event clock. Hydration is the same 20p path: event clock required, session tech sliced to bars closed by that clock, chain restamped only when the family allows it (`0DTE_PO_EXIT_CHAIN_ALLOW_STALE_SEC=60`; `age_120s` / `first_red` do not pull a live chain), no invented write-time clock. Executed copies the handoff freeze and does not restamp session tech. `breakout_watch_activated` is cache session-tech + ORB box (`po_fill_pct_scope=watch_activation`); it is not a 267-key training fill. Mid-trade envelopes add `lifecycle_event` (`FEATURE_KEYS_LIFECYCLE_EVENT`: `snapshot_type`, `event_ts`, `seconds_since_entry`, `pret`, `peak_pret`, `pullback_seq`, `pullback_shots_max`, `H_fire`, since-entry deltas, `window_lt_5m`). Retrieve reports the seven live families in `lifecycle_coverage` (older 50p files still parse).

**Entry/exit hydrate (cache-first):** `modules/po_snapshot_cache_hydrate.py` — session 1m TA + discovery quote cache first (`po_market_enrichment_status=session_tech_cache` / `cache`); broker `get_market_data_for_strategy` only on miss. Quote reuse and chain reuse read `max_age_by_source()` (quote 120 s, chain 60 s) unless the `0DTE_PO_*` env override is set. Options: reuse admit Layer C fields on entry (`cache_row`) only while the chain is inside that cap; exit prefers in-process chain TTL / `prefer_cache_only` while open exact OCC owns the budget. A sealed **executed** row returns `sealed_handoff` and does not restamp session tech or pull comprehensive. Densify fills remaining PO keys on executed snaps only — it does **not** apply a second chain fetch. Compact `qg_admit` on `watch_fsm` is the kill-safety pack (see §10).

### Densify path

`densify_feature_map(features, snapshot_strategy=…)`:

1. `ensure_volume_research_fields(src)` — aliases, totals, dir%, missing stamps  
2. `backfill_chain_totals_from_sides`  
3. Emit fixed schema (`easy_orb_0dte` / `easy_trendline_0dte` / `easy_breakout_0dte` → **267** unified keys) in usefulness order, null when absent. Densify canonicalizes `quality_gate_report.gate_id` to short stack ids (`RSI`, `Vol_Ratio`, `dir_pct`, …), overlays QG stamps onto `rsi` / `bollinger_position` / `quality_score` / `volume_ratio`, and rewrites opposite-side `direction_volume_pct` to same-side. `trendline_slot` and `iv` are first-class schema keys. Mining-dead keys in `FEATURE_KEYS_SNAPSHOT_DROPPED` are never densified.  

Entry snaps exclude exit-only outcome fields (`ENTRY_SNAPSHOT_EXCLUDED`).

### Entry/exit critical volume & tech (must populate when available)

Equity: `volume_ratio`, `orb_volume_ratio`, `volume_ratio_missing`, `volume_ratio_source`, `tech_source`, `session_bar_count`, VWAP/RSI/MACD + missing flags, `atr`, `momentum_10`, `rs_vs_spy`  

Chain: `call/put_chain_volume`, `call/put_chain_open_interest`, `put_call_*_ratio`, `total_chain_*`, `direction_volume_pct`  

Liquidity / depth: `representative_volume`, `cumulative_volume`, `delta`, `open_interest`  

Hold research: `hold_exit_dir_pct` (null OK on entry)

### PremiumPathSample (path ticks)

The durable series is compact `paths/{date}/{trade_id}.premium.jsonl` (`PREMIUM_JSONL_KEYS`). In-memory `premium_path` is the same row before persist. Daily metrics JSON does **not** hold the 2s tape.

Each live/shadow row keeps:

- Exact OCC tape: mark/bid/ask/mid/last, spread, bid/ask size, `option_l1_imbalance`, `cumulative_volume` (broker `volume` / `totalVolume`; missing stays null), `monitor_tick_id`, `evaluated`, `eval_now`, `eval_age_s`, quote age / `quote_age_ms`. `timestamp` = monitor/write clock. `eval_now` = evaluate clock (replay). `broker_quote_timestamp` = broker quote clock. `observation_timestamp` = write time
- Underlying provenance: `underlying_price` plus `underlying_source` / `underlying_age_ms` / `underlying_observation_timestamp` / `underlying_stale` (broker `date_time_utc`). Replay must not set UL quote time = write time. One owned refresh per tick (cap 25) stamps this clock; evaluate still refuses >8 s as unavailable
- Trade state: entry premium/underlying, HWM, `mfe_pct`, giveback, seconds since entry. **Local new recordings:** `path_v2_peak` / `path_pret` / `path_v2_reason`, `contracts` / `quantity`, modeled `opening_fees` / `closing_fees` / `round_trip_fees`. `actual_exit_*` only when a fill is already on metadata. Historical tapes before this pack omit those keys.
- Greeks when present: delta/gamma/theta/iv (sparse refresh; often sticky)
- Session tech from the 1m hub (no extra quote): RSI/MACD/VWAP/`volume_1m`/`volume_3m`/`volume_ratio`/ATR/BB/`price_volume_efficiency_3m`/`price_change_3m_pct`/`distance_from_vwap_atr` + `session_tech_bar_at` / `pve3_bar_at` / `session_tech_asof_at` + missing flags
- Derived Trade Profile review keys: signed `otm_pct`, `call_share`/`put_share`, tape `dir_pct_change_5m` (now vs 5m-ago when present), `underlying_l1_imbalance` from the equity L1 ring

`hold_exit_dir_pct` may be on the in-memory snap for Hold diagnostics; it is not required on compact JSONL. Legacy `volume` aliases to `cumulative_volume` when only one is set. Stamp functions: `stamp_live_rsi`, `stamp_owned_monitor_review_keys` in `profit_optimizer/trading_hooks.py`.

### Superior volume research checklist

For win/loss studies, score layers **separately** — never blend until proven:

1. **Layer A** — equity hub `volume_ratio` (trusted only)  
2. **Layer B** — `direction_volume_pct` + chain sides  
3. **Layer C** — `representative_volume` + `cumulative_volume`  

---

## 9. Watchlist & ORB Capture

### Watchlist

- Runtime: `data/watchlist/0dte_list.csv` only (**118** symbols + header) via `modules/watchlist_loader.py`.
- Tier split: **7** Tier 1 symbols / **111** Tier 2 symbols. Category split: **109** equities, **6** ETFs, **2** metals, **1** crypto.
- Shared by ORB capture, ORB SO, ORB 0DTE, Trendline, Breakout.
- `core_list.csv` is legacy reference — not loaded at runtime.
- Shared slots: `0DTE_SLOT_COUNT=50` / `TOTAL_0DTE_MAX_POSITIONS=50` (`Account0DTE.env`; live `easy-etrade-strategy-00624-8bn`, first pinned 10/13 in `00599-lhd`). Path caps are **50**. RESEARCH per-open cap **$600**, no contract cap. FINAL Live Pilot ($60 · 1 contract, cap 7) is retired.

### Discovery watch timing (PT)

ORB1, Trendline, and ORB2 are the discovery watch. The 7:15–7:30 Opening Range scan and the 7:30 Convex batch are not in the runtime.

| Window | What runs |
|--------|-----------|
| 6:30–6:45 | ORB #1 capture |
| 6:45–7:25 | ORB1 Breakout watch and TL0 (`DISCOVERY_ORB1_TL0_*`; end exclusive at 07:25) |
| 7:25–7:30 | Gap — no ORB1/TL0 discovery; ORB #2 capture finishes (freeze at 07:30) |
| 7:30–12:30 | Trendline TL1–TL2+ and ORB2 Breakout watch |

Discovery (ORB1, Trendline, ORB2): `0DTE_DISCOVERY_SYMBOLS_PER_TICK=24` · `0DTE_DISCOVERY_INTERVAL_SEC=10`. A full 118-symbol rotation is `ceil(118 / 24) * 10 = 50` seconds when each scan finishes inside 10s. The same task evaluates those quotes and sends a break to Trade Profile. A slow exit quote does not skip that evaluation. Each scan can request TL2+ Yahoo structure hydrate; `YahooBackfillManager` is the required control layer for those requests. Exact OCC stays priority 0 on the pacer — see [PositionMonitoring.md](PositionMonitoring.md).

---

## 10. GCS · Persistence · Secrets · Telemetry

### Namespaces

| App | `gcs_namespace()` | Service |
|-----|-------------------|---------|
| RESEARCH | `research_app` | `easy-etrade-strategy` |
| FINAL | `final_app` | `easy-etrade-strategy-live` |

Isolation is by **service / checkout**, not by `MODE=demo|live` alone. Helpers: `gcs_namespaced_coord_prefix`, daily markers, PO snapshot / profit_optimizer / daily_signals prefixes under `{namespace}/…`.

### Persistence required for app performance {#persistence-required-for-app-performance}

Cloud Run **scale-to-zero**, mid-session **redeploy**, and instance recycle wipe process memory. The stores below are the **must-persist** plane that keeps trading geometry, execution dedupe, open risk, and broker auth continuous. Without them the app can redraw wrong ORB/TL levels, double-fire SO, drop exit monitors, or lose OAuth.

**API:** `modules/gcs_persistence.py` · **paths:** `modules/runtime_mode.py` · **day markers:** `modules/daily_run_tracker.py` · local cache `DAILY_MARKER_DIR` (default `/tmp/easy_etrade_markers`, ephemeral). JSON uploads must pass **`content_type`** on the multipart body (`upload_from_string(..., content_type=...)`) — setting `blob.content_type` alone is not enough for `snapshot.json` / `head.json` / `actions.json`.

#### Must-persist keys (trading-critical)

| Feature | Persist keys / fields | GCS path | Why it must survive restart |
|---------|----------------------|----------|-----------------------------|
| **ORB#1 freeze** | `orb_high`, `orb_low`, `capture_time`, `captured_at`, snapshot by symbol | `{ns}/daily_markers/{date}.json` → `orb` | Mid-day re-capture changes the 06:30–06:45 box → wrong ORB1 breaks |
| **ORB#2 freeze** | same shape; session 06:30–07:30 H/L | same → `orb2` | TL1 + Breakout unlock require post-07:30 day-locked H/L |
| **TL0–TL6 geometry** | A1/A2 (`ts`, `price`, `source`), `slope_per_second`, intercept, slot, expected side; TL2+ inject flags; refined ORB2 extreme ts | same → `trendline_geometry` | ORB2 box alone ≠ drawn lines; never overwrite a valid candidate key |
| **TL1 pending** | `tl1_build_pending` (`record_tl1_build_pending` / `get_tl1_build_pending`); `hydrate_keep_tl1_pending` for TL0-only hydrate | same → `daily_markers` | After a kill, post-08:00 **run** unfinished TL1 — do not stamp complete; TL0-only hydrate must not consume the primary TL1 attempt |
| **SO collection / execution** | signals list, counts, `so_execution_batch_completed` | same → `signals` | Prevents double 7:30 collection / re-execution (paused in watch mode) |
| **Watch FSM + QG admit** | CONFIRM/READY/EXECUTED + compact `qg_admit` (sva, IV, seconds, dir%, VWAP, vol, RSI, BB, L1d5) + TL `material_break_at` | `{ns}/daily_markers/{date}.json` → `watch_fsm` | Mid-session kill must not lose a hot envelope or SS keys. 1m bars / L1 ring stay ephemeral and rebuild via `hydrate_equity_session_tech`. Missing stamps still miss-pass. |
| **signal_collection_730** | `pending_so_signals`, `allowed_so_symbols`, PO stamps | `{ns}/daily_markers/signal_collection_730/{date}.json` | Canonical SO allowlist across instances |
| **validation_open_700** | open prices by symbol | `{ns}/daily_markers/validation_open_700/{date}.json` | 7:00 and 7:15 may hit different instances |
| **validation_candle_715** | GREEN/RED validation payload | `{ns}/daily_markers/validation_candle_715/{date}.json` | Same — candle truth for collection |
| **Portfolio Protection Step5** | `block_so_execution`, `red_day_filter_blocked`, trigger, metrics | `{ns}/daily_markers/portfolio_protection_step5/{date}.json` | Red Day / block_so must not be lost between instances |
| **Demo options ledger** | `account_balance`, `starting_balance`, `booked_equity`, `peak_equity`, `withdrawals_total`, `equity_curve[]` (`{t, equity, delta, kind}`), `active_positions[]`, `closed_positions[]` | `{ns}/mock_options_history.json` | Re-arm stealth monitors for open 0DTE/TL after recycle. Compact book is the durable P&L curve (desk KPI/chart). Fat `closed_positions` is Event Tape only: age **2 calendar days** (`MOCK_OPTIONS_LEDGER_MAX_AGE_DAYS`, clamped 1–2) then newest **400** (`MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES`). **Write off the asyncio loop** (`_schedule_persist` / `_persist_loop`) so HTTP `/health` cannot starve |
| **Live closed ledger** | broker-confirmed `closed_trades[]`, `starting_balance`, `booked_equity`, `peak_equity`, `withdrawals_total`, `equity_curve[]` | `{ns}/live_options_history.json` | EOD / certification when broker history alone is incomplete. `_save_ledger` **keeps** persisted `starting_balance` (do not clobber from `0DTE_LIVE_STARTING_BALANCE` after first seed). Fat closes: count **400**, **no** age prune |
| **Open books / stealth HWM** | open-position stealth state | flushed ~**8s** via `persist_open_option_books` | Restart must not see a 0% trail on a live winner (`stealth_restart_gap` rebases ≥45s gap or ≥25pp stale HWM) |
| **EOD flatten resume** | `eod_flatten_complete_*`, `eod_flatten_batch_alert_*` | `{ns}/daily_markers/` + alert markers | After a kill during 12:50 flatten, resume until the book is empty — one EOD reason, all three paths |
| **Open recovery** | Demo actives from ledger; Live from broker `get_positions()` | ledger / broker | Exit path continuity |
| **E\*TRADE OAuth** | oauth token + secret (1 ENABLED version) | Secret Manager | Quotes/orders after container death |

**Day-freeze policy:** valid ORB1 (≥06:45), ORB2 (≥07:30), and TL geometry candidate keys are **not overwritten** unless `force=True`. Next calendar day writes a **new** `{date}.json` — it does not replace yesterday’s file.

#### Ops dedup (must persist for alert hygiene)

| Feature | Path | Retention |
|---------|------|-----------|
| Morning / holiday Telegram | `{ns}/alert_markers/*.txt` | 60d lifecycle |
| SO collection Telegram | `{ns}/signal_collection_markers/*.txt` | 60d |
| EOD report claim | `{ns}/eod_markers/*.txt` | 60d |

The morning / holiday marker covers the single pre-market operator alert for that service namespace: `{ns}/alert_markers/morning_alert_sent_{mode}_{service}_{PT date}.txt`, written only after a successful send and checked before every send. On `MARKET_CLOSED`, `LOW_VOLUME`, and `PRE_HOLIDAY` dates, the Holiday / Pre-Holiday message replaces the normal Good Morning alert and trading stays disabled for that session. Those three reasons are the only calendar off-switch; a listed Day Before … day does not create an extra `PRE_HOLIDAY`. Calendar policy: [MarketCalendar.md](MarketCalendar.md).

#### Market calendar data

The calendar is **computed, not stored**. No GCS object or database holds the holiday list.

| Data | Where | Notes |
|------|-------|-------|
| Skip days (`MARKET_CLOSED` / `LOW_VOLUME` / `PRE_HOLIDAY`) | `modules/dynamic_holiday_calculator.py` → `should_skip_trading` (computed per ET date) | Previous **market session** before a bank holiday or standalone low-volume day. A listed **Day Before …** skip is not a `PRE_HOLIDAY` source (2026-11-24 / 2026-12-22 trade). Policy: [MarketCalendar.md](MarketCalendar.md) |
| Early closes | `calculate_early_close_days` (computed) | 13:00 ET / 10:00 PT |
| Session clocks | `configs/Shared.env` §5 → `modules/session_clock.py` | PT walls; DST via `zoneinfo` |
| Trading-loop skip flag | In-process `_holiday_skip_today` / `_holiday_checked_date` | Ephemeral; recomputed on the first loop pass of each ET date |
| Morning / holiday Telegram dedup | `{ns}/alert_markers/` | One marker per mode, service and PT date |
| Reference notes | `data/holidays_custom.json` | 2025–2026 notes; not a skip or hours gate (`should_skip_trading` / `is_trading_day` / early-close ignore it) |
| Desk calendar | Desk UI `public/js/market-calendar.mjs` (computed in the browser) | Mirrors the backend; verified day-by-day by `scripts/test-calendar-crosscheck.mjs`. The desk does not read any calendar data from GCS |

#### Research / telemetry (not live gates)

| Feature | Path | Retention |
|---------|------|-----------|
| PO execution snapshots | `{ns}/priority_optimizer/execution_snapshots/` | **2d** GCS lifecycle (+ in-app KEEP_SESSIONS) |
| PO daily_signals | `{ns}/priority_optimizer/daily_signals/` | ~2d in-app + **2d** GCS lifecycle |
| TL break snapshots / outcomes | `{ns}/priority_optimizer/trendline_break_snapshots/` · `…/trendline_outcomes/` | **2d** GCS lifecycle |
| Profit optimizer flight | `{ns}/profit_optimizer/` | ~2d in-app; retrieve daily for local archive |
| Exit monitoring | `exit_monitoring/` (flat; **writer off**) | 14d if enabled |

#### Intentionally ephemeral (rebuild after boot)

Session tech / VWAP / RSI / MACD · TL `_bar_cache` / 1m bar cache · equity L1 quote ring · slot capital counters · in-process alert flags. These go stale across a Cloud Run gap; do **not** persist them. After boot, rebuild bars via the hub / `hydrate_equity_session_tech` (one-symbol if sva is empty at admit). L1d5 miss-passes until ~5s of quote polls refill the ring.

**SIGTERM vs hard kill:** `flush_watch_fsm_on_shutdown` writes `watch_fsm` (including compact `qg_admit`) on SIGTERM. A hard kill without SIGTERM can still lose ~5s FSM debounce and ~8s open-book flush. Compact `qg_admit` on the FSM blob is the admit-key insurance; missing stamps still **miss-pass**.

#### Key env

| Env | Role |
|-----|------|
| `GCS_PERSISTENCE_ENABLED` | Master GCS switch (default true) |
| `DAILY_MARKER_DIR` | Local marker cache |
| `MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES` | Demo fat-tape count cap (default **400**; **0** = off). Lives in `Account0DTE.env` |
| `MOCK_OPTIONS_LEDGER_MAX_AGE_DAYS` | Demo fat-tape age prune (default **2**, clamped **1–2**). Code default even if unset in env |
| `LIVE_OPTIONS_LEDGER_MAX_CLOSED_TRADES` | Live fat-tape count cap (**400** in Account0DTE). No age prune |
| `0DTE_LIVE_STARTING_BALANCE` | Seeds an **empty** live ledger only |
| `PRIORITY_OPTIMIZER_DAILY_SIGNALS_RETENTION_DAYS` | Daily signals prune (~2) |
| `EXIT_MONITORING_ENABLED` | Must stay **false** in prod |

Cross-refs: [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · [0DTETrendline.md](0DTETrendline.md) · [Cloud.md](Cloud.md#telemetry--gcs-data-plane-may-2026).

### Key GCS prefixes (under namespace)

| Prefix | Purpose | Retention (typical) |
|--------|---------|---------------------|
| `mock_options_history.json` | Demo unified 0DTE ledger (cash + compact book + fat closes) | Fat closes: **2d** + **400**. Compact `equity_curve` is durable |
| `live_options_history.json` | Live closed ledger + compact book | Fat closes: **400** (no age prune). `starting_balance` sticky |
| `desk_public/snapshot.json` | Hosted desk snapshot (open book Cur% / Unrealized, KPIs, tape) | **Cloud Run writer** `modules/desk_snapshot_pulse.py` on serving `00624-8bn`. Marks come from executor memory — no extra Market API. Local `127.0.0.1:4174` is preview only and does not write production |
| `desk_public/head.json` | ~200B change detector (`seq`) for hosted poll | Same Cloud Run pulse as snapshot. Hosted desk HEAD/ETags this every **1.5s**; a GET of snapshot follows only when `seq` / ETag changes |
| `desk_public/actions.json` | Fill/close pulse (Telegram analog) | Cloud Run at fill/close |
| `desk_public/discovery.json` | Scan-window pulse (desk chips = **SYMBOL·$price**) | Cloud Run after each unified 24/10s rotate |
| `desk_public/mock_options_history.json` | Slim public ledger (desk fields only) | Written at Demo seed (`demo_sim_ledger_seed.py`); not the fat private book. The hosted desk does **not** read it (`desk-config.js` reads only head / snapshot / actions / discovery) |
| `daily_markers/` | Per-day run state (ORB/TL/SO/PP/`watch_fsm` + `qg_admit`) | **2d** GCS lifecycle |
| `priority_optimizer/execution_snapshots/` | 7:30 / entry-exit PO JSON | **2d** GCS lifecycle + in-app prune |
| `priority_optimizer/daily_signals/` | SO list export | **2d** GCS lifecycle |
| `profit_optimizer/` | Flight recorder: metrics JSON + 2s JSONL under `paths/` | **2d** in-app and GCS (`daily/` · `paths/` · `cohorts/`); local archive is durable |
| `alert_markers/` · `eod_markers/` · `signal_collection_markers/` | Telegram dedup | **14d** GCS lifecycle |

**Desk writer vs hosted poll (Oct 27; in serving `00624-8bn`).** The hosted page is only a reader. It polls `head.json` every **1.5s** (`desk-config.js` `pollMs: 1500`) and GETs `snapshot.json` when the ETag / `seq` changes. That poll cannot invent new marks. `desk_snapshot_pulse.py` keeps a background heartbeat thread. While the book is open it reads in-memory marks, maps Cur% / `$` with fee-inclusive `net_result_pnl_dollars` / `net_result_pnl_fraction` (`modules/options_trade_economics.py`), bumps `head.seq`, and uploads snapshot + head. Interval `DESK_SNAPSHOT_MARK_INTERVAL_SEC` default **1.0**. Fill/close still flush immediately. Empty book: heartbeat sleeps. The trading / monitor asyncio loop is not blocked. Hosted Cur% should move about once a second while positions are open (poll 1.5s + writer 1.0s). See [Frontend.md](Frontend.md#live-freshness) · [Configs.md](Configs.md#desk-snapshot-pulse).

Lifecycle: `scripts/apply_gcs_data_lifecycle.sh` — see [Cloud.md](Cloud.md#telemetry--gcs-data-plane-may-2026).

### Secrets (Rev 00358)

OAuth and Telegram tokens live in Google Secret Manager. The trading service reads the current ENABLED production token. One live-data step, `restore_live_data_access()` in `modules/live_data_access.py`, reloads that token, proves a live quote, and attaches 0DTE, Trendline, and Breakout if they were skipped because the process booted before the token was valid. Do not keep old ENABLED versions. Verification: [OAuth.md](OAuth.md) and Secret Manager IAM for the Cloud Run service account.

### Telemetry bounds

| Writer | Default |
|--------|---------|
| Exit monitoring GCS | **Off** (`EXIT_MONITORING_ENABLED=false`) |
| Execution snapshots | Slim GCS; local skip on Cloud Run when configured |
| Trendline JSONL | Size-capped (`TELEMETRY_JSONL_*`) |
| Cloud Logging | Prefer `LOG_LEVEL=WARNING` in prod when not debugging |

---

## Appendix A — Technical Key Priority Catalog

Replay usefulness, not the persisted column order, and not the Trade Profile order. The twenty Trade Profile keys stay in `TRADE_PROFILE_KEYS` with their groups: Participation, Setup, Momentum, Market context, Microstructure, Liquidity, then Contract. Unified schema **267** keys. `SNAPSHOT_SCHEMA_ORDER` is the stable snapshot layout. `USEFULNESS_ORDER` is this research ranking. ORB SO schema **85**. Entry/exit critical **107**, in usefulness order.

### Highest-priority families

| Priority | Family | Canonical keys | Correct use |
|---:|---|---|---|
| 1 | Active Entry, Moon, Skip research | `call_chain_volume`, `put_chain_volume`, `representative_call_delta`, `representative_put_delta`, price-volume efficiency, break distance, chain-volume deltas, chain open interest, `rsi`, `implied_volatility`, greeks, `spy_change_pct`, VWAP distance, `atr`, one-minute volume, `break_level` | Research tier in `USEFULNESS_ORDER`. Not the snapshot column order. |
| 2 | Admitted-book separators | `open_interest`, `priority_score`, `velocity_pct`, `confidence`, `otm_pct`, `session_vs_avg_volume`, `macd_signal`, `put_call_oi_ratio`, `call_chain_open_interest` | Strongest remaining Cliff ranks on the 170 |
| 3 | Pre-chain tape | `same_side_chain_volume`, `call_volume`, `put_volume`, `representative_volume`, `volume_5m`, `cumulative_volume` | 8/19–9/23 replay score after the floors removed the chain tail |
| 4 | Path stamps | `break_level`, `break_timestamp`, `underlying_volume_1m`, `iv` | Sit immediately after the live 16 |
| 5 | Identity | `symbol`, `side`, `option_side`, `trade_id`, `current_price` | Read the row. Not separators. |
| 6 | Weak / observe | premium trajectory, missing-flags, SMA/EMA tails | Bottom of the snapshot |

### Frequently referenced keys (desk shortlist)

`call_chain_volume` · `put_chain_volume` · `representative_call_delta` · `representative_put_delta` · `price_volume_efficiency_3m` · `break_distance_pct` · `call_chain_volume_delta_5m` · `total_chain_open_interest` · `rsi` · `total_chain_volume` · `implied_volatility` · `spy_change_pct` · `theta` · `vwap_distance_pct` · `distance_from_vwap_atr` · `dir_pct_change_5m` · `open_interest` · `priority_score` · `velocity_pct` · `confidence` · `symbol` · `side` · `trade_id`

Lower-frequency but still in the optimizer contract when populated: MACD cross pack, Bollinger set, viability/spread/representative fields, chain sanity helpers, exit observation stamps (`hwm_*`, giveback, decision-vs-fill), SO-only `exec_volume_ratio` / `volume_color`.

---

## Appendix B — Legacy Notes

- **Rev 00236:** Broker-only quotes for prices/ORB/execution (third-party quote fallback removed).
- **Jul 16 session tech:** Introduced RTH 1m session VWAP/MACD for Convex/TL.
- **Jul 28:** Chain-skew Layer C (pc_oi + dir% &gt; 0.54), discovery 24/30s, Hold@0.55 reclaim design.
- **Jul 31:** Volume catalog + trust flags; HardGate soft-skip on missing equity vol; E14 soft-skip on missing MACD; Yahoo **daily** hist default-off; superior volume PO densify (`ensure_volume_research_fields`); no entry-FF for path ATR/RS.
- **Aug 4:** Unified hub invent-clear (RSI/MACD null when missing); bounded Yahoo RTH **1m** session-tech backfill when closes &lt; 15 (`0DTE_SESSION_TECH_YF_1M_BACKFILL`); **batched** Yahoo 1m for TL1 A2 + watch TL2/TL3 hydrate (`TRENDLINE_BUILD_YF_1M_*` / `TRENDLINE_WATCH_YF_1M_*`); explicit third-party data register; empty TL1 seeds `_bar_cache`; hydrate-before-inject; synth last-resort (no ORB H/L baked into bars); TL `orb_*_extreme_ts` ingest stub @ 06:30 PT; chain API `expiryYear/Month/Day` + `noOfStrikes`; same-day 0DTE first, then closest next expiry; SO Step5 no invent overwrite; E\*TRADE fallback placeholder nulls; PO schema identity dedupe.
- **Aug 5:** Yahoo **1m backfill sets correct TL1 A1+A2 timestamps** (`structure_1m_match` + hull bar) and **TL2/TL3 A1+A2** via watch hydrate — drawn slopes no longer use the 06:30 placeholder; prices stay broker; doctrine documents why yfinance is required under E*TRADE until a broker supplies requestable 1m candles; refine tols `TRENDLINE_ORB2_TS_REFINE_*`. Mirror in FINAL docs.
- **Aug 15:** Added `YahooBackfillManager` as the central third-party request manager for approved Yahoo RTH 1m backfills. TL0, TL1, watch TL2+ hydrate, and session-tech thin-history requests route through async manager entry points with coalescing, cache TTLs, process-wide pacing, bounded concurrency, pressure backoff, and `YAHOO_BACKFILL_MANAGER` telemetry. Discovery cadence is now 10s and runtime 0DTE watchlist is 148 symbols, increasing the need for managed Yahoo request flow.
- **Aug 16:** Expanded Tier 2 with large-cap energy, consumer/restaurant, utility, alternatives, mining, and crypto-miner names. Active watchlist now has 148 symbols: Tier 1 = 7, Tier 2 = 141; full discovery rotation is ~70 seconds at 24 symbols per 10-second tick. `TRENDLINE_BUILD_YF_1M_MAX_SYMBOLS` / `TRENDLINE_WATCH_YF_1M_MAX_SYMBOLS` raised to **148** so TL0/TL1 Yahoo 1m covers the full list (was 100 — that would have dropped the last 48 CSV names from structure backfill).
- **Aug 17:** TL2–TL4 A1/A2 use minute-aligned Yahoo 1m only (live quote ticks excluded; empty Yahoo → skip inject). Synthetic ×1.001 A2 refused. Stub TL2–TL4 rows are not day-frozen so plots use Yahoo 1m A1/A2. Yahoo provider OHLC snaps that minute to `:00`. TL2 A1 may be a Yahoo extreme **beyond** the ORB2 box; TL3/TL4 A1 is that extreme 1m bar. ORB1/ORB2 broker H/L still define the box and TL0/TL1 A1 prices.
- **Aug 25:** Added **USD** (ProShares Ultra Semiconductors) and **TTWO** (Take-Two Interactive). Watchlist is **150** symbols (Tier 1 = 7, Tier 2 = 143). `TRENDLINE_BUILD_YF_1M_MAX_SYMBOLS` / `TRENDLINE_WATCH_YF_1M_MAX_SYMBOLS` raised to **150** so Yahoo 1m covers the new names.
- **Aug 26:** Shared slots **50** both apps. Geometry **TL0–TL6**. Crash-recovery persist: `tl1_build_pending`, off-loop mock-ledger GCS, ~8s open-book flush, EOD flatten resume, `stealth_restart_gap`. HTTP `/health` liveness retired in deploy scripts (TCP :8080). No Yahoo quote-tick / synth A1/A2.
- **Aug 27:** Cut 18 never-filled names (AZN, BJRI, BMO, DINO, DPZ, DRI, EAT, EIX, EQNR, LIN, MUFG, SHW, SJM, SYK, TM, UBS, WING, YUM). Added **VSXY** (Victoria's Secret) and restored **USD** (ProShares Ultra Semiconductors). Watchlist was **133** symbols (Tier 1 = 7, Tier 2 = 126). Discovery rotation ~60 seconds at 24/10s. Yahoo 1m caps stay **150**. Mirror in FINAL `data/watchlist/0dte_list.csv` and collector copy.
- **Oct 20:** Cut 15 names with zero Total Results fills 8/19–10/19 (`HSY`, `FDX`, `ASML`, `MELI`, `RNG`, `ETN`, `TTE`, `CAT`, `DHR`, `BLK`, `UNP`, `CNQ`, `MPC`, `EMR`, `USD`). Kept **SONY** and **LMT** (also never filled). Watchlist is **118** symbols (Tier 1 = 7, Tier 2 = 111; 109 equities, 6 ETFs, 2 metals, 1 crypto). Discovery rotation `ceil(118/24)*10` = **50s**. Yahoo 1m caps stay **150**. Source: `data/watchlist/0dte_list.csv`.
- **Aug 31:** QG admit hydrate — Skip Signal / QG keys (sva, ATM IV, seconds, dir%, VWAP, RSI, vol, BB, L1d5) stamp **before** accept/reject from cached bars + last quote + the **same** Layer C chain fetch. Full 89-key PO snapshot stays **execution-only**. Compact `qg_admit` persists on `watch_fsm`. Live on RESEARCH Demo **`00527-64b`**.
- **Sep 1:** Owned eval is cache-only for path chain-skew (no sync E\*TRADE chain on the 2s loop). Layer C QG chain stays on discovery/entry: yield to monitor, reuse cache up to 600s while open, live cap 2/scan. Held path chain refresh is discovery-async (max 1–2). Log hygiene: borderline spread 30s throttle, chain-stale 60s, cache hits DEBUG.
- **Sep 3:** RESEARCH Demo **`00532-djh`** — PO entry/exit cache-first hydrate + Path SS LS100-93 live. FINAL Live not deployed.
- **Sep 4:** Market API priority gate fix in RESEARCH+FINAL `broker_api_pacing` (holder-stack recompute on release · pace after release). Deploy scripts pin Cloud Run **8Gi** + `0DTE_EXIT_QUOTE_BATCH_CONCURRENCY=4`. Live RESEARCH still **`00532-djh`** (`4Gi` / concurrency **2**) until **`deploy now`**.
- **Sep 16 EOD:** RESEARCH Demo **`00545-xvl`** (replaced `00544-xtp`). `$1M` demo ledger preserved.
- **Sep 17 EOD:** RESEARCH Demo **`00546-m6c`** — true-to-live Demo STC @ bid + $0.65/contract/side; `desk_public/actions.json` + `discovery.json` live. FINAL Live not deployed.
- **Sep 18 (local, no strategy deploy):** Compact booked-equity book on Demo + Live ledgers (`modules/equity_curve_book.py`). Demo fat `closed_positions` = **2 calendar days** + **400**. Live fat closes = **400** only. GCS JSON `content_type` on upload body. Desk public `head.json`. Cloud Run remains **`00546-m6c`** until `deploy now`.
- Historical Cloud Run pins (`00370-trm`, `00438-qhn`, `00450-nrm`, `00451-kpx`, `00460-*`) live in session summaries under `doc_elements/Sessions/` — operational truth is the **latest ready revision** on the service above.

---

*End of Easy 0DTE Data Guide. When adding a new key: declare its **kind**, writer, trust flag (if inventable), gate policy, and PO densify membership in this file and in `volume_data.py` / `po_feature_schema.py`.*
