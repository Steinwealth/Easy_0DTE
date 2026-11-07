**Oct 18 2026 — Trade Profile 10.17 (local, staged).** `easy0DTE/configs/trade_profile_10.17.json` remains available as the predecessor to 10.18. Its current verified direct replay is **337 trades, 242W/95L**, wins `+$124,903.92`, losses `-$8,017.16`, net **+$116,886.76**. The added existing-template bounds are TL0 PUT `put_share >= 0.1409958494039479`, TL1 CALL `chain_sanity_best_same_side_spread_pct >= 2.0101010101010122`, and Breakout PUT `gamma >= 0.065445`; together they provide a **+$141.50** net lift. ORB1 CALL uses the valid template share key `call_share >= 0.5630386091197992`; the non-template `call_chain_volume_share` key is not used.

**Oct 18 2026 — Trade Profile 10.18 (historical; was current on 10/18; local, not deployed).** `TRADE_PROFILE_VERSION=10.18` selects `easy0DTE/configs/trade_profile_10.18.json`, using the inherited 10.16 27-key CALL/PUT schema. Same-side `total_chain_vol_oi` minimums are filled on all ten path/sides, with TL2+ CALL optimized to `>= 0.665741751192113`; the existing TL1 PUT maximum `<= 8.424821046079652` remains. Missing values remain pass. The approved TL0 CALL addback is active in its existing template slots: `put_chain_volume <= 14454.000001`, `put_chain_open_interest <= 21570.000001`, and `break_distance_pct >= 0.00090538120145535`; the proposed TL1 CALL `call_chain_open_interest >= 7107.999998999` addback remains rolled back. The approved 8/19–10/19 source-backed continuation is **355 trades, 251W/104L**, wins `+$129,682.92`, losses `-$8,514.36`, net **+$121,168.56**. No deployment was performed.

**Oct 20 2026 — Trade Profile 10.20 session amendment (local, staged).** The 133-trade 10/20 Research population is complete and replayed with the updated local Profit Capture engine. Total Results contribution: **29W/104L, net −$6,348.90**. The 10.20 JSON admits **2 losses, net −$156.00**. From the current canonical 357-row 10/19 profile, the append-only 8/19–10/20 Trade Profile book is **359 trades, 252W/107L, wins +$131,684.12, losses −$8,778.16, net +$122,905.96**. The row-level admission artifact is `docs/0.ProfitabilityThresholds/10.20/pcv2_canonical_trade_profile_10.20_book_8.19-10.20.csv`; its slot audit maps every Trendline slot 2 or greater to TL2+. The earlier 377-row / +$124,450.46 figure was an invalid projection from globally loosened bounds and is not active. See `docs/0.ProfitabilityThresholds/10.20/PCV2_REPLAY_BOOKS_8.19-10.20.md`. **Superseded:** after the approved t210u/t213u bound changes, 10.20 (now sha `e3a59f89…` after t219u, commit `af5c0a1`, 8/19–10/21 370 / 264W/106L / +$124,673.66; earlier `26996e85…` (commit `041d1f2`; metadata-only cleanup of t213u `c7aafe0a`, resolved bounds identical; admit-stamp `bollinger_width` in `01c77e9`)) confirms **370 / 263W/107L / +$124,649.06**.

**Replay input repair:** use the enriched Total Results copies for future path/slot-aware mining: `docs/0.ProfitabilityThresholds/10.19/pcv2_enriched_total_book_8.19-10.19.csv` and `docs/0.ProfitabilityThresholds/10.20/pcv2_enriched_total_book_8.19-10.20.csv`. They repair all 128 affected 8/26 Trendline rows using local Priority Optimizer and certified decision-handoff sources. Slots 0/1 remain TL0/TL1; slots >=2, including TL3 and higher, are TL2+. Raw canonical books and the approved 359-row admission artifact are preserved unchanged. Audit: `docs/0.ProfitabilityThresholds/10.20/REPLAY_PATH_SLOT_ENRICHMENT_AUDIT_8.19-10.20.json`.
**10.18 validation contract:** the 8/19–10/19 replay is valid only at **355 trades, 251W/104L, net `+$121,168.56`**. The 351-row / `+$119,674.56` result is the pre-TL0 CALL baseline. Any result below `+$121,168.56` is invalid and indicates a replay population, evaluator, or configuration mismatch.

The 10.18 JSON is canonicalized to one effective override per path/side and template key, in the official 27-key CALL/PUT order. Historical duplicate override records and rolled-back addback records are not active configuration. Unbounded template keys are ordinary PASS entries; no disabled-feature or non-template controls are present.

**Oct 19 2026 — 10.18 TL2+ PUT replay (local, staged, not deployed).** The approved TL2+ PUT JSON adjustment uses `theta >= -4.200560001`, `atr <= 0.9610714295714233`, `vega <= 0.085590001`, `gamma >= 0.036829999`, `otm_pct <= 0.010500001`, `rsi <= 66.92679301474986`, and `put_chain_vol_oi >= 0.1643506422797303`. It adds **6W/5L** to the approved 340-row source-backed continuation, for **351 trades, 248W/103L**, wins `+$128,100.32`, losses `-$8,425.76`, net **+$119,674.56** before the subsequent TL0 CALL continuation. The replay artifact is `docs/0.ProfitabilityThresholds/10.19/APPROVED_REPLAY_SUMMARY_8.19-10.19_TL2P.json`.

**Oct 20 2026 — 10.18 TL0 CALL continuation (local, staged, not deployed).** The approved existing-template adjustment uses `put_chain_volume <= 14454.000001`, `put_chain_open_interest <= 21570.000001`, and `break_distance_pct >= 0.00090538120145535`, with the existing lower/upper bounds retained. It adds **3W/1L**, wins `+$1,582.60`, losses `-$88.60`, and lifts the 351-row baseline by **+$1,494.00**. The verified source-backed result is **355 trades, 251W/104L**, wins `+$129,682.92`, losses `-$8,514.36`, net **+$121,168.56**. The verifier is `docs/0.ProfitabilityThresholds/10.19/verify_tl0c_source_backed.py`.

10.18 also fills the same-side share minimum on every path: CALL `call_share >= 0.563038609119799` / `0.319904959535561` / `0.111791722834833` / `0.353513730555839` / `0.475842835078687` for ORB1 / TL0 / TL1 / TL2+ / Breakout, respectively; PUT `put_share >= 0.110879070236391` / `0.178323146274259` / `0.193358617395805` / `0.1509958844` / `0.20983660560238` in the same order. Existing stronger floors were retained. The approved TL2+ PUT continuation is **351 / 248W/103L / +$119,674.56**.

10.18 also uses explicit inclusive theta ranges on every path/side: ORB1 CALL `-4.315345..-0.07606`, ORB1 PUT `-2.284425..-0.12555`, TL0 CALL `-3.021641..-0.06014`, TL0 PUT `-3.498951..-0.341429`, TL1 CALL `-5.138515..-0.17573`, TL1 PUT `-2.580435..-0.063849`, TL2+ CALL `-4.523229..-0.084335`, TL2+ PUT `-1.170381..-0.08446`, Breakout CALL `-1.623715..-0.12525`, and Breakout PUT `-2.01866..-0.07463`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.

10.18 also uses explicit inclusive `chain_sanity_best_same_side_spread_pct` ranges on every path/side: ORB1 CALL `0.344836811924219..6.383701188455`, ORB1 PUT `0.596426764651064..7.773851591106`, TL0 CALL `1.61945974298453..20.1669298443492`, TL0 PUT `0.389194744737369..13.5137877158452`, TL1 CALL `2.52708404607139..23.8942731277533`, TL1 PUT `1.6260152601626032..66.6666666676667`, TL2+ CALL `0.392128606047284..11.7647068823529`, TL2+ PUT `1.35190682804293..40.000000001`, Breakout CALL `0.579709..28.2251082251082`, and Breakout PUT `0.4962779156327658..28.8018433179723`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.

10.18 also uses explicit inclusive ATR ranges on every path/side: ORB1 CALL `0.0838071204049245..11.7848166666667`, ORB1 PUT `0.187607312883651..2.93792573939732`, TL0 CALL `0.0556927764892573..3.27252737862723`, TL0 PUT `0.0977714449201318..3.59140289306641`, TL1 CALL `0.0382357142857144..1.3017867143`, TL1 PUT `0.011478571428571..0.706512074497764`, TL2+ CALL `0.0697061428571486..0.916285714285725`, TL2+ PUT `0.010310714285714..0.363773029436383`, Breakout CALL `0.02752142857..0.379835714285714`, and Breakout PUT `0.0266571428571425..0.767689285714289`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.

10.18 also uses explicit inclusive vega ranges on every path/side: ORB1 CALL `0.00505..0.132065`, ORB1 PUT `0.01543..0.082791`, TL0 CALL `0.003455..0.098525`, TL0 PUT `0.00967..0.08435`, TL1 CALL `0.004035..0.094591`, TL1 PUT `0.00161..0.076311`, TL2+ CALL `0.010825..0.08185`, TL2+ PUT `0.003665..0.07165`, Breakout CALL `0.00351..0.095511`, and Breakout PUT `0.00146..0.078755`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.

10.18 also uses explicit inclusive `price_volume_efficiency_3m` ranges on every path/side: ORB1 CALL `0.00456563397427..3.5028161367`, ORB1 PUT `0.1426011856..2.4864233357220407`, TL0 CALL `0.0129134837390364..3.66317100167422`, TL0 PUT `0.00649096351352805..1.69833944939667`, TL1 CALL `0.00371369666..2.4827719819959557`, TL1 PUT `0.271922743011..6.91827934618774`, TL2+ CALL `0.029059924520863..3.07766593010303`, TL2+ PUT `0.0462350856..0.850353880054005`, Breakout CALL `0.0546185004126276..1.0436761436206856`, and Breakout PUT `0.019405..0.9132435881`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.

10.18 also uses explicit inclusive `break_distance_pct` maxima on every path/side: ORB1 CALL `<= 0.0040720584752035`, ORB1 PUT `<= 0.0136065850825449`, TL0 CALL `<= 0.0152067996061208`, TL0 PUT `<= 0.007582190134`, TL1 CALL `<= 0.0176903601599601`, TL1 PUT `<= 0.0557678320857052`, TL2+ CALL `<= 0.0075689021770907`, TL2+ PUT `<= 0.014908792883353499`, Breakout CALL `<= 0.0240290015178664`, and Breakout PUT `<= 0.0079482992403997`. These maxima are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.

10.18 also uses explicit inclusive signed `otm_pct` ranges on every path/side: negative values are in-the-money, zero is at-the-money, and positive values are out-of-the-money. ORB1 CALL `-0.00115167442306..0.002664423`, ORB1 PUT `-0.0050794346971792..0.00556570198987305`, TL0 CALL `-0.0032713120554548..0.00457633446646655`, TL0 PUT `-0.00495786394677635..0.023501`, TL1 CALL `-0.00036447809662825..0.019700001`, TL1 PUT `-0.0082259933669408..0.014001`, TL2+ CALL `-0.003330197906206897..0.0030761208083175`, TL2+ PUT `-0.0061932448286027494..0.004095279074914292`, Breakout CALL `-0.0011608794..0.0190041401273885`, and Breakout PUT `-0.0032626427406199..0.00520109`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.
10.18 also uses explicit inclusive IV ranges on every path/side: ORB1 CALL `0.21783..1.226290001`, ORB1 PUT `0.33487..1.012991`, TL0 CALL `0.2561..0.971855`, TL0 PUT `0.183825..1.432220001`, TL1 CALL `0.19791..0.875565`, TL1 PUT `0.197815..1.361045`, TL2+ CALL `0.136589999..1.03468`, TL2+ PUT `0.11243..0.68493`, Breakout CALL `0.096979999..1.21884`, and Breakout PUT `0.098089999..0.697125`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.
10.18 also uses gamma minimums on each path/side where the scan found a beneficial zero-TP-harm floor: ORB1 CALL retains `>= 0.02031`; ORB1 PUT `>= 0.025745`; TL0 CALL remains unchanged because no beneficial floor was found; TL0 PUT `>= 0.043325`; TL1 CALL `>= 0.029625`; TL1 PUT `>= 0.03277`; TL2+ CALL `>= 0.039455`; TL2+ PUT `>= 0.12875`; Breakout CALL retains `>= 0.063009`; Breakout PUT `>= 0.076335`. These floors are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.
10.18 also uses explicit inclusive `distance_from_vwap_atr` ranges: ORB1 CALL `0.6879907168719176..2.246283543`, ORB1 PUT `-4.070635056592032..-0.6473584804869945`, TL0 CALL `-1.2098901687214545..1.702007653198124`, TL0 PUT `-3.0011112280735657..0.7700880176279401`, TL1 CALL `-7.94640718893108..16.873864924821127`, TL1 PUT `-11.852937333487391..6.2689135482085545`, TL2+ CALL `-23.602233072907463..1.6626402404021854`, TL2+ PUT `-0.9863333250594576..21.770585746755078`, Breakout CALL `6.183667971439902..17.875382727396637`, and Breakout PUT `-27.386341540812175..-2.6461714877462477`. These ranges are part of the approved source-backed replay at **355 trades, 251W/104L / +$121,168.56**.

The 10.18 profile also fills the same-side volume/OI ratio minima on every CALL and PUT path. CALL `call_chain_vol_oi` minima are ORB1 `0.0434753028309428`, TL0 `0.0969389738459309`, TL1 `0.115369801138699`, TL2+ `0.546804195936347`, and Breakout `0.199600993183583`. PUT `put_chain_vol_oi` minima are ORB1 `0.0524733081428465`, TL0 `0.0417151849721774`, TL1 `0.11329222684698`, TL2+ `0.163387361567477`, and Breakout `0.125729834873833`. Existing opposite-side rules and the TL1 PUT maximum remain unchanged. The approved source-backed replay remains **355 / 251W / 104L / +$121,168.56**.

**Oct 18 2026 — Profit Capture next-deploy set (BUILD; not deployed).** `Shared.env` §2b enables two narrow, replay-selected PATH_V2 exits plus the conservative stale-high ceiling refinement. `underlying_mfe_capture` (`UL_MFE_CAPTURE_175_250`) requires 175–250% premium MFE, a 25pp giveback, bid at least +50%, a high at least 120s old, and a fresh adverse underlying retrace/drawdown. `bounded_mfe_capture` (`BOUNDED_MFE_CAPTURE_290_305`) runs only from 10:45–12:30 PT with 290–305% MFE, a 10pp giveback, and bid at least +100%; it is passive and cannot supersede the mandatory afternoon `mfe_trail`. The bounded gate adds **+$1,697** to both 8/19–10/16 books with no harmed trade. `OPTION_PATH_V2_STALE_FAIL_HIGH_MAX_PRET=0.34` adds **+$96** Total Results and **+$20** on the 261-trade Trade Profile book with no harmed winners or outcome flips; do not widen it beyond `0.34`. The MFE-capture rollbacks are their feature kill switches; stale-high rollback is `0.325`. Full priority, routing, telemetry, and all knobs: [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Oct 15 2026 t202u/t203u — dir5m handoff keep-alive (discovery → must execute), `open_short` window, 20p pre-cross prefetch, API cap 180 (BUILD; next Demo deploy, not deployed). t204u: dir% is causal per stage clock (no post-event chain; decision chain ≤ freeze, ≤ 60 s); see `docs/0.POTemporalValidationFillSuccess.md` §9.1.** Details, budget and the 10/15 projection: `docs/Data.md` §5.3 "`dir_pct_change_5m` / 5m chain keys — architecture" (t202u bullet). New deploy env (the script's default `DEPLOY_ENV_UPDATES`, 16-key delta against rev 00601 after t204u): `DIR5M_KEEPALIVE_ENABLED=true`, `DIR5M_KEEPALIVE_SEC=210`, `DIR5M_KEEPALIVE_WIDE_SEC=360`, `DIR5M_KEEPALIVE_MAX_PER_MIN=24`, `DIR5M_OPEN_SHORT_WINDOW_PT=06:30-06:36|06:45-06:51|07:30-07:36`, `DIR5M_20P_PREFETCH_ENABLED=true`, `DIR5M_20P_NOW_MAX_AGE_SEC=90`, `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC=210`, `DIR5M_OPEN_POSITION_RESAMPLE_SEC=180`, `DIR5M_OPEN_POSITION_MAX_PER_MIN=12`, `OPTION_API_HARD_CAP_PER_MIN=180`, `CHAIN_FETCH_HARD_CAP_PER_MIN=180`, `CHAIN_FETCH_ADAPTIVE_CEILING_PER_MIN=60`, `CHAIN_FETCH_LOW_PRIORITY_SAFE_PER_MIN=150`, `CHAIN_FETCH_HIGH_PRIORITY_SAFE_PER_MIN=170`, `CHAIN_RESAMPLE_TICK_SEC=5`.

**Oct 15 2026 t194u — dir5m held-position sampling, aging-first ranking, PO telemetry (BUILD; next Demo deploy, not deployed).** Details: `docs/Data.md` §5.3 "`dir_pct_change_5m` / 5m chain keys — architecture" (t194u bullet). Report: `docs/0.ProfitabilityThresholds/10.15/grokbot_books/dir5m_stamp_review_10.15.md`.
- **New deploy env (the script's default `DEPLOY_ENV_UPDATES`):** `DIR5M_SAMPLE_OPEN_POSITIONS=true` (code `false`), `DIR5M_OPEN_POSITION_RESAMPLE_SEC=300`, `DIR5M_OPEN_POSITION_MAX_PER_MIN=6`, `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC=180` (code `60`). `DIR5M_APPROACH_AGING_FIRST` defaults to `true` in code. The boost stays off. Rollback: `DIR5M_SAMPLE_OPEN_POSITIONS=false`, `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC=60`, `DIR5M_APPROACH_AGING_FIRST=false`.
- **Load (worst case, coincident peaks):** 10/14 baseline avg 39 / peak 102 + approach sampler 24 calls/min + held sampling (10/15: 7.5 open symbols avg, 22 max → ≈3 avg / ≤12 calls/min) ≈ **66 avg / ~138 peak**. Against E*TRADE's 240/min that is avg 28 %, peak 58 % (50–80 %). Against the app's 140/min hard cap it is avg 47 %, peak ~99 % (near full; P2/P3 yield first under the hard cap). A boost to 18/min would add +12 calls/min, so it stays off.

**Oct 14 2026 t172u/t189u — `dir_pct_change_5m` 5m chain keys: deploy env and deploy script (rev `00600-fpn`).** Architecture: `docs/Data.md` §5.3 "`dir_pct_change_5m` / 5m chain keys — architecture".
- **Deploy env (only these two):** `DIR5M_APPROACH_SAMPLER_ENABLED=true` (code `false`), `DIR5M_APPROACH_MAX_PER_MIN=12` (code `6`). Every other `DIR5M_*` stays at its code default (table in Key env). Rollback: `DIR5M_APPROACH_SAMPLER_ENABLED=false`.
- **Load:** 10/14 wide avg 39/min, p90 50, peak 102; with the sampler ~63 avg / ~126 peak vs E*TRADE 240/min (≈ 53 % peak) and the app 140/min hard cap.
- **`scripts/deploy_demo_service.sh` (Demo `easy-etrade-strategy` only; refuses `*-live`).** Merge, never replace: snapshot the live env → baseline keys added only when missing → `DEPLOY_ENV_UPDATES` win (default the two keys above) → `gcloud run deploy --update-env-vars` with the delta only → post-deploy env diff check. Snapshots in `~/grokbot_transfer/deploy_snapshots`.
- **Protections:** protected keys must keep their values (`MODE=demo`, `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`, all position caps `=50`, `0DTE_MONITOR_MAX_SYMBOLS=50`, `0DTE_DEMO_STARTING_BALANCE=100000`, `0DTE_MAX_POSITION_SIZE_SUM=600`; override only with `ALLOW_PROTECTED_CHANGE=YES`). No ledger seed: aborts if `DEMO_SIM_LEDGER_SEED_ON_STARTUP` is on the service or in the update; `SEED_DEMO_LEDGERS` is refused. Runs only with `DEPLOY_NOW=YES`; `DRY_RUN=1` is read-only (prints the merged env and delta, no build/deploy).

**Nov 5 2026 — UL pass-2 + OCC apply-on-complete (in `00625-wwv`).** Leave the owned UL fetch running past 4s/30s (`ul_refresh_slow` / `ul_refresh_hung`, cancel=false). In-tick join only when the UL loop is down. OCC does not `wait_for`-cancel a finished executor batch. `0DTE_OPEN_EXACT_QUOTE_BATCH_SIZE=25` (was 12 on `00624-8bn`). PATH_V2 `pret` is this tick's exact bid / entry − 1. Evaluate-path 20% snapshot does not wait 3s for hub. CALL-only Save (`SIDES=call`) is unchanged. Rollback `00624-8bn`. — [PositionMonitoring.md](PositionMonitoring.md) · [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Nov 5 2026 — sequential OCC + recording (in `00625-wwv`).** Inner OCC batches of 25 run one HTTP at a time; after each wave except the last, `yield_lane_to_equity_batch` (`open_exact_occ` pri 0 > `equity_batch` pri 6). A new OCC fetch is deferred only while last exact is reusable (`OCC_DEFER_FOR_UL_MAX_S=12`). New eval JSONL keys: `path_v2_peak` / `path_pret` / `path_v2_reason`, `quote_age_ms`, `contracts` / `quantity`, modeled $0.65×2 fees. 16-tick 5s load: N=38/50 second-half miss ~42% / ~56% — TTL 4s/8s not raised. RESEARCH `exit_premium` is not booked fill. Rollback `00624-8bn`. — [PositionMonitoring.md](PositionMonitoring.md) · [Data.md](Data.md).

**Nov 4 2026 — monitor cadence (in `00624-8bn`).** Dedicated `_monitor_loop` always pauses after `maybe_tick` (min 0.2s). Empty book clears `_0dte_force_monitor_tick`. Watchdog restarts that loop; it does not evaluate beside a live tick. Tick ids are `canonical_monitor_tick_id` → `u0dte_<ms>` only (Trendline/ORB fast loops mint the same way). UL refresh 2s (that image still 4s-cancels). JSONL **5s** / daily JSON **45s**. `_LINE_COUNTS` locked and cleared after GCS download. Restart rebuild keeps the real entry time and a live HWM. Replay of saved rows still 0 differences. — [PositionMonitoring.md](PositionMonitoring.md) · [Data.md](Data.md).

**Nov 4 2026 — Profit Capture live/replay eval-clock alignment (in `00624-8bn`).** ULR ignores UL `<= 0`/`None` and runs only when both Save switches are true. Every PATH_V2 eval writes one JSONL row (`eval_now`, `eval_age_s`, `monitor_tick_id`, `prem_vel_10`). One evaluate per `(trade_id, monitor_tick_id)`: HOLD stores `out=None` so Trendline/ORB reuse (`PATH_V2_EVAL_DUP_BLOCKED`). Replay uses `eval_now` (historical fallback: `timestamp`) and sorts by parsed epoch. Sparse-velocity is removed (no `OPTION_PATH_V2_VEL_SPARSE_ANCHOR_ENABLED` key). Closed `exit_bid` is the frozen `entry_option_symbol` path bid. Replay PT clock is `America/Los_Angeles`. No Trade Profile JSON or exit-threshold change. — [0DTEProfitCapture.md](0DTEProfitCapture.md) · [PositionMonitoring.md](PositionMonitoring.md).

**Nov 3 2026 — Profit Capture STEP 2 20% Save stall save + UL-retrace extension (ON in `00624-8bn`, CALL-only).** STEP 2 is the +20% stall save (`twenty_percent_save` / `P20_STALL_SAVE`) plus the UL-retrace extension (`P20_UL_RETRACE_SAVE`), not the retired PVE3 latch (`PVE_20P_SAVE`). Shared.env `OPTION_PATH_V2_20P_SAVE_ENABLED=true` and `OPTION_PATH_V2_20P_SAVE_ULR_ENABLED=true`. Cloud Run `OPTION_PATH_V2_20P_SAVE_ENABLED=true` on `00624-8bn`. `SIDES=call` (file-only; puts like MSTR/NFLX/SLV/HD never save). Do not set a Cloud Run ULR override. Kill switch: stall `ENABLED=false` (whole STEP 2); ULR only `ULR_ENABLED=false`. Full STEP list: [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Serving now (Nov 5 2026):** RESEARCH Demo `easy-etrade-strategy-00625-wwv` · `MODE=demo` · `SINGLE_TP_ADMIT=true` · `TRADE_PROFILE_VERSION=wide` · sim ledger **$100,000** (seed flag off) · `OPTION_PATH_V2_20P_SAVE_ENABLED=true` (CALL-only from Shared.env `SIDES=call`) · JSONL **5s** / daily JSON **45s** · `0DTE_OPEN_EXACT_QUOTE_BATCH_SIZE=25`. Image is the Nov 5 UL/OCC pack (no 4s UL cancel, sequential OCC + `yield_lane_to_equity_batch`, reusable-gated OCC defer, eval JSONL engine/qty/fee keys) on the Nov 4 cadence (`_monitor_loop`, `u0dte_<ms>`). Serving still assigned `path_pret = current_pnl_pct`. The admit function is that path only. A slow exit quote does not skip the break check. The scan quote is the decision quote for **2 minutes** (`TP_FREEZE_QUOTE_MAX_AGE_SEC` default **120**). Rollback of this image: `00624-8bn`. FINAL is retired. Calendar: [MarketCalendar.md](MarketCalendar.md).

**Nov 6 2026 — local runtime pack (not on serving).** `live_path_v2_pret_from_tick` (this-tick bid only; missing bid → HOLD); leftover OCC skip while owned (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`); discovery scan slot must not wait on a running watch (`unified_discovery_scan_slot_free`); Telegram flush is notification-only (`ledger_alert_flush.py`); desk Bid MFE% vs Mark MFE%. Local 10.23 successor is S0_maxnet (`trade_profile_10.23.json` sha `a449ab0d…`; not the live admit). 5s + PATH_V2 at 38/50 is a safe-degradation test — TTL 4s/8s not raised. Sequential OCC is architectural. Deploy only on explicit `deploy now`.

**Oct 30 in `00619-xr6` (was local on `00618-cvb`):** A fill on this tick's monitor roster is watched. `ENROLLMENT_FAILED` is only a fill that never joins that roster. The first-seen stamp runs when the monitor takes the name, before an eval skip. Priority Optimizer `commit_bytes` holds one lock per object name. The first body is the object. A GCS 412 on that create-if-absent means the object is already there. A different body is a conflict and is not overwritten. Persist is time-correct without extra Market API: session tech uses bars whose minute close is ≤ the event; a future session-tech clock nulls RSI/MACD/BB; executed copies the handoff freeze; `first_red` ages the live OCC quote on that tick; mid-trade greeks restamp from that OCC quote; stale or unread chain volumes are nulled; owned 5m keys use OCC samples; flatten primes remaining opens once; `breakout_watch_activated` is not a 267-key training fill. Guide: [PriorityOptimizer.md](PriorityOptimizer.md). An accept orders the Trade Profile strike. Demo fills the sim ledger at that ask. Live limits at that ask, then one lift of $0.05–$0.15. Guide: [0.OrderExecution.md](0.OrderExecution.md). `00618-cvb` still re-selects after the accept.

**Oct 13 2026, 3:08 PM PT — DEPLOYED `easy-etrade-strategy-00599-lhd` (RESEARCH Demo, 100% traffic, MODE=demo).** Image `gcr.io/easy-etrade-strategy/easy-etrade-strategy@sha256:ec62b222e35fb7cd6272e61111e0df4644e486c2279fdd1d83ae263c3b7a9e42`, commit `f8c5013`. Env added (`--update-env-vars` only): `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide` (no shadow; 10.5 loaded but unused), caps pinned to **50**: `0DTE_SLOT_COUNT`, `TOTAL_/ORB_/BREAKOUT_/TRENDLINE_0DTE_MAX_POSITIONS`, `MAX_TOTAL_OPTION_POSITIONS`, `0DTE_MONITOR_MAX_SYMBOLS`. Spread 8/30/30 + $0.01, $600 sum cap unchanged. Rollback: `gcloud run services update-traffic easy-etrade-strategy --region us-central1 --to-revisions easy-etrade-strategy-00598-tr4=100` (or `SINGLE_TP_ADMIT=false`). FINAL Live Pilot untouched (cap 7). The "local, NOT deployed" note below is now deployed.

**Oct 13 2026 — Single Trade Profile admit path (deployed `00599-lhd`; still the admit on `00625-wwv`).** RESEARCH only. Local source (Oct 29) has no flag-off branch. On the serving image the env is `true`, so every signal runs:

1. **Discovery Scan** (unchanged; 10 s, 24-symbol batches).
2. **Must Execute** — feasibility only, never quality. In order: signal geometry (`orb_break`/`orb_range`/`regime`, `tl_break`, `bo_confirm`; ORB `PS`/`top_n` rank only) → cooldown / day-exclude → book-full precheck (`max_open_positions_reached`, no API, no cooldown) → **one** chain fetch via `chain_fetch_manager` → data readiness (`data_wait:<key>` holds the break; after `DIR_PCT_DEFER_MAX_WAIT_SEC` (90 s) it retires as `dir_pct_data_unavailable` / `data_unavailable:<key>`, no cooldown) → **Call / Put Skip Signals** (`GATE_call_ss` / `GATE_put_ss`, same evaluators and `QG_*_SS_*` levels, kept OUTSIDE the Trade Profile; CALL SS may early-out pre-chain on TL/BO and is not re-run; PUT SS runs at chain time) → `skip_signal:<call|put>` → **one shared contract pick** (premium 0.03–2.50, \|delta\| 0.18–0.42 (+tolerance), OTM ≤ 0.055 (Breakout 0.035), contract vol ≥ 50 / OI ≥ 100 when present, one spread rule: spread % of mid ≤ `0DTE_MAX_SPREAD_PCT_ORB1` 8 / `_BREAKOUT` 30 / `_TRENDLINE` 30 (Breakout has its own key) or ask−bid ≤ `0DTE_SPREAD_CHEAP_MAX_USD` ($0.01), re-checked at submit) → `contract_unavailable:<delta_out_of_band|otm|premium_low|premium_high|volume|oi|spread|no_quote>`.
3. **Single stamp** (`central_tech/decision_stamp.py`): one immutable dict = the TP decision keys read once from the hydrated row + `call_share`/`put_share` (computed once) + `equity_volume_ratio` (Wide extension) + the **picked** contract's `delta`/`otm_pct`. `decision_digest = sha256(canonical keys)`. The same dict feeds the TP, the decision record, the handoff snapshot freeze (`po_decision_freeze.decision_digest`) and the TP_DECISION log line; the TP returns `evaluated_digest` and a mismatch rejects `data_unavailable:stamp_parity`.
4. **Trade Profile** — exactly one evaluation, `TRADE_PROFILE_VERSION=wide|10.5|10.16|10.17|10.18|10.20|10.21|10.22|10.23` (`easy0DTE/configs/trade_profile_wide.json`, `trade_profile_10.5.json`, `trade_profile_10.16.json`, `trade_profile_10.17.json`, `trade_profile_10.18.json`, `trade_profile_10.20.json`, `trade_profile_10.21.json`, `trade_profile_10.22.json`, or `trade_profile_10.23.json`; code default `10.5`, deployed Research Demo pins `wide`); optional `TRADE_PROFILE_SHADOW_VERSION` evaluates the other version on the same stamp (record only, never blocks).
5. **Accept / reject** — `tp_admit:<cell>` or `tp_reject:<key>:<min|max|missing|not_and>`.
6. **Sizing / caps** (unchanged) — $600 per position (floor(600/(premium×100)), min 1; a $0.05 contract = 120), max open 50 everywhere incl. TL slots, TL slot persistence/recovery, `capacity:*`.
7. **Order** — the accept's strike. Demo sim ledger at the ask, or Live E*TRADE. [0.OrderExecution.md](0.OrderExecution.md). In `00619-xr6`.

**Not part of this admit:** the GATE_* quality stack (dir%, Vol_Ratio, break_max, pc_oi, path_ss, entry_part, red_day, observe stamps), Layer D contract gates, the post-TP contract-band check, cheap-lotto. Those lists are gone from local source. dir%, Vol_Ratio and break_max live in TP Wide lines. PO snapshot writers (entry, handoff, 20p, exit), discovery, the position monitor and the flight recorder are unchanged.

**Live missing-value policy = the loaded profile JSON (Eric 10/13; no code-side table).** `single_tp_admit` applies no pre-policy. **10.5, 10.16, 10.17, and 10.18:** missing = pass except each cell's configured `missing_reject_keys`; `dir_pct_change_5m` is phased out as a gate and remains PASS. 10.18's approved source-backed continuation replay is **355 / 251W/104L / +$121,168.56**. The full joined CSV is a different population and is not used to replace that approved replay. **Wide:** `trade_profile_wide.json` `semantics.missing_default` = pass, per-cell `missing_reject_keys` = [equity_volume_ratio] (legacy Vol_Ratio), `missing_defer_keys` = the share key → `data_wait` (legacy dir% defer); break_distance_pct missing passes (legacy break_max skip). The 10.5 replay baseline remains separate.

**TP Wide** (`trade_profile_wide.json`, built by `build_tp_wide.py`): per cell `call_share`/`put_share` 0.10 (strict, same side); `break_distance_pct` ≤ 0.025 (ORB1, Breakout); **`equity_volume_ratio` ≥ 0.012 inclusive on all ten cells** (ORB1, TL0/TL1/TL2+, Breakout; Nov 2 2026 — was 0.12 on ORB1/Breakout). Missing still rejects. Env parity: `0DTE_HARD_GATE_*_VOL_FLOOR=0.012`, `BREAKOUT_MIN_VOLUME_RATIO=0.012`, `GATE_TL_Vol_Ratio_MIN=0.012`. 10.23 has no `equity_volume_ratio` line. No spread line (picker only), no Skip-Signal lines (outside TP). Picker `bid≤0` or `ask≤0` is `no_quote` (no two-sided book), not a $0 premium; cheap lottery is still ask **$0.03–$2.50**. An empty two-sided cache is `force_live` (`no_two_sided_quotes`); a stale `no_quote` pick holds `data_wait:chain_quotes` (no 1h cooldown). Local until `deploy now`.

**Rollback of today's image:** traffic to `easy-etrade-strategy-00614-bbx`. `SINGLE_TP_ADMIT=false` still selects the old stack only on images that contain it (`00617` does; local source does not). Replays: 10.5 book through the legacy gate stays **215 / 167W/48L / +$93,331.32**; the single-TP engine with the legacy missing rule also reproduces 215 / +$93,331.32 exactly (0 diffs); with the new live missing table it is 90 / 81W/9L / +$45,236.50 (historical rows lack `dir_pct_change_5m`, `spy_change_pct`, L1, greeks). Wide parity on 10/13 decision records: 257/257 (100 %).

**Oct 11 2026 — Profit Capture GB30 gated 38pp extension (local next-deploy candidate, not deployed):** RESEARCH only. `modules/path_v2_exit.py` STEP 21 (now STEP 24) keeps the base `gb30_giveback` sale at 24pp, but when the exit snapshot has `ul_dd <= 0.0009` and `0.05 <= pret <= 0.10`, the extension latches and the 24pp touch is held until 38pp giveback or a later Profit Capture exit. New `Shared.env` §2b knobs: `OPTION_PATH_V2_GB30_EXTENSION_ENABLED=true`, `_GIVEBACK=0.38`, `_MAX_UL_DD=0.0009`, `_MIN_PRET=0.05`, `_MAX_PRET=0.10`. 8/19–10/9 replay versus the current PC V2 books: Total **+$4,433**, Trade Profile **+$3,045**, winner gain **+$4,433**, **$0 winner harm**, **0 winner→loss**, **0 worsened losses**. Rollback: set `OPTION_PATH_V2_GB30_EXTENSION_ENABLED=false`. Research artifacts: `docs/0.ProfitabilityThresholds/10.9/pc_gb30_38pp_compound_gate_scores_8.19-10.9.csv` and `pc_gb30_38pp_compound_gate_trades_8.19-10.9.csv`. — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Oct 9 2026 — Research Demo admit is now the wide collection gates, not 10.5 (Eric, 10/9; deployed `easy-etrade-strategy-00596-sqc`, ~17:10 PT).** `QUALITY_GATE_ADMIT=wide_collect` (`configs/Shared.env`) runs the RESEARCH wide collect `GATE_*` stack (`quality_gate_stack.WIDE_COLLECT_STACKS`, copied verbatim from the last pre-Trade-Profile code `quality_gate_stack.py.bak_pruned88_20260921_193549`; levels from the Sep 17 wide-collect env): Layer C dir% strictly **>0.10** hard on ORB1 / TL / ORB2 (`0DTE_DIR_VOLUME_MIN_PCT`, `TRENDLINE_DIR_VOLUME_PCT`, `BREAKOUT_DIR_VOLUME_MIN_PCT` = 0.10) · `pc_oi` off · volume-ratio floor hard (ORB1/ORB2 **0.12**, TL **0.012**) · `break_max` **2.50%** hard (ORB1/ORB2) · ORB1/TL/ORB2 geometry + Layer D contract gates (δ 0.18–0.42, OTM ≤5.5% / BO ≤3.5%, liq, spread, BO cheap-lotto) hard · VWAP / RSI / BB / MACD / stamps observe · CALL / PUT Skip Signals **hard** (Eric 10/9 17:16 PT; `GATE_call_ss` / `GATE_put_ss` and the per-path `GATE_ORB/TL/BO_call_ss|put_ss` = hard, current levels `QG_CALL_SS_BREAK_DIST_MAX=0.00086` and `QG_PUT_SS_CALL_CHAIN_MIN=18800` + `QG_PUT_SS_BREAK_ORB_RATIO_MAX=0.287`; deployed `easy-etrade-strategy-00597-gdh`) · Path SS (`path_ss`) and `entry_part` stay **off** (Sep 4 wide collect had Path SS off). The 10.5 Trade Profile (`trade_profile_10.5.json`, sha `ddc81830…`) still loads and stamps its decision record and handoff keys, but runs **observe-only** (a TP reject or freeze deferral does not block). Rollback to the 10.5 admit: remove `QUALITY_GATE_ADMIT` (or set `trade_profile`) and restore the TP-era `GATE_*` lines from `~/Downloads/session_monitor/backup_wide_reseed_1009/repo/configs/`, or route traffic to `easy-etrade-strategy-00594-zn5`.

**Oct 9 2026 — Demo $100,000 reseed + $600 sum cap (Eric, 10/9; `easy-etrade-strategy-00595-qqk` seed write, then `easy-etrade-strategy-00596-sqc` with the seed flag removed).** One-time `DEMO_SIM_LEDGER_SEED_ON_STARTUP=true` wiped `research_app/mock_options_history.json` (0 closed, 0 open, one `seed` equity point) and reset `desk_public` ledger/actions; the flag was then removed. `0DTE_DEMO_STARTING_BALANCE=100000` and `0DTE_MAX_POSITION_SIZE_SUM=600` in `Account0DTE.env` **and** Cloud Run env (`--update-env-vars`; Cloud Run env wins). Pre-wipe ledger backup: `gs://easy-etrade-strategy-data/backups/research_app_pre_reseed_20261009/` (balance $2,973.10 on $5,000 seed, 32 closed).

**Oct 7 (Trade Profile decision freeze, time-correct data; Demo deploy approved by Eric 10/7 17:25 PT):** RESEARCH only.
- **What it does.** The Trade Profile gate and the handoff snapshot now read one decision-time freeze object (`central_tech/decision_freeze.py`). It applies per-source age caps: chain 60 s, quote 20 s, SPY 45 s, session tech 90 s, and a strict 5m prior of 300 s ± 90 s. When a gated source is too old there is one targeted per-symbol refresh, deduped and budgeted (chain 300/session and 8/min, quote 300/session and 10/min). Stale policy is **defer**. The SPY fallback cap dropped from 120 s to 45 s.
- **Config.** Every new key has a code default, so no env change is needed. They are read with `os.environ.get`, so a missing key does not log `CONFIG_MISSING_KEY`.
- **Rollback (env only).** `TP_FREEZE_ENABLED=false`, `TP_FREEZE_5M_STRICT=false`.
- **More.** Keys: [Trade Profile decision freeze](#trade-profile-decision-freeze-oct-7-2026). Data flow: [Data.md §5.3](Data.md#53-time-correct-data-for-trade-profile-decisions).

**Oct 6 (Runner Extension R2X — new PATH_V2 STEP 1; local only, not deployed or committed; Eric deploys):** RESEARCH only. `Shared.env` §2b: `OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED=true`, `_TARGET_MULT=1.25`, `_UL_RETRACE=0.04`, `_UL_MIN_MULT=1.045`, `_FLOOR_PRET=0.50`, `_MIN_TICKS=2`, `_JUMP_GUARD=0.5` (engine `modules/path_v2_exit.py`; dataclass default off, so a missing key keeps V3A + Stale-fail 4). Where `runner_timed_capture` would fire, a runner that passes the gap guard (≥ 2 ticks before the touch, previous bid return ≥ 0.5 × the touch L0) is held instead; then only R2X exits: bid < +50% → `runner_extension_floor` (`RUNNER_EXT_FLOOR`), bid ≥ 1.25 × L0 → `runner_extended_capture` (`RUNNER_EXT_TARGET`), bid ≥ 1.045 × L0 with UL retrace ≥ 0.04 → `runner_extended_capture` (`RUNNER_EXT_ULREV`). PATH_V2 steps renumbered (R2X = STEP 1, old 1–24 = 2–25). Both ids route passive like `runner_timed_capture`, with Telegram labels and stats counters. 8/19–10/5 replay vs Stale-fail 4: TP +$5,671 / Total +$5,861 (DEV +2,160 / +2,350, VAL +3,511 / +3,511), LODO min +$4,071 / +$4,261, 0 flips, 0 worsened losses, 0 winners harmed, 15 trades changed. New baseline: Trade Profile **$91,611.31**, Total **−$156,184.91**. Rollback: `OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED=false` (= Stale-fail 4 exactly). — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Oct 6 (Stale-fail 4 — `premium_stale_fail` thresholds; local only, not deployed or committed; Eric deploys):** RESEARCH only. `Shared.env` §2b: `OPTION_PATH_V2_STALE_FAIL_HIGH_MAX_PRET` 0.25 → **0.325**, `OPTION_PATH_V2_STALE_FAIL_RUNNER_MAX_PRET` 0.20 → **0.24**, `OPTION_PATH_V2_STALE_FAIL_UPPER_MIN_MFE` 0.80 → **0.75**, `OPTION_PATH_V2_STALE_FAIL_RUNNER_MIN_AGE_SECONDS` 60 → **42**. No code change (engine `modules/path_v2_exit.py` defaults unchanged; Shared.env carries the new values). 8/19–10/5 replay vs V3A: TP +$152 / Total +$978 (DEV +104 / +749, VAL +48 / +229), 0 flips, 0 worsened losses, 0 winners harmed, 15 trades changed, each knob passes ±5%. New baseline: Trade Profile **$85,940.31**, Total **−$162,045.91**. Rollback: restore the four old values. — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Oct 6 (PC V2 flat incl. V3A + Contract Fade off — local only, not deployed or committed; Eric deploys):** RESEARCH only. `Shared.env` §2b: V3A switches `OPTION_PATH_V2_SHALLOW_FLOOR_STOP_ENABLED=true` (`SHALLOW_FLOOR_*`, exit `shallow_floor_stop` / `SHALLOW_FLOOR_5_10_18`), `OPTION_PATH_V2_RUNNER_TIMING_V3A_ENABLED=true` (`RUNNER_TIMING_V3A_*`, details `RUNNER_TIMED_CAPTURE_350_A30` / `RUNNER_PROFIT_FLOOR_350_G900`), `OPTION_PATH_V2_STALL_FLOOR_STOP_ENABLED=true` (`STALL_FLOOR_*`, exit `stall_floor_stop` / `STALL_FLOOR_15_40_4`); dataclass defaults are off, so all three false = package B. Contract Fade is now **off** (`OPTION_PATH_V2_CONTRACT_FADE_EXIT_ENABLED=false`, pending calibration; readings still log). New key `OPTION_PATH_V2_CONTRACT_FADE_ENTRY_CHAIN_FALLBACK_ENABLED=true` (read in `prime_trading_system.py`: entry-chain breadth fallback). Engine is the flat `modules/path_v2_exit.py`. 8/19–10/5 replay: Trade Profile **$85,788.31**, Total **−$163,023.91** (vs package B +$1,601 / +$3,686). Exit catalog, deploy checklist and expected sha256s — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Oct 5 (local only, not deployed): PO fill + admit/executor fixes.** From the Oct 5 PO snapshot audit (`/workspace/session_monitor/oct5_po_fill_and_fixes.md`). (1) `option_l1_imbalance`: gate chain stamp (`convex_chain_skew.stamp_path_ss_chain_fields_from_cache` → `central_tech.option_l1.stamp_option_l1_from_chain`) and the PO `cache_row` handoff path now carry option bid/ask size + imbalance (selected strike → representative → ATM; never a different strike when the selected one is known). (2) R6: `confirm_chain_skew_before_execution` no longer stamps 5m chain deltas before its chain fetch (`stamp_admit_tech(chain_trajectory=False)`), clears stale `dir_pct_change_5m` / `*_chain_volume_delta_5m` from row, metadata and nested `qg_admit` after a successful fetch, restamps, and persists those keys with overwrite; `chain_ring` ignores a prior sample with identical call+put chain volumes (same observation → None, not a fake 0.0). (3) New Layer D `contract_band` admit check for TL/BO (`easy0DTE/modules/admit_contract_band.py`): reject when the cached chain has no OTM-side contract with |δ| in [`0DTE_ENTRY_DELTA_MIN`, `0DTE_ENTRY_DELTA_MAX` + `TRENDLINE_DELTA_TOLERANCE`] within the path research OTM max (`QG_ADMIT_CONTRACT_BAND_CHECK`, default `true`; grep `QG_CONTRACT_BAND_REJECT`). (4) PO no longer writes `*_options_executed` (entry_executed + seal) when `broker_success=False` (`PO_WRITE_FAILED_EXECUTED_SNAPSHOT=true` restores; grep `PO_SNAPSHOT_SKIP | reason=fill_failed`).

**Oct 8 (10.5 Trade Profile; deployed to Research Demo `easy-etrade-strategy-00594-zn5`, 10/8 ~20:01 PT):** RESEARCH only. The current default admit is the **10.5 flat Trade Profiles** ([0.TradeProfiles.md](0.TradeProfiles.md) section 2): `easy0DTE/configs/trade_profile_10.5.json`, gate `_evaluate_10_5` in `easy0DTE/modules/trade_profile.py`, and shadow selection in `easy0DTE/modules/quality_gate_stack.py`. `TRADE_PROFILE_VERSION`: unset or `10.5` = 10.5; `10.2nh` / `10.2` / `10.3` = 10.2/10.3 rollback; `10.1` = 10/1 Superior. `TRADE_PROFILE_105_JSON` overrides the 10.5 file path. Current (deployed) semantics: missing value passes by default; only ORB1/TL0 listed warm-up keys reject when missing (`ORB1 CALL`: `price_volume_efficiency_3m`, `distance_from_vwap_atr`, `rsi`; `ORB1 PUT`: `price_volume_efficiency_3m`, `rsi`; `TL0 CALL`: `rsi`; `TL0 PUT`: `dir_pct_change_5m`, `rsi`). `dir_pct_change_5m` / `spy_change_pct` exact `0.0` count as missing; PUT delta = `abs(delta)`. Startup logs `TP_PROFILE_LOADED | version=10.5 | active=10.5 | … | guards=97 | range_lines=95 | not_and=2 | bounds=162 | cells=10 | … | strict_bounds=5 | missing_reject=8 | missing_default=pass` (JSON sha256 `32792b3f270dec3038bc90a494b4475854ed4bf51b6b872375ea0baf2b942894`). Current 8/19–10/8 replay after removing weak/non-functional full-book TP-loss candidates: Total **4,257 trades, 946W/3,311L, −$152,927.11**; Trade Profile **196 trades, 151W/45L, +$90,293.82** under the current deployed code rule (missing passes except the warm-up reject keys). It omits two 10/7 trades that admit (SPCX Breakout PUT −$17.20, NVDA TL2+ CALL −$94.90): with them **198, 151W/47L, +$90,181.72**. Reference only: missing-reject on all gated keys gives **89, 77W/12L, +$47,845.10**; that rule is PLANNED (Eric, 10/8), not implemented, and needs a code change in `easy0DTE/modules/trade_profile.py` (lines 1637–1643, 1744–1746, 1864–1868). Deployed to Research Demo as `easy-etrade-strategy-00594-zn5` (10/8); previous revision `easy-etrade-strategy-00593-mkh`. Rollback: `gcloud run services update-traffic easy-etrade-strategy --to-revisions easy-etrade-strategy-00593-mkh=100 --region us-central1 --project easy-etrade-strategy` (pins traffic; a later deploy then needs `--to-latest`), or set `TRADE_PROFILE_VERSION=10.2nh` for the 10.2/10.3 profile on the new image.

**Oct 12 t155u (10.5 Trade Profile: TL1 CALL participation repair; local next-deploy candidate, not deployed):** RESEARCH only. Adds TL1 CALL `put_share > 0.239948820749` as a derived line from `put_chain_volume / (call_chain_volume + put_chain_volume)`, checked after `call_chain_open_interest` in the runtime 23-key template. JSON sha256 `2f241665b591010ebca6fb33a62017f13804ebc6559740f1a54e7e7ff25951be`; startup `guards=106 | range_lines=104 | not_and=2 | bounds=171 | … | template=CALL:23,PUT:23 | … | strict_bounds=6 | missing_reject=10`. Replay 8/19–10/9: Total **4,270 / 948W/3,322L / −$154,381.21**; Trade Profile **215 / 167W/48L / +$93,331.32**. Research Demo admit remains `wide_collect`; 10.5 is staged for a later Trade Profile admit switch.

**Oct 11 t154u (10.5 Trade Profile: 23-key loss repair; local next-deploy candidate, not deployed):** RESEARCH only. Adds TL1 PUT `option_l1_imbalance <= 0.82721479112145`, TL2+ CALL `gamma >= 0.033625`, TL2+ CALL `vega <= 0.09412`, and tightens Breakout PUT `spy_change_pct <= 0.653792978693174`. JSON sha256 `c97d12e7c3cd3fc6ae679a0cd01c766d08c8794366df2761bdcfeac872e0c3b2`; startup `guards=105 | range_lines=103 | not_and=2 | bounds=170 | … | template=CALL:23,PUT:23 | … | missing_reject=10`. Replay 8/19–10/9: Total **4,270 / 948W/3,322L / −$154,381.21**; Trade Profile **216 / 167W/49L / +$93,189.82**. Research Demo admit remains `wide_collect`; 10.5 is staged for a later Trade Profile admit switch.

**Oct 9 t152u follow-up (10.5 Trade Profile: 23-key TL1 PUT `otm_pct` repair; local next-deploy candidate, not deployed):** RESEARCH only. The earlier 26-key Option A experiment was reverted; the Trade Profile 10.5 template remains 23 keys per side. `trade_profile_10.5.json` keeps only the valid 23-key TL1 PUT `otm_pct >= -0.00135873` line from t152u. The non-template keys `price_volume_efficiency_1m`, `volume_pct_of_day_5m`, and `macd_histogram` are not in the Trade Profile template or gate. JSON sha256 `a5da3c92241a5b9cefbb5b3c9c7c563f97fad6306074aa670d5b6aec699431d0`; startup `guards=102 | range_lines=100 | not_and=2 | bounds=167 | … | template=CALL:23,PUT:23 | … | missing_reject=10`. Replay 8/19–10/9: Total **4,270 / 948W/3,322L / −$154,381.21**; Trade Profile **222 / 169W/53L / +$93,012.62**. Deployed Research Demo admit is still `wide_collect` (10.5 observe-only).

**Oct 9 t151u (10.5 Trade Profile: TL2+ CALL `delta >= 0.22493`; local next-deploy candidate, not deployed):** RESEARCH only. Adds one line to TL2+ CALL in `easy0DTE/configs/trade_profile_10.5.json` (CALL delta, missing passes) that rejects NVDA-237.5c-357 (−$94.90, delta 0.20975); 0 admitted winners lost on 8/19–10/9 (nearest kept winner 0.24011), ±5% robust, `delta` 100% filled. JSON sha256 `8afb87552165c82cc9c225ce3666d3d763e142b68ca980730e4f39ac10cc68d5`; startup `guards=102 | range_lines=100 | not_and=2 | bounds=166 | … | missing_reject=10`. Replay 8/19–10/9: Total **4,270 / 948W/3,322L / −$154,381.21**; Trade Profile **226 / 169W/57L / +$92,647.12** (was 227 / +$92,552.22). Note: the deployed Research Demo admit is `wide_collect` (10/9, `00596-sqc` / `00597-gdh`) with 10.5 observe-only; this JSON is the 10.5 profile for the next TP-admit deploy or for observe stamping. Backup: `~/grokbot_transfer/t151_backup/`.

**Oct 14 t168u (dir_pct_change_5m: one path-agnostic 5m chain pre-sampler; local next-deploy candidate, not deployed):** RESEARCH only. The t167u TL near-line and Breakout tick hooks are removed (`modules/prime_trading_system.py` and `modules/breakout_watch.py` are byte-identical to their pre-t167u versions). There is one entry point, `central_tech/dir5m_presample.on_discovery_quotes(system, quotes)`, called once per unified discovery tick in `modules/discovery_watch_batch.run_unified_0dte_discovery_scan`.
- **Sources:** it reads every registered level source: `_orb1_0dte_watch_candidates`, `trendline_signal_engine.candidates` and `_breakout_watch_candidates`. A new path calls `register_level_source`.
- **Levels and triggers:** one common level interface (`dir5m_levels()` → `trendline.value_at()` → `orb_high`/`orb_low`). Armed states (CONFIRM / READY / BREAK_DETECTED) count as distance 0.
- **Ranking and caps:** closest first (`DIR5M_APPROACH_RANK=close`), then the caps.
- **Chain fetches:** P2 through `chain_fetch_manager`, behind the P0/P1 reserve. The sampler makes no broker calls of its own.
- **Pre-warm:** default slots move to `06:40,06:50,07:00,07:26,07:33` (was `06:36,06:53,07:00`). Each pre-warm sample calls `note_open_seed`.
- **Telemetry:** `dir_pct_approach_lead_s` and `dir_pct_approach_source` are stamped on every decision. Both are in `CHAIN_REFRESH_KEYS` and the freeze `dir5m` block.
- **Replay 9/15–10/14 (3,240 decisions):** stamp rate 18.6% → 81.2% (strict 9.0% → 43.0%) at about 1,900 sampler + about 650 pre-warm chains/day.
- **Proposed deploy env:** `DIR5M_APPROACH_SAMPLER_ENABLED=true` (code default `false`). Rollback: unset it.
- **Policy:** `dir_pct_change_5m` stays missing-pass and is not a gate key until the measured stamp rate is ≥ 95–100%.
- Details: `docs/0.ProfitabilityThresholds/10.14/grokbot_books/dir5m_unified_t168u.md`. Backup: `~/grokbot_transfer/t168_backup/`.

**Oct 14 t161u (10.5 Trade Profile dir_pct replacement lines; local next-deploy candidate, not deployed):** RESEARCH only. Three lines are added in side template order to `easy0DTE/configs/trade_profile_10.5.json` beside the existing `dir_pct_change_5m` lines (which stay): TL1 PUT `spy_change_pct >= -0.598051`; TL2+ PUT `put_chain_open_interest >= 8016`; Breakout CALL `spy_change_pct <= 1.156517`. Missing values pass (no missing_reject). guards 109, range_lines 107, bounds 174. TP book 8/19-10/14 is unchanged at 230 / +$96,690.72. JSON sha `2a820749c63f...`. See [0.TradeProfiles.md](0.TradeProfiles.md).

**Oct 14 t160u (10.5 Trade Profile epsilon widenings; local next-deploy candidate, not deployed):** RESEARCH only. Four bounds in `easy0DTE/configs/trade_profile_10.5.json` are widened in place (no new lines; template order and counts unchanged; `dir_pct_change_5m` lines kept): Breakout PUT `price_volume_efficiency_3m` min `0.06752637063568141` -> `0.019405`; Breakout CALL `price_volume_efficiency_3m` min `0.0807188254` -> `0.012915`; Breakout CALL `chain_sanity_best_same_side_spread_pct` strict `> 0.6329` -> inclusive `>= 0.579709` (strict_bounds 6 -> 5); TL1 PUT `vega` max `0.06297000000000003` -> `0.071131`. Complete 8/19-10/14 TP book 224 / +$96,287.92 -> 230 / 176W/54L / +$96,690.72. JSON sha `3cc02daecd11...`. See [0.TradeProfiles.md](0.TradeProfiles.md).

**Oct 9 t146u (10.5 Trade Profile: TL0 PUT drops `dir_pct_change_5m`; local next-deploy candidate, not deployed):** RESEARCH only. `easy0DTE/configs/trade_profile_10.5.json` TL0 PUT: the `dir_pct_change_5m` range line and its `missing_reject_keys` entry are removed (fill 1/23 on 10/1-10/5 executed TL0 PUT trades and 28/100 on 10/8 decisions; the line mostly rejected missing stamps). No ORB1/TL0 cell gates `dir_pct_change_5m` now. Added in template order on keys 100% filled 9/10-10/9: `call_chain_vol_oi <= 5.831114988040195`, `spy_change_pct >= -0.5827321350421744` (raw SPY % change, not side-signed; 0.0/missing passes), `distance_from_vwap_atr <= 0.6064253192728054` (warm-up key, listed in `missing_reject_keys`, so TL0 PUT = `price_volume_efficiency_3m`, `distance_from_vwap_atr`, `rsi`). JSON sha256 `ddc8183086cb85f2a185fe12c836e388561a304dff3bbf1312b4982862d1d6be`; startup `guards=101 | range_lines=99 | not_and=2 | bounds=165 | … | missing_reject=10 | missing_reject_cells=…;TL0 PUT:price_volume_efficiency_3m+distance_from_vwap_atr+rsi`. Confirmed 8/19-10/9: Total **4,270 / 948W/3,322L / −$154,381.21**; Trade Profile **227 / 169W/58L / +$92,552.22** (was 213 / 157W/56L / +$90,023.82; +13W/+2L/−1W, +$2,528.40). Of the five losers plain removal would admit, D-AMZN-252.5p-892, D-META-632.5p-135 and D-QQQ-706p-337 stay rejected; D-SLV-61.5p-451 (−$10.60) and D-INTC-86p-780 (−$3.90) admit. Must-keeps AMD-605p-222 and SLV-59p-121 admitted. Backup: `~/grokbot_transfer/t146_backup/`.

**Oct 9 t144u (10.5 Trade Profile leak-free widenings; local next-deploy candidate, not deployed):** RESEARCH only. Four existing bounds in `easy0DTE/configs/trade_profile_10.5.json` are widened in place (no new lines; template order and counts unchanged): Breakout CALL `break_distance_pct` max `0.020604115553599988` -> `0.024030000517866468`; TL2+ PUT `otm_pct` max `0.001149` -> `0.004095279074914292`; TL1 PUT `put_chain_vol_oi` max `3.6337026975667706` -> `4.997383584973826`; Breakout PUT `price_volume_efficiency_3m` min `0.0720923434777612` -> `0.06752637063568141`. Each admits one rejected >50%-MFE winner (D-CRWV-102c-998 +$413.40, D-IBIT-45p-946 +$288.90, IWM-280p-999 +$139.10, TSLA-377.5p-568 +$106.80) with 0 new losses. Local candidate sha256 `40f4614be62395c61565f2e405122cba54f958057e4bc0ee2e116f27dcf79131` (was `62375383…`); startup counts unchanged `guards=99`, `range_lines=97`, `bounds=164`, `missing_reject=10`. Replay 8/19–10/9 (full 4,270-trade population): Total **4,270 trades, 948W/3,322L, −$154,381.21**; Trade Profile **213 trades, 157W/56L, +$90,023.82** (was 209 / 153W/56L / +$89,075.62; +$948.20); losses avoided unchanged; must-keeps AMD-605p-222 and SLV-59p-121 admitted. Details: [0.TradeProfiles.md](0.TradeProfiles.md) section 2.

**Oct 9 (10.5 Trade Profile local next-deploy candidate; not yet deployed):** RESEARCH only. The local `easy0DTE/configs/trade_profile_10.5.json` now adds two fill-audited TL0 repair lines after the 10/9 session: TL0 CALL `distance_from_vwap_atr >= -1.48488521464437` and TL0 PUT `price_volume_efficiency_3m <= 3.00318119990692`. Both keys are in the 23-key template order and are now included in the relevant TL0 `missing_reject_keys`; missing values reject instead of passing inertly. The researched TL1 PUT `option_l1_imbalance <= 0.834254143645409` line was not shipped in this batch because its 9/10–10/9 TL1 PUT fill audit was only 208/355 rows (58.59%), and TL1 missing values pass; later explicit t153u/t154u updates added the corrected boundary-safe `<= 0.82721479112145` line. Local candidate sha256 `62375383045f7c3cbe71b4624341635bb78b67005ccf3755b5169fc5f3cd0aee`; startup counts `guards=99`, `range_lines=97`, `bounds=164`, `missing_reject=10`. Confirmed 8/19–10/9 replay: Total **4,270 trades, 948W/3,322L, −$154,381.21**; Trade Profile **207 trades, 153W/54L, +$89,187.72**.

**Oct 3 (10.2/10.3 Trade Profile — DEPLOYED; Oct 5 Cloud Run logs show `version=10.2nh` live):** RESEARCH only. The admit is now the **10.2/10.3 Trade Profile (no history, with greeks)** in [0.TradeProfiles.md](0.TradeProfiles.md). It uses the same `trade_profile` gate and the same stack (`trade_profile` only). Changed: `easy0DTE/modules/trade_profile.py` (10.2/10.3 gate + version switch), `easy0DTE/modules/quality_gate_stack.py` (reads the 6 chain ratios and `put_chain_volume_delta_5m` already stamped by `central_tech`, plus the decision log line), new `easy0DTE/configs/trade_profile_10.2_nohist.json` (the profile, applied on the 10/1 tables and `greedy_mined_profile_rules.csv`), new tests `tests/test_trade_profile_10_2_*.py`, and docs `0.TradeProfiles.md` / `10.2_Implementation_Readiness.md`. `TRADE_PROFILE_VERSION`: unset (default) = 10.2/10.3; `10.1` rolls back to 10/1 Superior. `TRADE_PROFILE_SHADOW_LOG` (default `true`) adds the other version's verdict to each decision line. Startup logs `TP_PROFILE_LOADED` (JSON and CSV sha256, 15 guards, 1 lane, 1 tightened row). Each verdict logs one `TP_DECISION_VALUES` line (version, path/side, admit, reason, every key as read, `missing=[…]`). 8/19–10/2 replay through the app gate: **416 admits, 257W/159L, $85,138.91**; 10/2: **24 entered (17W/7L), 14 rejected**. Deployed before the Oct 5 session (gate log lines carry `version=10.2nh`).

**Oct 1 (package B, Research Demo `00587-585`):** RESEARCH only. PATH_V2 in `Shared.env` §2b now has `OPTION_PATH_V2_GB30_GIVEBACK=0.24` (detail `GB30_24PP`; old `GB30_25PP` still normalizes). Two new exits are on: `gb32_timed_giveback` (`OPTION_PATH_V2_GB32_TIMED_ENABLED=true`, detail `GB32_40_TIMED_15PP`, passive limit) and `deep_green_stop` (`OPTION_PATH_V2_DEEP_STOP_ENABLED=true`, detail `DEEP_STOP_10_30_65`, moderate aggressive limit, not a market order). Both are the last PATH_V2 checks and fire only when no other exit fired on that tick. Shallow time stays `900` / `0.075`. Mandatory exits stay mandatory and still run first. Versus the 25pp GB30 baseline on the 8/19–10/1 replay: Trade Profile **$85,939.23 → $86,257.23**, **+$318.00**; Total **−$172,014.31 → −$168,713.31**, **+$3,301.00**. Both books are positive on the fit dates (8/19–9/19) and the holdout dates (9/22–10/1). Serving revision is `easy-etrade-strategy-00587-585` (1 Oct 2026, 7:25 PM PT, MODE=demo, 100% traffic). It replaces `easy-etrade-strategy-00586-c94`. Ledger was not reseeded. Final Live was not deployed. — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Oct 1 earlier (PC V2 loss-management thresholds, GB30 later moved to 0.24 by package B):** RESEARCH only. Existing PATH_V2 exits updated in `Shared.env` §2b: `OPTION_PATH_V2_SHALLOW_TIME_STOP_SECONDS=900`, `OPTION_PATH_V2_SHALLOW_TIME_STOP_MAX_MFE=0.075`, and at that step `OPTION_PATH_V2_GB30_GIVEBACK=0.25`. Canonical exit ids stay `shallow_time_stop` and `gb30_giveback`; the detail alias at that step was `GB30_25PP` (current detail is `GB30_24PP`). Mandatory exits remain mandatory and still run first. 8/19–10/1 exact PC V2 replay: Total Results Book **−$170,272.81 → −$167,486.31**, **+$2,786.50**; Superior 10.1 Trade Profile Book **+$73,370.50 → +$74,113.14**, **+$742.64**. `last_stand_exit` standalone was weaker and added no overlay lift on top of this combo, so it remains research-not-shipped. These numbers are the step before package B and use a different replay basis than the package B books above. — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Sep 30 (9/30 Trade Profile and 350% runner):** RESEARCH only. `runner_timed_capture` and `runner_profit_floor` are on in `Shared.env` §2b (`OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED=true`). The 8/19–9/30 no-lookahead matrix selected arm `3.50`, timed current return `4.00`, peak ratio `0.50`, arm age `60s`, floor age `600s`, and floor ratio `0.80`. Both are passive limits. They run after `shallow_proof_stop`, `catastrophic_premium_stop`, afternoon `mfe_trail`, and `absorption_exit`, and before the hybrid trail, spike fail, stale fail, contract fade, shallow-green, GB30, and `hwm_rtc`. Versus current PC V2 baseline: total book **+$6,046**, Trade Profile book **+$4,420**, combined **+$10,466**, 18 improved / 5 worsened. The AMZN-max alternative (`3.50` / `6.00` / `0.80`) captured AMZN better but gave lower combined book lift and more worsened trades. Serving revision `easy-etrade-strategy-00585-86v` (30 Sep 2026, 6:14 PM PT, MODE=demo, 100% traffic) replaces `easy-etrade-strategy-00584-hdf`. It ships the 9/30 Trade Profile overlay and the 350% runner (arm `3.50`, timed return `4.00`, arm age `60s`, floor `0.80`). Ledger was not reseeded. Final Live was not deployed. — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Sep 28 evening (demo $5,000 reseed, `00579-vpg`):** RESEARCH Demo `easy-etrade-strategy` · MODE=demo · one-time seed wrote `research_app/mock_options_history.json` at **$5,000**, 0 closed, 0 open, then `DEMO_SIM_LEDGER_SEED_ON_STARTUP` was removed. Open book **7**. Dollar sum cap **off**. FINAL Live was not deployed. Replaced **`00578-8m7`** (seed write) and **`00577-bbl`**.

**Sep 29 (quality gate, local):** The admit stack is `trade_profile`, then `call_ss`, then `put_ss`. Call SS and Put SS default off. Chain-share dir% and put/call OI ratio are not on the stack. Serving revision `00579-vpg` still has the prior stack until `deploy now`.

**Sep 28 evening (Trade Profile, Demo `00577-bbl`):** RESEARCH Demo `easy-etrade-strategy` · MODE=demo · ledger not reseeded · FINAL Live not deployed. Admit is the Trade Profile in [0.TradeProfiles.md](0.TradeProfiles.md). Entry, Skip, Delta Moon, dir%, volume ratio, and contract delta / OTM / liquidity / spread gates are off. Expected key missing rejects. This revision also includes the PATH_V2 settings already in `Shared.env`. Health ok. Replaced the prior demo revision.

**Sep 28 (PATH_V2 stale high fail and spike fail on, hybrid trail off, shipped on `00577-bbl`):** RESEARCH only. `OPTION_PATH_V2_STALE_FAIL_ENABLED=true`. `OPTION_PATH_V2_SPIKE_FAIL_ENABLED=true`. `OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED=false`. On the 9/10–9/25 Entry/Skip book (CALL ≥ 110k, PUT ≥ 11k, C2, R-PUT, 214 trades, theta off) spike fail plus the stale high fail replay at **$22,913.30** net, **$34,565.30** wins, **−$11,652.00** losses. The full 8/19–9/25 Entry/Skip book, 472 trades, replays at **$18,972.82** net, **$55,364.36** wins, **−$36,391.54** losses. The richer upper, reversal, and gap sales bank INTC, META, and QQQ-715 at +$171.20, +$71.40, and +$88.20 and reduce no winner. The hybrid stages stay off. Local until `deploy now`. — [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Sep 27 (exit catalog, local):** RESEARCH only. Profit Capture exits: `contract_fade_exit` (Contract Fade Exit, on), `shallow_green_exit` (CALL only, on), `hwm_rtc`, `premium_trail` (175% and 100%, both off), `gb30_giveback`, `immature_stress`, `catastrophic_premium_stop`, `shallow_time_stop`. Mandatory, do not remove: `mfe_trail` after 12:30 PT, `end_of_day_exit`, `absorption_exit` (age ≥ 450s, expansion speed &lt; 0.00005, pret ≥ +2%), `no_data` (90s), `degraded_data`. Experimental, off: `theta_exit` (`OPTION_PATH_V2_THETA_EXIT_ENABLED=false`; age ≥ 300s, running MFE &lt; 0, theta ≥ −0.50). `contract_fade_exit` and `shallow_green_exit` are normal exits, not mandatory and not experimental. Catalog and knobs: [0DTEProfitCapture.md](0DTEProfitCapture.md). Local until `deploy now`.

**Sep 23 evening (PATH_V2 max-net, local):** RESEARCH+FINAL — 0DTE singles use PATH_V2 (`OPTION_PC_ARCHITECTURE=path_v2`, `OPTION_PATH_V2_ENABLED=true`). Active-expansion warning is on for flat/red immature stress (90s window, 45s stall, +2pp cancel). Premium explosion trails are implemented and off. Afternoon `mfe_trail` −10pp after 12:30 PT and `end_of_day_exit` at 12:50 PT stay on. Catastrophic stop is −80% after 60s. Legacy stack is [0DTEProfitCaptureLEGACY.md](PCLegacy/0DTEProfitCaptureLEGACY.md). Full knob and exit list: [0DTEProfitCapture.md](0DTEProfitCapture.md). Research Demo `00561-mbs` is the earlier PATH_V2 ship and does not include the warning or the premium-trail switches. Final Live was not deployed. This max-net update stays local until `deploy now`.

**Sep 21 evening (Absolute SS 9.21 PRUNED-8/8):** RESEARCH Demo **`00555-lnl`** — `CALL-SS-PRUNED88` / `PUT-SS-PRUNED88` (PE100 8/8 from `ABS-SS-OBSERVE-LADDER-922`) + Absolute SS hydrate (`break_distance_pct` fraction · `abs_vwap_distance_pct`). `GATE_call_ss=hard` · `GATE_put_ss=hard` · `GATE_path_ss=off`. Replaces PC921-C. MODE=demo. `$30k` ledger preserved (no reseed). FINAL Live was **not** deployed. Replaced **`00554-v7h`**. Pocket keys: [live keys](#absolute-call--put-skip-signals--live-keys) · [0.0MFESkipSignal.md](SkipSignal/0.0MFESkipSignal.md) · [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md).

**Sep 21 (Absolute SS PC921-C ship — superseded):** Was CALL-SS-PC921-C / PUT-SS-PC921-C (PC918-safe + STRUCTURAL-4 / ABS-NOCLOCK-5). Replaced same evening by PRUNED-8/8. Freeze `ABS-SS-STRUCT-AE-922` historical.

# Configuration Guide (Configs.md)
## Easy 0DTE — config reference

**Last Updated:** November 6, 2026 (local runtime pack not on serving: this-tick bid pret + HOLD, leftover OCC skip, discovery scan vs watch, notification-only Telegram; local 10.23 S0_maxnet sha `a449ab0d…`; serving still `00625-wwv`) · November 5, 2026 (serving `00625-wwv`: sequential OCC + UL lane yield, reusable-gated OCC defer, eval JSONL engine/qty/fee keys; rollback `00624-8bn`) · November 4, 2026 (serving `00624-8bn`: `_monitor_loop` pause + empty-book force-flag clear; `u0dte_<ms>` tick ids; UL 2s loop; JSONL 5s / daily 45s; PATH_V2 restore keeps entry time / live HWM; PATH_V2 live/replay parity — ULR UL`<=0` + master Save gate; sparse-velocity removed; one eval per `(trade, tick)` with HOLD reuse; recorder every eval tick (`eval_now`); frozen-contract `exit_bid`; PT `America/Los_Angeles`; Save ON CALL-only) · November 3, 2026 (STEP 2 stall save + UL-retrace; now on `00624-8bn`) · November 2, 2026 (then-serving `00620-btt`; Wide `equity_volume_ratio` ≥ **0.012** on every path; empty two-sided chain cache `force_live` / `data_wait:chain_quotes`; listed-expiry identity — fill OCC = `selected_expiry`; no same-day remap of a later payload; 1002 skip when the day is memo-empty; OCC repair uses fill hint / `nearest_for`; calendar skip matches Training Calendar) · October 30, 2026 (serving `00619-xr6`; persist clocks, OCC greeks, stale-chain null, owned 5m samples, flatten bar prime, executed freeze lock, first_red OCC age, owned UL skip_cache last, strike handoff; rollback `00618-cvb`) · October 29, 2026 (serving `00618-cvb`; sim reseed $100,000; discovery does not skip a break on exit-quote latency; local source drops the flag-off admit) · October 27, 2026 (desk pulse `DESK_SNAPSHOT_MARK_INTERVAL_SEC=1.0`) · October 26, 2026 (single chain door `fetch_0dte_intent_chain`; shipped in the Oct 29 image) · October 22, 2026 (Mon–Thu `optionexpiredate` first, then one same-day or nearest chain; Friday same-day-first; deployed Research Demo is `SINGLE_TP_ADMIT=true` / `TRADE_PROFILE_VERSION=wide` since 10/13)

**Sep 26 (decision data + Live Pilot, local):** RESEARCH and FINAL. Required gate inputs are classified before comparison: **VALID** (zero is a real number), **NOT_READY** (same break is held), **DATA_ERROR** (missing or non-finite required number rejects), **NON_APPLICABLE**. A blank entry chain volume does not admit. Moon cannot clear a missing chain. Chain fetch failure fails closed. Loader order is later-file-wins. Same-value repeats are harmless. Two different values for one active key are a defect. Live Pilot: `0DTE_SLOT_COUNT=7`, `TOTAL_0DTE_MAX_POSITIONS=7`, path caps **7**, `0DTE_MAX_POSITION_SIZE_SUM=60`, `LIVE_MAX_CONTRACTS_PER_TRADE=1`. Research demo seed **$30,000**. Final demo seed **$100,000**. Profit Capture remains PATH_V2. Not deployed.  
**Subject:** Env files, merge order, ownership, and operator defaults for the **RESEARCH app**.  
**Sep 23 evening (PATH_V2 max-net, RESEARCH=FINAL, local — earlier ship, superseded by the Sep 28 catalog):** That ship’s profit capture for 0DTE singles was PATH_V2 in `Shared.env` §2b. `OPTION_PC_ARCHITECTURE=path_v2` and `OPTION_PATH_V2_ENABLED=true`. Min hold 120s. Immature exit when running MFE &lt; 15% and live_stress ≥ 0.40. Flat/red active-expansion fires hold up to 90s (`OPTION_PATH_V2_AE_WARNING_ENABLED=true`); green stress exits are not deferred. Hold from 15% to 50%. Mature RTC when retrace ≥ 20%, material high-water age ≥ 300s, and 10s premium velocity ≤ −0.0005. Catastrophic stop is premium −80% after 60s. After 12:30 PT, `mfe_trail` −10pp is required. `OPTION_PATH_V2_PREMIUM_TRAIL_ENABLED=false` and `OPTION_PATH_V2_EARLY_PREM_TRAIL_ENABLED=false`. Batch `end_of_day_exit` flattens leftovers at 12:50 PT. Fills stay the liquidable bid. EARLY_BE / PX2 / PX3 are bypassed while PATH_V2 is on. Kill switch: `OPTION_PC_ARCHITECTURE=current` or `OPTION_PATH_V2_ENABLED=false`. PC8 is removed. Research Demo `00561-mbs` does not have this warning or these trail switches. Final Live was not deployed. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Sep 22 evening (bid-aware working BE — now the legacy stack):** EARLY_BE **20%/5m/−5%** + PRE_ARM hold **30m** + bid-aware working BE. Preserved in [0DTEProfitCaptureLEGACY.md](PCLegacy/0DTEProfitCaptureLEGACY.md). Cloud `00556-78z` still has the older premium path.  
**Sep 22 (first-latch vs overlay — historical remine):** First-latch ranking without working BE preferred EARLY_BE **20%/5m/−2%** on 9.10–9.21: **−$53.0k** all / **+$3.2k** &gt;0% / **+$26.3k** ≥30%. Overlay BEST +$3.2k / +$42.9k gt0 is overlay-if-better (not live). **Superseded same evening** by 5m/−5% + working BE. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Sep 21 (Path SS off — collect more book):** RESEARCH+FINAL — `GATE_path_ss=off` · `GATE_ORB/TL/BO_path_ss=off`. That cloud revision kept CALL-SS-PRUNED88 / PUT-SS-PRUNED88 hard on Demo `00555-lnl`. **Superseded locally on 2026-09-27** by C2 and R-PUT in [live keys](#absolute-call--put-skip-signals--live-keys). Path knives stay in code and do not reject. Not deployed.  
**Sep 21 (bid-calibrated PC, RESEARCH=FINAL — historical):** Remine on **path liquidable bid** (not floor stamps). Then-shipped: `OPTION_EARLY_BE_MAX_MFE_PCT=0.20` · `MIN_HOLD_MINUTES=5` · `MIN_CURRENT_PNL_PCT=-0.02` · `OPTION_WEAK_LOSS_CAP_FLOOR_PNL_PCT=-0.10` · pre/low-ARM settle `MIN_HOLD=20` / `MIN_CURRENT=-0.05` · stall@90 / MFE≥50 / HIGH_MFE 30pp · Band B wrap-before-skip. **Sep 22 evening superseded EARLY_BE to −5% and PRE_ARM hold to 30m + working BE.** Marks: `0DTE_*_PRICE_FIELD=bid`. Floors = decisions; fills = bid/broker. ≤0% MFE = QG skip. 9.10–9.21 bid: book −$72.8k. Overlay BEST **+$3.2k** / >0% **+$42.9k** is **not** live. — [0DTEProfitCapture.md](0DTEProfitCapture.md) · [PC_BID_REMINE_REVIEW_9.10-9.21.md](0.ProfitabilityThresholds/9.18/PC_BID_REMINE_REVIEW_9.10-9.21.md).

**Sep 20 EOD+ (Demo $30k reseed):** RESEARCH Demo **`00549-nr6`** (seed write on **`00548-w26`**, then one-time `DEMO_SIM_LEDGER_SEED_ON_STARTUP` cleared) — wiped options trade history · `0DTE_DEMO_STARTING_BALANCE=30000` · GCS `research_app/mock_options_history.json` **$30,000** / 0 trades. Ships CALL-SS-PC918 + PUT-SS-PC918 · LIVE PC (PX2.6/6b/6c · EARLY_BE · CAP_weak) · `0DTE_SLOT_COUNT=60` = `TOTAL_0DTE_MAX_POSITIONS=60` · `$600` per-open cap. Path SS still **off**. MODE=demo. FINAL Live was **not** deployed. Replaced **`00547-f4t`**.  
**Sep 17 (local true-to-live fills):** Protect / premium-stop / BE floors stay **exit decisions**. Demo STC fill = executable **bid**; Live = broker `average_fill_price`; net `$` = fill minus **$0.65**/contract/side. Open marks, Telegram headlines, and desk tape use the same net. `OPTION_SINGLE_PREMIUM_STOP_SETTLE_AT_STOP=true` still **fires** at −60%; it must not **record** −60% as the fill. Cloud Run **`00545-xvl`** still stamps floors until an explicit post-EOD `deploy now`. — [0DTEProfitCapture.md §3.11](0DTEProfitCapture.md#311-fills-and-net-pnl-true-to-live) · [PositionMonitoring.md](PositionMonitoring.md).  
**Sep 17 (PC floors confirmed, RESEARCH=FINAL):** 8.19–9.16 winner-path replay keeps **`price_stop` −0.80%**, **`premium_stop` −60% / MFE &lt; 15%**, **nonstarter 20m / −40% / MFE &lt; 10%**. Profit Capture and open-position **decisions** stay **bid vs entry, no fees**. Exited frontend / Telegram stay fill + fees. No env change. No deploy. — [0DTEProfitCapture.md §3.2](0DTEProfitCapture.md) · [PositionMonitoring.md](PositionMonitoring.md).  
**Sep 16 EOD+ (deploy):** RESEARCH Demo **`00545-xvl`** — after-hours deploy of current RESEARCH tree for next session · clip library moved off the trading tree (desk `soundeffects/` only; Cloud Build 8.4 MiB, no SFX) · `$1M` demo ledger preserved (no reseed). Replaced **`00544-xtp`**. MODE=demo. FINAL Live was **not** deployed.  
**Sep 11 (local PC max-lift, RESEARCH=FINAL):** code+config ready for next deploy. Added `OPTION_SINGLE_NONSTARTER_STOP_*` (0DTE never-armed, held ≥20m, MFE <10%, cap at **−40%** — fire at −40%, fill = live bid), `OPTION_LOW_ARM_PROFIT_PROTECT_*` (+10% to <+20% MFE weak/degraded exits **decide** at **0%** after 30m held, 10m since HWM, current PnL ≤ −10%; fill = live bid), and `OPTION_SINGLE_PREMIUM_STOP_SETTLE_AT_STOP=true` (existing −60% `premium_stop` **fires** at the stop threshold; Sep 17 local source no longer stamps that floor as the fill). `USE_MARKET_ORDERS=false` activates the existing smart limit ladders for non-urgent option exits while urgent risk/data/EOD exits still route market by reason. Local 8.19–9.11 replay: **588** closed 0DTE trades, modeled lift **+$10,793.25**, **97 improved / 0 hurt**, **$0.00** harm to historical >30% MFE winning trades. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Sep 9 EOD+ (deploy):** RESEARCH Demo **`00542-t8q`** — after-hours deploy of current RESEARCH tree for next session · `$1M` demo ledger preserved (no reseed). Replaced **`00541-n6d`**. MODE=demo. FINAL Live was **not** deployed.  
**Sep 10 (local PC RESEARCH shadow):** PX2.6 stall-bank HWM retention — `PX2_STALL_MIN_RETENTION_OF_MFE=0.80` staged in `Shared.env` §2b + [0DTEProfitCapture.md §3.5](0DTEProfitCapture.md#35-pc-px2-after-arm--modulespx2_exitpy). Full-book 8.17–9.9 open-path CF **+$6,706** / **$0** loss impact / **0** new red days. Confirm `evaluate_px2_tick` honors on **`00542-t8q`** before treating as live. Not FINAL. Session notes: [0DTEProfitCapture.HISTORY.md](PCLegacy/0DTEProfitCapture.HISTORY.md) · [pc_stall80_fullbook.md](0.ProfitabilityThresholds/pc_improve_8.17-9.9/pc_stall80_fullbook.md).
**Sep 9 EOD (deploy):** RESEARCH Demo **`00541-n6d`** — PO chain ~5m fill pack (Layer C / warm ring seed · execute wall-clock chain restamp · prior `max_age` 6h · owned-strike/RS invent fixes) · `$1M` demo ledger preserved (no reseed). Replaced **`00540-8zg`**. MODE=demo. FINAL Live was **not** deployed. — [PriorityOptimizer.md](PriorityOptimizer.md) · [Data.md](Data.md).  
**Sep 8 (local PC deploy candidate):** PX3 MFE confirmation handoff clears pending before runner evaluation; MFE-confirm runner uses **0.40%** underlying trail and **50pp** premium ratchet. Current MFE recovery settings: thin floor giveback **10pp**, strong underlying gate **+1.00%**, strong floor giveback **20pp**. Aug 17–Sep 4 closed-book replay: expected winners **20877.2pp** vs actual **20105.8pp** (**+771.4pp**), all closed-trade lift **+2491.7pp**, modeled winner-to-red flips **0**. Local RESEARCH=FINAL; no cloud until `deploy now`. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Sep 9 (local PC):** Weak-exit pre-ARM profit protect — `OPTION_PRE_ARM_PROFIT_PROTECT_*` is live in RESEARCH+FINAL config/code. 0DTE singles that reach **+20%** premium HWM but stay below **+30%** ARM clamp weak/degraded exits (`no_progress`, `scratch`, `time_exit`, `price_stop`, `premium_stop`, etc.) to **+2%** after **20m** held, **10m** since HWM, and current PnL **≤ −5%**. It does not front-run healthy holds, `mfe_take_profit`, or `premium_trail`. Keeps `0DTE_ARM_TRAIL_PCT=0.30`; PX2/PX3 still own >30% winners. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Sep 5 (deploy):** RESEARCH Demo **`00538-ptv`** — Profit Capture updates + Path SS **off** (wide collect) · `$1M` demo ledger preserved (no reseed). Replaced **`00537-5js`**. MODE=demo. FINAL Live was **not** deployed. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Sep 4 EOD+ (Demo $1M reseed):** RESEARCH Demo **`00536-rvt`** — wiped options trade history · `0DTE_DEMO_STARTING_BALANCE=1000000` · one-time `DEMO_SIM_LEDGER_SEED` then seed flag cleared. Path SS still **off** (wide collect). MODE=demo. — [Configs.md](Configs.md).  
**Sep 4 EOD+ (deploy):** RESEARCH Demo **`00535-hpw`** — Path SS **off** · wide collect (dir% **>0.10** · vol floor · CALL/PUT SS **hard**) · knives stay in code (LS100-94x + MissedProfits clear). Replaced **`00534-bsh`**. MODE=demo. FINAL Live was **not** deployed. — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md).  
**Sep 4 EOD+ (Path SS off — RESEARCH wide collect):** RESEARCH — `GATE_path_ss=off` · `GATE_ORB/TL/BO_path_ss=off` · **live on Demo `00535-hpw`**. Absolute CALL-SS / PUT-SS stay **hard**. Wide collect dir% **>0.10** · vol floor stay. Goal: many sessions of loss/win fills for remine. Knives remain in code (LS100-94x + MissedProfits clear). — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md).
**Sep 4 EOD (deploy):** RESEARCH Demo **`00534-bsh`** — Path SS **hard** · **LS100-94x + full MissedProfits reject-knife removal** (141 pockets) · peak runtime **8Gi** / exit-quote concurrency **4**. Replaced **`00532-djh`**. MODE=demo. FINAL Live was **not** deployed. — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · [MissedProfits.md](SkipSignal/0.MissedProfits.md).  
**Sep 4 EOD (Path SS on — local, superseded):** briefly `GATE_path_ss=hard` with LS100-94x + MissedProfits clear; **superseded by Sep 4 EOD+ wide-collect off** above. Cloud Demo **`00534-bsh`** still shipped hard. — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md).  
**Sep 4 (local peak / gate):** RESEARCH+FINAL — Market API priority gate fix (`modules/broker_api_pacing.py`): holder stack recomputes active priority on release (stale high-pri after open-exact leave starved peers; Sep 4 Demo logs ~11k deferrals / 700 reqs) · release gate **before** inter-request pace sleep · deploy scripts pin **`8Gi`** + `0DTE_EXIT_QUOTE_BATCH_CONCURRENCY=4` (was `4Gi` / `2` override). Live Cloud still **`00532-djh`** (`4Gi` · concurrency **2**) until **`deploy now`**. — [Data.md](Data.md) · [PositionMonitoring.md](PositionMonitoring.md).  
**Sep 3 (local PC):** Near-ARM weak red exit protection — `OPTION_NEAR_ARM_PROTECT_MFE_PCT=0.20` holds red `no_progress`, `scratch`, and `time_exit` once premium MFE reached **+20%** but stayed below +30% ARM. MSFT CALL `D-MSFT-517.5c-236` reached **+27.66%** max bid and exited `no_progress` **−45.21%**; historical summaries Aug 17–Sep 2 show **91** weak exits in this band, **65** red, **+19.60pp** estimated red-exit drag targeted. Does not lower `0DTE_ARM_TRAIL_PCT=0.30`; true stops remain live. **Live on RESEARCH `00532-djh`.** — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Sep 3 (deploy):** RESEARCH Demo **`00532-djh`** — PO entry/exit **cache-first** hydrate (session tech + discovery quotes + Layer C TTL; async Path SS reject GCS) + PriorityOptimizer.md · Data/PositionMonitoring stamps. Replaced **`00531-d5l`**. MODE=demo. FINAL Live was **not** deployed. — [PriorityOptimizer.md](PriorityOptimizer.md) · [Data.md](Data.md).  
**Sep 3 (deploy):** RESEARCH Demo **`00531-d5l`** — accepted Path SS **LS100-93** (all 10 path×side) + admit hydrate harden + Path SS reject snaps + Sep 3 monitor/perf pack. Replaced **`00530-sc5`**. Superseded by **`00532-djh`**. FINAL Live was **not** deployed. — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · [MissedProfits.md](SkipSignal/0.MissedProfits.md) · [Data.md](Data.md).  
**Sep 3 (accepted Path SS):** ten path×side **LS100-93** knives (`*-0MFESS-LS100-93`) in RESEARCH + FINAL `quality_gate_stack` — skip all losses 8.31–9.3 · 0 win collisions 8.17–9.3. **Live on RESEARCH `00532-djh`.** — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · [MissedProfits.md](SkipSignal/0.MissedProfits.md).  
**Sep 3 (local):** early 9.3 PE/LS pocket drops (superseded by LS100-93) — drop SSORB1CALL `vol<1.5 |vwap%|≥0.5 IV<0.7` · SSTL1PUT `vol≥0.1 |vwap%|≤0.3 sec≤70` · SSTL2CALL `vol<2 |vwap%|≤1.2 vwap_with≤−1` · SSORB2CALL `dir%≤0.5 |vwap%|≥0.5 BB≥80` plus `IV 0.7–0.9 sec≥2` plus `dir%≤0.85 RSI≥80 IV≥0.3`. Superseded by **`00532-djh`**. — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md).  
**Sep 2 (deploy):** RESEARCH Demo **`00530-sc5`** (2.37.9) — 9.2 path SS knives + admit hydrate restamp (ATM IV/delta/otm from the same Layer C chain) · leftover OCC **off** owned lock / cap **0** · SSORB1CALL pocket 3 vol **<1.5**. Replaced **`00529-9cv`**. MODE=demo. For Sep 3 session. — [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) · [Data.md](Data.md).  
**Sep 2 (SSORB1CALL p3):** path SS stays **hard**. ORB1 CALL pocket 3 vol **<2 → <1.5** so GOOGL 06:56 (vol **1.596**, |vwap%| **0.57**) admits. `|vwap%| ≥ 0.5` and `IV < 0.7` unchanged. **Live on `00530-sc5`.** — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md).  
**Sep 2 (leftover tape):** Winner leftover OCC quoted **off** the owned lock · leftover underlyings that left the open book refresh on that leftover task · `0DTE_MAX_SHADOW_LEFTOVER_PER_TICK=0` (every winner) · do **not** skip leftover when owned is slow. **Live on `00530-sc5`.** — [PositionMonitoring.md](PositionMonitoring.md).  
**Sep 1:** RESEARCH Demo **`00528-tr9`** — process-survival (skip hydrate of the fat daily blob · persist off owned eval). Local tree walks back the 80-sample stride cap: **2s JSONL** per trade · daily JSON metrics-only · RAM ceiling **20000**. Do not deploy mid-session. — [PositionMonitoring.md](PositionMonitoring.md).  
**Sep 1 (Liquidity Guard):** ADV **2.0%** (`LIVE_UNDERLYING_ADV_PARTICIPATION_PCT=0.02`) · generic $200M floor **off** (`LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD=0`). Ask-depth **200%** · vol/OI/exit-book **off** · min bid/ask **1**. Local until **`deploy now`**. — [LiquidityGuard.md](LiquidityGuard.md).  
**Sep 1 (local pack):** QG Layer C yield/cache/cap · path chain cache-only on owned eval · **no `main_loop_tail` second tick** · leftover OCC **8**/tick + skip when cycle slow (**superseded Sep 2**: cap **0**, no skip-when-slow) · eval **1800ms** at 20+ open · PO checkpoint **90s** / min write gap **45s** · E\*TRADE fail-fast timeout and lotto slot-cap logs demoted.  
**Aug 31 (f):** RESEARCH Demo **`00527-64b`** (2.37.9) — QG admit hydrate live (`stamp_qg_admit_fields` · `watch_fsm` `qg_admit`). Wide collect unchanged. QG 31 **not** promoted. Replaced **`00526-pw2`**. — [Data.md](Data.md) · [0DTEQualityGates.md](0DTEQualityGates.md).  
**Aug 31 (e):** **QG admit hydrate** — Skip Signal / QG keys (sva, ATM IV, seconds, dir%, VWAP, RSI, vol, BB, L1d5) stamp **before** accept/reject from cached 1m bars + last equity quote + the **same** Layer C chain fetch (`stamp_qg_admit_fields`). Full 89-key PO snapshot stays **execution-only**. Compact `qg_admit` persists on `watch_fsm` (`discovery_watch_fsm_persist.py`). Missing stamps still do not skip. Live `GATE_*` unchanged. **Live on `00527-64b`.** — [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) · [Data.md](Data.md) · [Cloud.md](Cloud.md).  
**Aug 31 (d):** CODE **2.37.9** RESEARCH Demo **`00526-pw2`** — winner leftover OCC quoted **after** owned evaluate on the same 2s tick (`0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0`, `PROFIT_OPTIMIZER_MAX_SHADOW_CONTINUATIONS=0`, `0DTE_PO_CONTINUATION_MAX_OPEN=0`, `PROFIT_OPTIMIZER_LOSS_SHADOW_SECONDS=0`). Replaced **`00525-65b`**. Superseded by **`00527-64b`**. — [PositionMonitoring.md](PositionMonitoring.md).  
**Aug 31 (c):** CODE **2.37.8** — evaluate never initiates broker `get_quote`/`get_quotes` for underlying (age ≤4s fresh · ≤8s `last_good` · >8s always `unavailable`) · `run_fair_owned_eval_tick` · Profit Optimizer JSON/GCS persist **off** the owned-eval thread (`po-persist`). Live on RESEARCH Demo **`00525-65b`**. Signals / QG / Profit Capture unchanged. — [PositionMonitoring.md](PositionMonitoring.md) · [profit_optimizer/README.md](../profit_optimizer/README.md).  
**Aug 31 (b):** RESEARCH Demo **`00524-vng`** (2.37.7) — cache-only underlying on evaluate; replaced **`00523-pgj`**. Local tree is now **2.37.9**.  
**Aug 31:** RESEARCH Demo **`00523-pgj`** — discovery **fair rotate only** (next **24** / **10s**, always advance; 133 ≤60s; CONFIRM does not pin slots). Historical Cloud revision; superseded by **`00524-vng`**. Wide collect unchanged. QG 28 not promoted. — [ARCHITECTURE.md](ARCHITECTURE.md).  
**Aug 31 (a):** RESEARCH Demo **`00522-fts`** — persist_open_option_books uses `system` (fixes `name 'sys' is not defined` after every tick) · discovery `already_running` log throttled 5s. Wide collect unchanged. QG 28 not promoted. — [ARCHITECTURE.md](ARCHITECTURE.md).  
**Aug 30:** RESEARCH Demo **`00521-gwm`** — discovery rotate no longer skips WAIT_BREAK when CONFIRM extras fill the 24-cap · cooldown extras skip cooled-down BO/ORB1/TL sides only · live `str` Enum CONFIRM extras · no second equity fetch under open-quote priority · `already_running` does not restamp the 10s timer. Wide collect unchanged. QG 28 not promoted. — [ARCHITECTURE.md](ARCHITECTURE.md).  
**Aug 28:** **2s monitor spine on RESEARCH Demo `00520-fhx`** (after hours) — fill enroll sets `_0dte_force_monitor_tick` only (no second full-book `maybe_tick`) · discovery **24** while open (do not advance 10s timer on defer; skip schedule if monitor lock held) · `0DTE_MONITOR_EVAL_BUDGET_MS=1200` (pending fills never skip; trail-armed / PX3 first) · `0DTE_PATH_REFRESH_SKIP_CYCLE_MS=1500` · `stealth_restart_gap` only on **process start** (not starved ticks) · PX3 `trend_runner` **keep_px2** if already through PX2 20pp · P1: `TRENDLINE_EXECUTION_BLOCKED` = INFO · 15s heartbeats · enroll `failed_sec` scales with cycle · **`0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0`** · winners-only leftover cap **16** / sample **2s** · first-jump quarantine drops leftover · discovery allows a single owned miss on 3+ opens · WATCH_FSM datetime JSON-safe · MATCH logs miss-only. Live `GATE_*` = **wide collect** (QG 28 not promoted). — [PositionMonitoring.md](PositionMonitoring.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [0DTEQualityGates.md](0DTEQualityGates.md).  
**Aug 27 (f):** **Strategy/side 0-MFE Skip Signals live on the QG stack** (`GATE_path_ss=hard`) with absolute CALL-SS-1724 / PUT-SS-1724. Reject if either the side-wide skip **or** the path/side knife (`SSORB1CALL` · `SSTL1PUT` · …) matches. Wide collect stays. Local until **`deploy now`**. Live keys in this file and `Shared.env`. — [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) · [0DTERisk.md](0DTERisk.md) · [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md).  
**Aug 27 (e):** **0-MFE Skip Signal guide** — [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md). Per strategy × side print slots (ORB1 / TL0 / ORB2 / TL1 / TL2+ × CALL/PUT) for knives that skip **0 MFE** entries and keep **MFE &gt;1%**. Live copies are `GATE_path_ss` (this file / `Shared.env`).  
**Aug 27 (d):** **Peak-book runtime** — pending-first evaluate · PO shadow defer when open≥8 · quote batch concurrency **4** · `PROFIT_OPTIMIZER_MAX_PATH_SAMPLES=120` · Cloud Run memory **8Gi**. **(Superseded Aug 28:** fill enroll is flag-only — does **not** start a second full-book `maybe_tick`.) **Sep 4:** deploy scripts now pin **8Gi** + concurrency **4**; live `00532-djh` still `4Gi`/`2` until **`deploy now`**. — [PositionMonitoring.md](PositionMonitoring.md).
**Aug 27 (c):** **Watch Wait rolled back** — removed from all 0DTE paths. Spine is **Structure → Quality Gate → Confirm → PO Entry Snapshot → Enter Trade**. Local until **`deploy now`**. — [ARCHITECTURE.md](ARCHITECTURE.md) · [ProcessFlow.md](ProcessFlow.md).  
**Aug 27 (b):** **`price_stop` −0.80%** — `OPTION_SINGLE_STOP_PCT=0.008` (was 0.007) in `Shared.env` §2b for all 0DTE (ORB1 / Trendline / ORB2 BO). Aug 27 META CALL required wider than −0.725% to survive the −0.736% post-exit low; −0.80% sweep saved 21 later-positive stopouts for +784.9pp opportunity. Local until **`deploy now`**. — [0DTEProfitCapture.md](0DTEProfitCapture.md) · [0DTERisk.md](0DTERisk.md).  
**Aug 26 (d):** **PX2 early premium trail codified** — `PX2_EARLY_PREM_TRAIL_*` live in `Shared.env` §2b. Dual-sided pre-12:30 premium trail for exceptional +175% premium HWM winners; exits as `premium_trail` with `early_prem_trail=true`. Aug 17–26 replay: **45 triggers / 41 improved / 1 worse / 3 same**, **+7945.9pp** total premium-capture lift; AMZN Aug 26 **+81.2% -> +256.2%** at the 175% gate. Local until **`deploy now`**. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Aug 26 (c):** **`price_stop` −0.7%** — `OPTION_SINGLE_STOP_PCT=0.007` (was 0.005) in `Shared.env` §2b for all 0DTE (ORB1 / Trendline / ORB2 BO). MN26 17–26 PC-PX2 walk: −0.5% **+$25,516** · −0.7% **+$25,527** (GDX 19 recovers) · −0.8% **+$25,496**. Local until **`deploy now`**. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Aug 26:** **PX3 MFE confirmation runner codified** — `PX3_MFE_CONFIRM_*` live in `Shared.env` §2b. Dual-sided one-quote confirmation after qualified PX2 `mfe_take_profit`; Aug 17–25 full-live replay: **+112.9pp net**, **10 triggers / 7 improved / 2 worse / 1 same**, worst **−4.2pp**. Local until **`deploy now`**. — [0DTEProfitCapture.md §3.6](0DTEProfitCapture.md#36-px3-protected-runner--modulespx3_exitpy).  
**Aug 26:** **PX3 exceptional winner handoffs codified in Profit Capture** — `PX3_EXCEPTIONAL_*`, `PX3_THIN_LEAD_EXCEPTIONAL_*`, and `PX3_HIGH_HWM_EXCEPTIONAL_*` live in `Shared.env` §2b. Applies only after PX2 `premium_trail` on already-+100% winners; strict Aug 17–25 replay: 4 historical rows improved / **0 worse**; high-HWM refinement adds **+93.0pp**. Local until **`deploy now`**. — [0DTEProfitCapture.md §3.6](0DTEProfitCapture.md#36-px3-protected-runner--modulespx3_exitpy).  
**Aug 26 (b):** **Cloud Run kill-safety** — deploy scripts use **TCP :8080** liveness (not HTTP `/health` on the trading asyncio loop) · mock-ledger GCS **off-loop** · GCS `tl1_build_pending` (run unfinished TL1 after kill; do not stamp complete) · `stealth_restart_gap` rebases trail floors (**Aug 28:** process start / Cloud Run replace only — not starved ticks) · EOD flatten **resumes** until book empty. Local until **`deploy now`**.  
**Sep 20:** **CALL-SS-PC918 + PUT-SS-PC918 live** (replaces SS-1724) — PC-verified PE100 knives mined 2026-09-20 on 8.24–9.18 book (protect = PC net_pnl_pct > 10%; target = peak 0MFE ∩ PC loss). Levels live here and in `Shared.env` (`GATE_call_ss` / `QG_CALL_SS_*` · `GATE_put_ss` / `QG_PUT_SS_*`). **Aug 27 (f)** added `GATE_path_ss`. Code reads these keys; do not hardcode dump pockets. Local until **`deploy now`**. — [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) · [0DTEQualityGates.md](0DTEQualityGates.md).  
**Aug 25:** **CALL-SS-1724 + PUT-SS-1724 on the Quality Gate stack** (superseded by PC918). Legacy keys removed from Shared.env.  
**Aug 21 (d):** **PX2 / PX2.5 / PX3 Profit Capture codified** — `PX2_ENABLED=true` · `PX25_ENABLED=true` · `PX3_ENABLED=true`. PX2.5 is existing-position only: no re-entry / no new trades. Same 17–21 book: PX2 **+$22,297** → PX3 **+$36,713** → PX3+PX2.5 **+$38,647**. Local until **`deploy now`**. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Aug 21 (c):** **PX3 Protected Runner ON Demo** — `PX3_ENABLED=true`. Intercepts PX2 `premium_trail` only (no look-ahead). Independent 17–21: PX2 **+$22,297** → PX3 **+$36,713** · **51/51** · 0 flips. Does **not** replace PX2. — [0DTEProfitCapture.md §3.6](0DTEProfitCapture.md#36-px3-protected-runner--modulespx3_exitpy).  
**Aug 21 (b):** **`price_stop` −0.5%** — `OPTION_SINGLE_STOP_PCT=0.005` in `Shared.env` §2b for all 0DTE (ORB1 / Trendline / ORB2 BO). Local until **`deploy now`**. — [0DTEProfitCapture.md](0DTEProfitCapture.md).  
**Aug 21:** **Unified 0DTE entry policy** — shared knobs live in `Shared.env`: `0DTE_MAX_ENTRY_PREMIUM=2.50`, `0DTE_MIN_ENTRY_PREMIUM=0.03`, `0DTE_ENTRY_DELTA_MIN/MAX=0.18/0.42`, `0DTE_ENTRY_MIN_VOLUME=50`, `0DTE_ENTRY_MIN_OPEN_INTEREST=100`, `LIVE_MIN_ASK_SIZE=1`, `LIVE_MIN_BID_SIZE=1`. ORB, ORB2 Breakout, and Trendline all read the same entry policy; spread/OTM/cheap-lotto remain path-specific. Local until **`deploy now`**.  
**Sep 9:** **Liquidity Guard** — ask-depth / Book Ask size cap **off** (`LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT=0`) · vol/OI/exit-book **off** · ADV **2%** · min bid/ask **1** · hard max **off** — [LiquidityGuard.md](LiquidityGuard.md). Local until **`deploy now`**.  
**Aug 17:** **Liquidity Guard v1.6** — vol qty cap **off** · OI **off** · book = **100% of ask** · bid **not** a qty cap · min bid/ask **5** · hard max **off** — [LiquidityGuard.md](LiquidityGuard.md). Local until **`deploy now`**.  
**Aug 17:** **PC-PX2** expected next session — stall-bank + leftover ≤ **$10** + 20pp / lock@200% + BE entry $0 + 0.50% trail after und≥0.5% and prem≥60% · 12:30 −10pp required. Expected book **+$12,545 (56%)** vs overlay **+$12,200 (54%)** vs live thin TP **+$4,809 (21%)**. `PX2_ENABLED=true` · thin TP30 **off**. Local until **`deploy now`**. — [0DTEProfitCapture.md §3.5](0DTEProfitCapture.md#35-pc-px2-after-arm--modulespx2_exitpy).  
**Aug 17:** Moon hold H2 **removed**. Runner moons = +60% MFE → **0.50%** `price_trail`. Telegram `Moon: ⭐️` on exit **> +200%**. Local until **`deploy now`**.  
**Aug 18:** Discovery cutoff **12:30** (mfe_trail runs from here) · flatten **12:50** · EOD Telegram **12:55**. **Aug 16:** **TL1–TL4** ARMED first-break **3h** (`TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC=10800`) · TL hard reject/fill **expire that slot/side envelope** (A1/A2 persist) · **ORB1** 06:45–07:10 **one-shot** (reject = 1h same-side; fill = one trade, no re-entry) · **ORB2** reject/fill = 1h + **inside-ORB2 re-entry** (opposite edge open). Local until **`deploy now`**.  
**Aug 14 (e):** ORB/TL `price_stop` **−1.8%** (`OPTION_SINGLE_STOP_PCT=0.018`). BO stays **−0.8%**. Local until `deploy now`.  
**Aug 14:** next-session collect ORB1 + ORB2 BO dir% **>0.10** · vol **>0.12** · VWAP observe \|vwap%\| **≤1.00**. TL dir% **>0.10** unchanged. Local until `deploy now`.  
**Aug 13:** TL0 + ORB1 **hard-stop at 07:10** · TL admit = **`GATE_TL_VWAP` / `RSI` / `BB` / `Vol_Ratio` / `dir_pct`** · **`GATE_TL_Vol_Ratio_MIN=0.012`** · TL1–TL3 ARMED age **2h** · old dotted TL admit / PC-OI / ENABLE keys **deleted** (not listed as off) · RESEARCH Demo **`00492-4jv`** (morning prune); local key strip until `deploy now`.  
**Aug 12 (d):** **Moon hold On (H2)** — `OPTION_MOON_HOLD_*` · path force caps · invent-vol fail-closed — [0DTEProfitCapture.HISTORY.md](PCLegacy/0DTEProfitCapture.HISTORY.md) · local until `deploy now`.  
**Aug 12:** Discovery align — **≥06:45** ORB1 capture → Yahoo → TL0 inject → **TL0 + ORB1 BO discovery together** through **&lt;07:10** (`ORB_0DTE_DISCOVERY_START_PT=06:45`; closed 06:45→06:50 gap) · shared **24/10s** · local until `deploy now`.  
**Aug 11:** Discovery align — TL activate **0.04%** · **2h** age · TL2/TL3 Yahoo · BE/scratch bands — local until `deploy now`.  
**Aug 10:** RESEARCH Demo **`00482-hhp`** · watch-mode scheduler (07:10 arm → 07:30 ORB2→**BO unlock**→TL1 held→**TL join**) · **same-side 1h cooldown** (reject **+ post-exit**) · exit alert Expiry/DTE · `exact_quote` Stage B.  
**Aug 9:** Profit Capture §2b key names + Hold@0.55 prune.  
**Aug 7 lock:** Easy 0DTE **three-lane** — ORB1+TL0 morning discovery through **07:10** · TL + ORB2 BO **≥07:30–12:30** · Layer C **dir% hard** (ORB/BO `pc_oi` off · TL dir% only) · SO/SC/validation/PP/Red Day **paused**.  
**Slots (RESEARCH Demo):** `0DTE_SLOT_COUNT=50` = `TOTAL_0DTE_MAX_POSITIONS=50` · path caps ORB1/ORB2/TL **50** (deployed 10/13 in `00599-lhd`) · `0DTE_MAX_POSITION_SIZE_SUM=600` (10/9; was 0) · `LIVE_MAX_CONTRACTS_PER_TRADE=0` · 90% tradable / 10% buffer · demo seed **$100,000** (10/9).  
**Not this file:** Cloud deploy → [Cloud.md](Cloud.md) · path strategy → [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTETrendline.md](0DTETrendline.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · risk → [0DTERisk.md](0DTERisk.md) · skip-signal formulas → [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) · [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) · liquidity size governors → [LiquidityGuard.md](LiquidityGuard.md) · monitor → [PositionMonitoring.md](PositionMonitoring.md) · [SORisk.md](SORisk.md) · brokers → [Brokers.md](Brokers.md).

### Path matrix & loader

| Mode | Paths |
|------|--------|
| **Demo / Live (Aug 7)** | ORB1 0DTE watch + Trendline + ORB2 Breakout (`ENABLE_ORB_STRATEGY` / SC / validation / PP **false**) |
| **Rollback** | `ORB_0DTE_DISCOVERY_MODE=batch` + re-enable SC/PP (+ SO ETF only if desired) |

**Merge order:** `Data.env` → `Shared.env` → `ORBSO.env` → `Account0DTE.env` → `ORB0DTE.env` → `Trendline0DTE.env` → `Breakout0DTE.env` → `Risk.env` → `Alerts.env` (later wins).  
**Quick index:** [configs/README.md](../configs/README.md).

### Current deploy (Demo soak)

| Key | Value |
|-----|--------|
| **Cloud Run** | RESEARCH Demo service `easy-etrade-strategy` · **serving `00625-wwv`** (Nov 5 2026 UL/OCC pack, $100,000 seed, `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`). Local Nov 6 runtime pack is not on this revision. URL in [CloudSecrets.md](CloudSecrets.md). |
| **MODE** | `demo` |
| **Morning stack** | `ENABLE_ORB_STRATEGY=false` · `ENABLE_ORB_SIGNAL_COLLECTION=false` · `ENABLE_VALIDATION_CANDLE=false` · PP/Red Day **off** |
| **ORB1** | `ORB_0DTE_DISCOVERY_MODE=watch` · floor **06:45–07:25** (activate with TL0 after structure) · **`expire_orb1_discovery_after_cutoff`** · **one-shot** (fill = one trade) |
| **TL0** | After ORB1 ≥**06:45** → **Yahoo 1m** → dual-side ORB1 box build · persist `SYM:bull|bear:trendline0` · discovery **with ORB1** through **&lt;07:25** · geometry expires **07:25** (`tl0_geometry_cutoff_0710` reason token kept) |
| **Scheduler** | **≥06:45** ORB1→Yahoo→TL0 inject→**TL0+ORB1 discovery &lt;07:25** · **07:25–07:30** capture gap (arm TL1 **once**, GCS `tl1_build_pending`; no ORB1/TL0 entries) · **≥07:30** ORB2 freeze (~3–4s) → **BO unlock** → Yahoo→**single-flight** TL1 (~4–6.5s; **TL held**; after kill **run** unfinished TL1) → **TL joins** · TL2+ waits for TL1 + reclaim-inside-ORB2 |
| **Path consume** | (10/13 local: book full `max_open_positions_reached`, executor cap codes and dir% data waits never arm the 1h cooldown.) **ORB1** reject = 1h same-side (other H/L open until 07:25); fill = one trade · **ORB2** reject/fill = 1h + inside-ORB2 re-entry · **TL** hard reject/fill expire that slot/side envelope · opposite envelope / sibling slots stay open |
| **ENABLE_*** | 0DTE / Trendline / Breakout = `true` |
| **Monitor** | `0DTE_POSITION_MONITOR_INTERVAL_SEC=2` · **3s** at 20+ opens · `_monitor_loop` always pauses after `maybe_tick` · empty book clears `_0dte_force_monitor_tick` · watchdog restarts the loop (no second tick id) · tick ids `u0dte_<ms>` only · 2s `_ul_refresh_loop` (on `00625-wwv`: no 4s cancel; sequential OCC + UL yield; rollback `00624-8bn` still 4s-cancels) · `begin_underlying_tick` each evaluate · JSONL **5s** / daily JSON **45s** · `_LINE_COUNTS` locked · `_position_still_open` after each yield · every open name exit-checked · JSONL writes every PATH_V2 eval (`evaluated=true`; local rows keep `path_v2_peak` / qty / fees) · eval budget is a warning · cache-only und (4s/8s; UL `0` is unavailable to PATH_V2) · `stealth_restart_gap` on **process start** only · **local leftover OCC skip while owned** (`0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=true`) · losers skip shadow · **local pret** this-tick bid only. |
| **Peak sizing** | All deploy/redeploy scripts pin **`8Gi`** · `0DTE_EXIT_QUOTE_BATCH_CONCURRENCY=4` · `0DTE_POSITION_MONITOR_INTERVAL_SEC=2` · `0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0` · leftover cap **0**. **`00577-bbl`** ships this pin. |
| **Broker lanes** | `modules/broker_api_pacing.py` — `open_exact_occ` pri 0 > `equity_batch` pri 6; one Market HTTP. **Local:** `yield_lane_to_equity_batch` between OCC inner waves. Sep 4 gate stack (release recomputes active · pace after release) is on **`00545-xvl`**. — [Data.md](Data.md) · [PositionMonitoring.md](PositionMonitoring.md) |
| **Liveness** | No HTTP `/health` liveness (Cloud Run rejects TCP liveness; startup TCP :8080) — RESEARCH Demo **`00545-xvl`** |
| **Discovery** | Rotate **24** / **10s** CSV tier→row · cursor **always advances** · no status/rank queue · 118 names ≤**50s** full coverage · while open still **24** · a slow quote batch does not cancel the 10s rotation · **local:** a running watch must not occupy the scan slot (`unified_discovery_scan_slot_free`) · day-freeze hydrate-first · `watch_fsm` debounce · `discovery_watch_batch.py` |
| **Trade Profile** | **Live admit (since 10/13, serving `00625-wwv`): `SINGLE_TP_ADMIT=true` / `TRADE_PROFILE_VERSION=wide`.** 10/9–10/13 history: observe-only in the `wide_collect` stack. Local source has no flag-off branch. Catalog [0.TradeProfiles.md](0.TradeProfiles.md). **Current local flat successor 10.23 — … + S0_maxnet applied 11/6 (Eric pt81u): sha `a449ab0d773ccb1358b8f93d618d708b08f35e77a2fd7c21f45c577d2c548f88` (8/19–11/6 542 / 436W/106L / net +$159,400.39; not the live admit; 14 bounds in 0.TradeProfiles.md). Previous C0_maxws `46f4305d…` (backup `.bak1106_preS0`; 435W/107L / +$158,957.89). Earlier: P2_AMZN applied 11/5 (Eric t74u): sha `d480f7c157f54eba38ccf42e3ce16fccb2c7f7e0bf51d51e04f659957b1247f5` (backup before P2_AMZN `.bak1105_preP2`, sha `a3c4cc86…`; P2_AMZN = 9 flat bounds (ORB1 PUT 2, TL0 PUT 5, TL1 PUT 2) on N100 to recover 11/5 missed winners: ORB1 PUT spy_change_pct min -0.9080443415 → -1.803575853; ORB1 PUT rsi min 14.3983119 → 6.370764688; TL0 PUT chain_sanity_best_same_side_spread_pct min 0.8048322322 → 0.694452817; TL0 PUT vega max 0.069 → 0.064495; TL0 PUT spy_change_pct min -1.473627 → -2.038966628; TL0 PUT distance_from_vwap_atr min -2.31 → -5.058738080; TL0 PUT rsi min 26.0 → 12.011295621; TL1 PUT price_volume_efficiency_3m min 0.2718066 → 0.252128411; TL1 PUT spy_change_pct min -1.424235728 → -2.917313285; engine `32a80291` / `5e447ecd`; 8/19–11/5 TP 528 / 425W/103L / +$166,255.64 / −$6,977.55 / net +$159,278.09 (N100 520 / 420W/100L / +$157,205.79); red days 09/04 + 10/09; 11/5 TP 5W/0L +$2,477.70; see 0.TradeProfiles.md *10.23 CURRENT — S0_maxnet on C0_maxws (11/6)*). Previous: 10.23 — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 + A99 + N3 + N100 applied 11/4 (Eric t65s315): sha `a3c4cc8672053e21830a75ee707299fb082b12eabcedcc668dbe4a801b5e810a` (backup before N100 `.bak1104_preN100`, sha `0ef7a804…`; N100 = 16 flat bounds (Breakout CALL 7, TL0 PUT 5, ORB1 CALL 1, TL0 CALL 1, TL1 CALL 1, Breakout PUT 1), max net under losses ≤ 100 and loss sum ≥ −$6,600: ORB1 CALL vega max 0.08457 → 0.132065; TL0 CALL underlying_l1_imbalance min -0.9667585784 → -0.9936748168; TL0 PUT put_chain_volume max 29900 → 30028.5; TL0 PUT chain_sanity_best_same_side_spread_pct min 1.7 → 0.8048322322; TL0 PUT break_distance_pct min 0.0008005018602 → 0.0008355199031; TL0 PUT spy_change_pct max PASS → 1.688871214; TL0 PUT option_l1_imbalance max 0.9296150481 → 0.9511031604; TL1 CALL break_distance_pct max 0.02085629046 → 0.02583442669; Breakout CALL put_chain_open_interest max 172000 → 174043; Breakout CALL call_share min 0.4941137174 → 0.4764261764; Breakout CALL price_volume_efficiency_3m min 0.03962743495 → 0.01231184337; Breakout CALL break_distance_pct max 0.02162691707 → 0.0243277266; Breakout CALL distance_from_vwap_atr min -0.4103778043 → 5.675037139; Breakout CALL rsi min 34.59464 → 65.10182492; Breakout CALL option_l1_imbalance min -0.9 → -0.9344086022; Breakout PUT rsi max 54.84496862 → 58.08793004; engine `32a80291` / `5e447ecd`; 8/19–11/4 TP 520 / 420W/100L / +$163,777.94 / −$6,572.15 / net +$157,175.79 (N3 526 / 423W/103L / +$154,020.24); red days 09/04 + 10/09; 11/4 TP 3W/0L +$2,814.90 (N3 3W/4L −$29.90); 8/19–11/3 517 / 417W/100L / +$154,390.89; see 0.TradeProfiles.md *10.23 CURRENT — S0_maxnet on C0_maxws (11/6)*). Previous: 10.23 — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 + A99 + N3 applied 11/3 (Eric t48u): sha `0ef7a8044196360302c88b68b6fad97bf594bf4e8c0a02ea2f53eaf0a4774824` (backup before N3 `.bak1103_preN3`, sha `90f98504…`; N3 = 14 flat bounds (Breakout CALL 9, TL1 PUT 3, ORB1 CALL 1, TL1 CALL 1): ORB1 CALL vega max 0.132065 → 0.08457; TL1 CALL break_distance_pct max 0.02568068 → 0.02085629; TL1 PUT put_chain_vol_oi max 4.787199 → 4.614672; TL1 PUT vega max 0.078895 → 0.072145; TL1 PUT spy_change_pct min -1.388024 → -1.424236; Breakout CALL put_chain_volume min 2332 → 2114.5; Breakout CALL theta min -2.22082 → -2.737725; Breakout CALL chain_sanity_best_same_side_spread_pct min 1.302945 → 1.052661; Breakout CALL vega min 0.001785 → 0.003079; Breakout CALL price_volume_efficiency_3m max 1.114408 → 1.045; Breakout CALL gamma min 0.042655 → 0.0469; Breakout CALL spy_change_pct min -0.989267 → -1.264038; Breakout CALL distance_from_vwap_atr min 6.672701 → -0.4103778; Breakout CALL bollinger_width max 0.02673162 → 0.0145026; engine `32a80291` / `5e447ecd`; 8/19–11/3 TP 519 / 420W/99L / +$160,457.29 / −$6,407.15 / net +$154,050.14; 8/24 +$20.10 (A99 −$136.50); red days 10/09 only; 11/3 TP 4W/2L +$45.30; see 0.TradeProfiles.md *10.23 CURRENT — S0_maxnet on C0_maxws (11/6)*). Previous: 10.23 — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 + A99 applied 11/3 (Eric pt40u, near-miss A): sha `90f985044fcaf5fe8405cc20e8f8a13d2815403220bf8f1b1ad8805275d0b4d1` (backup before A99 `.bak1103_preA99`, sha `b0f1489c…`; A99 = 77 flat bounds in 9 cells (losses 112 → 99, red days 2): ORB1 CALL call_chain_volume min 2520 → 1269; ORB1 CALL put_chain_volume min 1570 → 557.5; ORB1 CALL total_chain_vol_oi min 0.1253104 → 0.02169471; ORB1 CALL call_chain_vol_oi min 0.1278631 → 0.0434753; ORB1 CALL put_chain_vol_oi min 0.0911737 → 0.01379803; ORB1 CALL vega min 0.00411 → 0.0097; ORB1 CALL price_volume_efficiency_3m max 10.413 → 4.891886; ORB1 CALL distance_from_vwap_atr min 0.652 → 0.3045482; ORB1 PUT total_chain_vol_oi min 0.1 → 0.03414181; ORB1 PUT put_chain_vol_oi min 0.1184144 → 0.009769591; ORB1 PUT put_chain_vol_oi max 1.516765 → 8.271201; ORB1 PUT put_chain_open_interest min none → 9051.5; ORB1 PUT call_chain_open_interest max none → 377956.5; ORB1 PUT put_share max none → 0.7083931; ORB1 PUT theta min -6 → -6.60429; ORB1 PUT atr min 0.21 → 0.1655925; ORB1 PUT atr max 2.937926 → 7.579642; ORB1 PUT chain_sanity_best_same_side_spread_pct min 0.5964268 → 0.7365538; ORB1 PUT vega max 0.08018 → 0.114025; ORB1 PUT break_distance_pct min 0.0008718601 → 0.0008213932; ORB1 PUT gamma min 0.025745 → 0.020295; ORB1 PUT gamma max none → 0.249995; ORB1 PUT otm_pct max 0.016901 → 0.0321; ORB1 PUT iv min 0.33487 → 0.1562; ORB1 PUT iv max 1.15951 → 1.191705; ORB1 PUT spy_change_pct min -0.35 → -0.9080443; ORB1 PUT distance_from_vwap_atr min -4.070635 → -7.775034; ORB1 PUT rsi min 21.47672 → 14.39831; ORB1 PUT bollinger_width max 0.06149404 → 0.06351685; ORB1 PUT option_l1_imbalance min none → -0.3026437; ORB1 PUT option_l1_imbalance max none → 0.9170393; ORB1 PUT delta min 0.275 → 0.2732; TL0 CALL underlying_l1_imbalance min -0.9936748 → -0.9667586; TL0 CALL option_l1_imbalance min -0.8881064 → -0.5575758; TL1 CALL call_share max 0.7635281 → 0.7599098; TL1 CALL chain_sanity_best_same_side_spread_pct min 2.527084 → 2.626135; TL1 PUT put_chain_vol_oi max 4.614672 → 4.787199; TL1 PUT vega max 0.072145 → 0.078895; TL1 PUT spy_change_pct min -1.424236 → -1.388024; TL2+ CALL call_chain_volume min 48043 → 14176.5; TL2+ CALL total_chain_vol_oi min 0.4333741 → 0.2973727; TL2+ CALL call_chain_vol_oi min 0.5141493 → 0.2932633; TL2+ CALL put_chain_open_interest max 111041 → 146155; TL2+ CALL call_chain_open_interest min 21675 → 20973; TL2+ CALL theta min -4.523229 → -1.822435; TL2+ CALL atr min 0.06970614 → 0.04931786; TL2+ CALL vega min 0.010825 → 0.00585; TL2+ CALL vega max 0.12 → 0.07359; TL2+ CALL price_volume_efficiency_3m max 1.661652 → 3.69365; TL2+ CALL iv max 1.03468 → 0.874285; TL2+ CALL gamma min 0.033839 → 0.073265; TL2+ PUT put_chain_open_interest min 3821.5 → 7013; TL2+ PUT put_chain_open_interest max 138851 → 166152; TL2+ PUT call_chain_open_interest min 6721.5 → 16978; TL2+ PUT call_chain_open_interest max 1015479 → 186854.5; TL2+ PUT vega min 0.00366 → 0.01708; TL2+ PUT price_volume_efficiency_3m min 0.005203563 → 0.06899994; TL2+ PUT break_distance_pct min 0.001145674 → 0.001034373; TL2+ PUT otm_pct max 0.01125 → 0.0116; TL2+ PUT spy_change_pct min -1.41814 → -1.656897; TL2+ PUT distance_from_vwap_atr max 36.43513 → 26.21959; TL2+ PUT rsi min 7.730137 → 5.245567; TL2+ PUT bollinger_width max 0.008683307 → 0.01270515; TL2+ PUT option_l1_imbalance min -0.4759494 → -0.966092; TL2+ PUT underlying_l1_imbalance min -0.9872809 → -0.9780213; Breakout CALL put_chain_volume min 2114.5 → 2332; Breakout CALL total_chain_vol_oi min 0.1107968 → 0.1753504; Breakout CALL theta min -2.737725 → -2.22082; Breakout CALL vega min 0.003079 → 0.001785; Breakout CALL price_volume_efficiency_3m min 0.01231184 → 0.03962743; Breakout CALL price_volume_efficiency_3m max 1.045 → 1.114408; Breakout CALL break_distance_pct max 0.0224 → 0.02162692; Breakout CALL spy_change_pct min -1.264038 → -0.989267; Breakout CALL distance_from_vwap_atr min 5.67 → 6.672701; Breakout CALL bollinger_width max 0.0145026 → 0.02673162; Breakout PUT vega max 0.05565 → 0.052565; Breakout PUT spy_change_pct max 1.55 → 0.6443855; engine `32a80291` / `5e447ecd`; 8/19–11/3 TP 523 / 424W/99L / +$160,582.49 / −$6,464.55 / net +$154,117.94; 11/3 TP 4W/2L +$45.30 (T1_E2 5W/2L +$134.70); see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 + A99 (11/3)*). Previous: 10.23 — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 applied 11/3 (Eric, T1 of pt30u): sha `b0f1489c4dc571be7e6da2c324ca5314914694ca9793f577878aada440215218` (backup before T1_E2 `.bak1103_preT1`, sha `5a8a0dd9…`; T1_E2 = 41 flat bounds in 7 cells under a loss-sum cap of −$6,468.16 on the 8/19–11/3 book: ORB1 CALL call_chain_volume max 600000 → 765960.8; ORB1 CALL put_chain_volume min 4667.5 → 1570; ORB1 CALL total_chain_vol_oi min 0.132 → 0.1253104; ORB1 CALL call_chain_vol_oi min 0.137 → 0.1278631; ORB1 CALL put_chain_vol_oi min 0.11 → 0.0911737; ORB1 CALL put_chain_open_interest max 161000 → 294479.6; ORB1 CALL call_chain_open_interest min none → 10247.5; ORB1 CALL call_share min 0.5240998 → 0.3954794; ORB1 CALL call_share max none → 0.8965421; ORB1 CALL theta min none → -4.37794; ORB1 CALL theta max -0.32563 → -0.419695; ORB1 CALL break_distance_pct min none → 0.0009726425; ORB1 CALL break_distance_pct max 0.0036 → 0.003697221; ORB1 CALL otm_pct max 0.01425 → 0.04605; ORB1 CALL iv min 0.185205 → 0.16901; ORB1 CALL spy_change_pct min -0.673203 → -0.6689402; ORB1 CALL spy_change_pct max 1.03 → 0.9936768; ORB1 CALL distance_from_vwap_atr max 3.173932 → 7.241872; ORB1 CALL bollinger_position max 130.31 → 108.5605; ORB1 CALL bollinger_width min 0.0036 → 0.002728069; ORB1 CALL underlying_l1_imbalance max 0.9932365 → 0.9542194; ORB1 CALL option_l1_imbalance min none → -0.8340909; TL0 CALL call_chain_vol_oi min 0.1399437 → 0.1503841; TL0 CALL underlying_l1_imbalance min -0.953126 → -0.9936748; TL0 CALL option_l1_imbalance min -0.5575758 → -0.8881064; TL0 PUT break_distance_pct min 0.000834 → 0.0008005019; TL1 CALL put_chain_volume min 2894.772 → 2880.5; TL1 CALL call_share max 0.7599098 → 0.7635281; TL1 CALL chain_sanity_best_same_side_spread_pct min 2.626135 → 2.527084; TL1 CALL bollinger_position min 72.47007 → 69.54777; TL2+ PUT put_chain_open_interest min 8561 → 3821.5; TL2+ PUT put_chain_open_interest max 166152 → 138851; TL2+ PUT call_chain_open_interest min 18175.5 → 6721.5; TL2+ PUT break_distance_pct min 0.001160999 → 0.001145674; TL2+ PUT otm_pct max 0.0121 → 0.01125; TL2+ PUT rsi max 67.39638 → 67.13241; TL2+ PUT bollinger_width max 0.007642969 → 0.008683307; TL2+ PUT underlying_l1_imbalance max 0.7203039 → 0.9900772; Breakout CALL distance_from_vwap_atr max 17.95124 → 18.02513; Breakout CALL bollinger_position min 69.07933 → 77.64544; Breakout PUT rsi max 58.1 → 54.84497; engine `32a80291` / `5e447ecd`; 8/19–11/3 TP 536 / 424W/112L / +$160,740.59 / −$6,466.70 / net +$154,273.89; 11/3 TP 5W/2L +$134.70 (S1_Q2cap 0W/3L −$289.00); see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 (11/3)*). Previous: 10.23 — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap applied 11/3 (Eric pt27u): sha `5a8a0dd9a330e9618ec914bc044351f31af0dab8e467076522856da71f66b92d` (backup before S1_Q2cap `.bak1103_preS1`, sha `f8c41a59…`; S1_Q2cap = 51 flat bounds in 7 cells under a loss-sum cap of −$6,468.27: TL0 CALL call_chain_volume max 213247 → 366749; TL0 CALL put_chain_volume max 84544.5 → 184720.5; TL0 CALL total_chain_vol_oi max 2.901716 → 10.71361; TL0 CALL call_chain_vol_oi max 2.818396 → 9.320936; TL0 CALL call_chain_open_interest max 760000 → 127038; TL0 CALL option_l1_imbalance min -0.8219114 → -0.5575758; TL0 PUT underlying_l1_imbalance min -0.9954441 → -0.945827; TL0 PUT delta max 0.622 → 0.61299; TL1 PUT put_chain_vol_oi max 5.346311 → 4.614672; TL1 PUT vega max 0.077075 → 0.072145; TL1 PUT spy_change_pct min -1.388024 → -1.424236; TL2+ CALL price_volume_efficiency_3m max 1.399989 → 1.661652; TL2+ PUT put_chain_open_interest min 9241.5 → 8561; TL2+ PUT put_chain_open_interest max 146194 → 166152; TL2+ PUT call_chain_open_interest min 19467 → 18175.5; TL2+ PUT put_share max 0.4963154 → 0.6788929; TL2+ PUT chain_sanity_best_same_side_spread_pct max 12.79638 → 11.28192; TL2+ PUT price_volume_efficiency_3m max 4.931183 → 4.913005; TL2+ PUT break_distance_pct max 0.006658903 → 0.01501422; TL2+ PUT otm_pct max 0.0283 → 0.0121; TL2+ PUT iv max 1.00753 → 1.01524; TL2+ PUT distance_from_vwap_atr min -3.752237 → -7.107289; TL2+ PUT rsi min 17.24718 → 7.730137; TL2+ PUT rsi max 83.1 → 67.39638; TL2+ PUT bollinger_width max 0.006085394 → 0.007642969; TL2+ PUT underlying_l1_imbalance min -0.985 → -0.9872809; TL2+ PUT underlying_l1_imbalance max 0.9739988 → 0.7203039; TL2+ PUT delta min none → 0.16918; Breakout CALL call_chain_volume min 5749.5 → 5179; Breakout CALL call_chain_volume max 3310000 → 1675510; Breakout CALL put_chain_volume min 3100 → 2114.5; Breakout CALL put_chain_vol_oi min 0.0574 → 0.1283437; Breakout CALL call_share min 0.501 → 0.4941137; Breakout CALL theta min -4.265864 → -2.737725; Breakout CALL chain_sanity_best_same_side_spread_pct min 1.23 → 1.302945; Breakout CALL atr min 0.01108471 → 0.02513929; Breakout CALL vega max 0.07564 → 0.10413; Breakout CALL price_volume_efficiency_3m min 0.03962743 → 0.01231184; Breakout CALL break_distance_pct min 0.0008851212 → 0.0008822278; Breakout CALL otm_pct min -1.86e-05 → -0.003308817; Breakout CALL gamma min 0.065235 → 0.042655; Breakout CALL gamma max 0.857 → 1.097055; Breakout CALL spy_change_pct min -0.8727513 → -1.264038; Breakout CALL distance_from_vwap_atr max 18.5 → 17.95124; Breakout CALL rsi max 97.28 → 101; Breakout CALL bollinger_width max 0.0267 → 0.0145026; Breakout PUT put_chain_volume max 2308541 → 3414191; Breakout PUT put_chain_open_interest max 307166 → 313902.5; Breakout PUT vega max 0.051 → 0.05565; Breakout PUT bollinger_width min 0.001137408 → 0.001824229; Breakout PUT option_l1_imbalance min -0.537 → -0.1608118; engine `32a80291` / `5e447ecd`; 8/19–11/2 TP 507 / 406W/101L / +$160,454.19 / −$6,468.16 / net +$153,986.03; 11/2 TP 7W/3L +$2,768.10; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap (11/3)*). Previous: 10.23 — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 applied 11/3 (Eric pt21u): sha `f8c41a596daa3f5484731c89fec25dba177904f553d325c3e75a15fc75e202d8` (backup before P4_rel9 `.bak1103_preP4`, sha `0f296670…`; P4_rel9 = 24 flat bounds in 3 cells: ORB1 CALL call_share min 0.5630 → 0.5241, iv min 0.21783 → 0.18521, gamma max 0.36652 → 0.22620, bollinger_width max 0.0465 → 0.02974; TL2+ PUT 14 bounds (call_chain_volume min 2430 → 12133, put_chain_volume min 1998 → 6024.5, put_chain_open_interest max none → 146194, call_chain_open_interest min 1650 → 19467, put_share min 0.2572 → 0.2055, theta max −0.182 → −0.4731, spread_pct max 26.9 → 12.796, break_distance_pct max 0.004907 → 0.006659, otm_pct max 0.0116 → 0.0283, iv max 0.69557 → 1.00753, distance_from_vwap_atr min −1.6316 → −3.7522, bollinger_width max 0.0158 → 0.006085, option_l1_imbalance max 0.75288 → 0.71620, underlying_l1_imbalance max 0.73354 → 0.97400); Breakout PUT put_chain_volume min 16340 → 17913, price_volume_efficiency_3m min 0.059 → 0.001138, iv min 0.185365 → 0.191135, spy_change_pct min −1.5828 → −2.0166, bollinger_position max 50.649 → 71.990, bollinger_width min 0.0013201 → 0.0011374; vs A_g7 on the same engine +10 winners +$1,675.00, −10 winners −$731.60, 3 losers cut +$153.70, 5 losers added −$382.34, net +$714.76; off the L ≤ 99 target (101 losses); low-fill bollinger_width / bollinger_position / option_l1_imbalance; engine path_v2_exit `32a80291` / Shared.env `5e447ecd`; 8/19–11/2 TP 502 / 401W/101L / +$159,375.74 / −$6,890.45 / net +$152,485.29; 11/2 TP 7W/3L +$2,251.20, missed 53 +$5,024.90; Total 7,135 / −$318,572.31; see 0.TradeProfiles.md *10.23 CURRENT — S0_maxnet on C0_maxws (11/6)*).** Previous: A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 applied 11/3 (Eric pt15u): sha `0f296670434c523ee7f3b3e7fbb391fd8b1c827d1812340ee88deb7bde754dcd` (backup before A_g7 `.bak1103_preA`, sha `d4349452…` = SV_lean1 bounds `ff0255c7…` + Eric's 11/3 engine-note edits; A_g7 = 5 flat bounds in 2 cells (3 loosened, 2 tightened): TL0 CALL `bollinger_position` max 85.7199 → 88.3955; TL1 PUT `put_chain_vol_oi` max 4.63 → 5.3463, `chain_sanity_best_same_side_spread_pct` min 1.4085 → 1.6130, `vega` max 0.068525 → 0.077075, `spy_change_pct` min −1.4242 → −1.3880; vs SV_lean1 on the newest engine +4 winners +$477.50, −3 winners −$159.70, 2 losers cut +$68.70, 2 losers added −$223.40, net +$163.10; low-fill TL0 CALL bollinger_position; engine path_v2_exit `32a80291` / Shared.env `5e447ecd` (20% Save stall + UL-retrace on); 8/19–11/2 TP 500 / 401W/99L / +$158,432.34 / −$6,661.81 / net +$151,770.53; 11/2 TP 7W/3L +$2,251.20, missed 53 +$5,024.90; Total 7,135 / −$318,572.31; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 (11/3)*). Previous: A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 applied 11/3 (Eric pt13u): sha `ff0255c777cabc5cfab864a9c0533e33bb525d02bfbef1675dafc54ad9221b93` (backup before SV_lean1 `.bak1103_preSVlean1`; SV_lean1 = 47 flat bounds in 8 cells (24 loosened, 23 tightened) mined for the new 20% Save exit winners; 15 hairlines; low-fill ORB1 PUT / TL2+ CALL bollinger_position, TL0 CALL bollinger_width; vs R2_A on the new engine +20 winners +$1,903.20, −13 winners −$666.20, 8 losers cut +$328.66, 8 losers added −$370.10, net +$1,195.56; engine path_v2_exit `eb812a6f` / Shared.env `b0065e9d`; 8/19–11/2 TP 499 / 400W/99L / +$158,234.54 / −$6,507.11 / net +$151,727.43; 11/2 TP 7W/3L +$2,251.20, missed 53 +$5,024.90; Total 7,135 / −$321,956.31 (on the newest engine 499 / 400W/99L / +$151,607.43); see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 (11/3)*). Previous: A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A applied 11/2 (Eric pt8u): sha `495b3bccbc2abb163f8f668d0a67dfcde33e35d84c86ef6bc3a49d1f6e9e5f92` (backup before R2_A `.bak1102_preR2A`; R2_A = 7 flat TL0 CALL / TL0 PUT bounds (round-2 package A; hairlines TL0 CALL put_chain_volume max 0.53%, TL0 PUT gamma max 0.67%; low-fill TL0 PUT bollinger_position 31%); vs F105 +4 winners +$913.90, −3 winners −$370.60, 2 losers cut +$72.72, 1 loser added −$144.20, net +$471.82; 8/19–11/2 TP 492 / 392W/100L / +$156,978.14 / −$6,468.27 / net +$150,509.87; 11/2 TP 6W/4L +$2,171.70, missed 54 +$5,035.10; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A (11/2)*).** Previous: A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 applied 11/2 (Eric): sha `29805ada9fff34df5e9e78bf9831b662e579c6d7a6d86e31970032d3d9acefca` (backup before F105 `.bak1102_preF105`; F105 = 7 flat bounds (package F_ALL_105p, narrow bounds widened; 5 loosened, 2 tightened in TL0 CALL / TL0 PUT / TL1 PUT / TL2+ PUT / Breakout CALL); vs X_ALL +3 winners +$371.60, −2 winners −$378.50, 4 losers cut +$186.99, 0 losers added, net +$180.09; 8/19–11/2 TP 492 / 391W/101L / +$156,434.84 / −$6,396.79 / net +$150,038.05; 11/2 TP 5W/4L +$1,591.20, missed 55 +$5,615.60; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 (11/2)*).** Previous: A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL applied 10/30 (Eric pt129u): sha `2c0f057802523eaf8b9ff398d83c37d5625e1f30a73d23aaa7fa653422fa42a1` (backup before X_ALL `.bak1030_preXALL`; X_ALL = 11 flat TL1 PUT / TL2+ PUT bounds (5 loosened, 5 tightened, 1 new option_l1_imbalance max); vs W2 +4 winners +$721.30, −5 winners −$409.80, 5 losers cut +$257.50, 6 losers added −$236.80; TL1 PUT put_share min 0.026% hairline; medium-fill chain_sanity/vega/option_l1_imbalance; 8/19–10/30 TP 488 / 387W/101L / +$155,092.54 / −$6,456.28 / +$148,636.26; 10/30 TP 12W/0L +$3,595.60; Total 6,954 / −$321,838.81 unchanged; see 0.TradeProfiles.md *10.23 CURRENT — S0_maxnet on C0_maxws (11/6)*). Previous: A + P1 + N2 + Lean + L2 + H1 + W2 applied 10/30 (Eric pt125u): sha `8ed303954dd9c82b2d0d0ce230fb6e0a3705643ea54dc136a23e5a95f2bd204a` (backup before W2 `.bak1030_preW2`; W2 = 18 loosened flat bounds (ORB1 PUT, TL1 PUT, TL2+ CALL, TL2+ PUT, Breakout CALL) keeping all 375 H1 winners, adding 13 winners +$1,118.32 and no losers; uses low/medium-fill bollinger_width/bollinger_position/option_l1_imbalance; 4 hairlines; TL1 PUT rsi min and bollinger_position min filter nothing; 8/19–10/30 TP 488 / 388W/100L / +$154,781.04 / −$6,476.98 / +$148,304.06; 10/30 TP 11W/0L +$3,560.60; Total 6,954 / −$321,838.81 unchanged; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 (10/30)*). Previous: A + P1 + N2 + Lean + L2 + H1 applied 10/30 (Eric pt121u): sha `cf4f671b69ad0b434248645dafe9348b84dc525c3a309bb88f63f1af91452d34` (backup before H1 `.bak1030_preH1`; H1 = 13 flat TL1 PUT / TL2+ PUT bounds admitting >$300 missed winners SPY-774p +$1,005.60 (8/28) and CRWV-54p +$579.60 (10/30); drops 15 small current winners −$933.90; TL1 PUT `price_volume_efficiency_3m` min 0.2718066 hairline 0.086% (cannot widen at $0); 8/19–10/30 TP 475 / 375W/100L / +$153,662.72 / −$6,476.98 / +$147,185.74; 10/30 TP 9W/0L +$3,155.00 (pre 1W/2L −$503.60); Total 6,954 / −$321,838.81 unchanged; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 (10/30)*). Previous: A + P1 + N2 + Lean + L2 applied 10/30 (Eric pt118u): sha `787353dffe67d8b1d0b32eddc3bf08953aacdbbb0ae22dfe5ba22eda76c7c940` (backup before L2 `.bak1030_preL2`; L2 = 3 Breakout PUT bounds: `spy_change_pct` min -1.51 → -1.582809, `bollinger_position` min -6.715772 → -40.37602, `otm_pct` max 0.01375 → 0.008616584 (L2 hairlines widened one step at $0 cost); admits 10/30 NVDA-205p +$1,247.00 and 10/16 IBIT-46p +$78.20, rejects 10/22 PLTR-195p +$86.10; 8/19–10/30 PC V2 `569a516c`/`37bd9d1f` TP 478 / 382W/96L / +$152,188.12 / −$6,341.19 / +$145,846.93; 10/30 TP 7W/0L +$2,406.90 (pre 1W/2L −$503.60); Total 6,954 / 1,567W/5,387L / −$321,838.81; caveats: `bollinger_position` low-fill before 10/26 (missing = PASS), gain is a 10/30 fit; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 (10/30)*). Previous: A + P1 + N2 + Lean applied 10/30 (Eric pt114u): sha `f71eca7df24861704c6419bbf404580f69925a30597d50705fa11086fc1870f8` (backup before Lean `.bak1030_preLean`; Lean = 7 flat bounds: TL1 PUT `spy_change_pct` min -1.387 → -1.225369, TL1 CALL `call_share` min 0.3906474 → 0.3221748, ORB1 PUT `iv` max 1.012991 → 1.15951, TL2+ PUT `spy_change_pct` min -0.648 → -1.970469, TL0 PUT `spy_change_pct` min -1.006 → -1.473627, TL0 PUT `bollinger_width` max 0.05 → 0.0767516, TL0 PUT `underlying_l1_imbalance` min -0.9852 → -0.9954441; 8/19–10/30 PC V2 `569a516c`/`37bd9d1f` TP 477 / 381W/96L / +$150,949.02 / −$6,341.19 / +$144,607.83; 10/30 TP 6W/0L +$1,159.90 (pre 1W/2L −$503.60); Total 6,954 / 1,567W/5,387L / −$321,838.81; holdout caveat: mostly a 10/30 fit; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean (10/30)*). Previous: A + P1 + N2 applied 10/29 (Eric t111u): sha `52468772354cbb65594311570a0a23040e1f3e9fc4a889e50f3be47350622558` (N2 bounds; audit metadata-only fix 10/29 t112u of the first N2 file `139eea4e…`, backup `.bak1029_preAudit`; backup before N2 `.bak1029_preN2`; N2 = ORB1 PUT `price_volume_efficiency_3m` min 0.11 → 0.0992175, TL2+ CALL `put_chain_open_interest` max 139469 → 109098.5, TL2+ CALL `price_volume_efficiency_3m` max 1.4 → 1.6656965; 8/19–10/29 TP 472 / 375W/97L / +$149,924.82 / −$6,362.39 / +$143,562.43; 10/29 TP 3W/0L +$318.30; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 (10/29)*). Previous: A + P1 applied 10/29 (Eric t109u): sha `0fb839eae45389e3efaa24033bedc6e8024bca9ea85f393c75507393e2c5b88e` (backup `.bak1029_preP1`; P1 = TL0 PUT `bollinger_width` max 0.094826626 → 0.05, a backup for missing `dir_pct_change_5m` (+$41.50 in the dir%-missing scenario, cuts CRWV-60p-305); admit set identical to A: 8/19–10/29 TP 471 / 374W/97L / +$143,469.09; 10/29 TP 3W/0L +$318.30; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 (10/29)*). Previous: A applied 10/29 (Eric t107u): sha `e0b1e4b3f2c27adffd5ddcdc251249ec3b4e5ed3433918f903fce4fbddbc54f0` (backup `.bak1029_preA`; 24 flat bounds in TL2+ CALL / Breakout CALL / ORB1 PUT; 8/19–10/29 PC V2 `569a516c`/`37bd9d1f` TP 471 / 374W/97L / +$149,856.92 / −$6,387.83 / +$143,469.09, missed 1,108 +$86,821.28; Total 6,685 / 1,482W/5,203L / −$305,932.91; 10/29 TP 3 / 3W/0L / +$318.30; `dir_pct_change_5m` fills ~55% (Eric t107u): remaining uses are legacy, missing = PASS, replacements to be found; see 0.TradeProfiles.md *10.23 PREVIOUS — A (10/29)*). Previous: Max applied 10/29 (Eric t104u): sha `cec10e5d2c70e34854ff7adbc35013a372a5112bddabd362bef13aefe9d2eb2f` (backup `.bak1029_preMax`; 5 flat bounds in TL0 PUT / TL1 CALL / TL2+ PUT; 8/19–10/29 PC V2 `569a516c`/`37bd9d1f` TP 475 / 377W/98L / +$149,692.02 / −$6,434.43 / +$143,257.59, missed 1,105 +$86,986.18; Total 6,685 / 1,482W/5,203L / −$305,932.91; 10/29 TP 2 / 2W/0L / +$280.20, Total 211 / 52W/159L / −$14,475.10; see 0.TradeProfiles.md *10.23 PREVIOUS — Max (10/29)*). Previous: Option 1 + ORB1 CALL Q2 applied 10/28 (Eric t93u): sha `82e8f1d9c637125aa5404859d3c4c39c6d28bd716ab7d811ca5cada6a0762643` (backup `.bak1028_preORB1Q2`; 7 epsilon-centered flat bounds in ORB1 CALL; 8/19–10/28 PC V2 `569a516c`/`ea15c978` TP 476 / 378W/98L / +$149,488.92 / −$6,434.43 / +$143,054.49, missed 1,052 +$83,613.78; ORB1 CALL 39 / 34W/5L / +$16,992.20; Total 6,474 / 1,430W/5,044L / −$291,457.81; 10/28 TP 9 / 5W/4L / −$65.90; see 0.TradeProfiles.md *10.23 PREVIOUS — Option 1 + ORB1 CALL Q2 (10/28)*). Previous: Option 1 applied 10/28 (Eric t83u): sha `8ddf23b0758e4b079124dc51362d022d7c35e2e2b8ee7cf720f0e403bf51a57b` (backup `.bak1028_preO1`; 8 flat min/max bounds in TL0 CALL / TL0 PUT / TL2+ CALL / Breakout CALL / Breakout PUT; 8/19–10/28 PC V2 `569a516c`/`ea15c978` TP 479 / 379W/100L / +$149,310.32 / −$6,505.63 / +$142,804.69, missed 1,051 +$83,792.38; Total 6,474 / 1,430W/5,044L / −$291,457.81; 10/28 TP 9 / 5W/4L / −$65.90, missed +$2,094.10; Total 10/28 212 / 41W/171L / −$15,457.00; see 0.TradeProfiles.md *10.23 PREVIOUS — Option 1 (10/28)*). Before Option 1: Option B applied 10/27 (Eric t77u): sha `f024dfe9e38b7732bd8684d5c7d1eec1d8c97aa175e30deb4b37970ae1751062` (8/19–10/28 479 / 378W/101L / +$142,615.29; backup `.bak1027_preB`; 12 flat min/max bounds; 8/19–10/27 PC V2 `569a516c`/`ea15c978` TP 470 / 375W/95L / +$142,853.39, missed 1,014 +$81,609.58; Total 6,262 / 1,389W/4,873L / −$276,000.81; 10/27 TP 3 / 2W/1L / −$50.70, missed +$1,421.00). Before B: P3 applied 10/26 (Eric t67u): sha `a4dd0222e6f8dd5ea83b45f77eb38eaa6ac319bb99f86c0b6f95e3aed0b3674a` (backup `.bak1023_preP3` of `e0d7bf3b…`); 6 bounds: ORB1 CALL `bollinger_width` min 0.0063791474409935 → 0.0036 and max 0.0718525158139732 → 0.0465; TL2+ PUT `chain_sanity_best_same_side_spread_pct` min 2.04 → 1.047, `break_distance_pct` min 0.000965 → 0.001117; Breakout CALL `rsi` max 97.0229509359499 → 97.28, `bollinger_width` max 0.0173 → 0.02397. Replay 8/19–10/26 (6,033 rows, replay-basis P&L, not live): TP 10.23 **468 / 373W/95L / +$148,904.52 / −$6,104.33 / +$142,800.19 STEP 9 (ex-7A) (+$141,008.19 base)**; Total Results 1,351W/4,682L / −$260,494.91 STEP 9 (ex-7A) (−$264,798.91 base); 10/26 TP 6 trades 5W/1L +$1,367.30, 59 missed wins +$7,084.20; Total 10/26 64W/175L −$8,943.90. MU-905c-353 replay +$1,427.10 (live +$22.05) kept per Eric pending Profit Capture review. Future cuts never touch `dir_pct_change_5m`. Local only; deploy stays `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`. Earlier stage (superseded):** 10.23 (FINAL, reconciled on the final 10/23 session t54u; sha `e0d7bf3be1ad17ad3da4251afb9c6ffa817f823cf56547511f9dd184304801ae`, metadata-only change from the flattened `aef9dfbb…`; final replay 8/19–10/23 **462 / 368W/94L / +$141,521.99 STEP 9 (ex-7A) (+$139,729.99 base)**, 10/23 16W/0L +$2,990.00, 8/19–10/22 446 / 352W/94L / +$138,531.99; Total Results 5,794 rows −$251,551.01 STEP 9 (ex-7A); backup `.bak1023_reconcile`; flatten-stage note: `flat-27-key-cells-v2`: cells only key/min/max, `not_and`/`missing_reject_keys` in top-level `cell_policies`, Breakout PUT iv strict <0.66 → inclusive 0.6599999999999999; replay 462 / 368W/94L / +$141,654.99, 0 decision diffs vs `fea583ea…`; backup `.bak1023_preflat`; day reviews 8/28, 10/22, 10/09, 10/14, 10/21 kept, 8/20 A_cons applied; future cuts avoid `dir_pct_change_5m`; local only, Demo deploy stays `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`):** `easy0DTE/configs/trade_profile_10.23.json` — `TRADE_PROFILE_VERSION=10.23` (aliases `10.23flat` / `tp1023`) selects sha256 `e0d7bf3b…` (final, reconciled t54u; flattened cells stage `aef9dfbb…` 10/23 t52u; pre-flatten `fea583ea…`; package H + 8/20 A_cons applied 10/23, on top of P1, with refreshed JSON metadata text; the A_cons-stage file before the text refresh was `1c0aa3b1…`); it started as an exact 27-key flat clone of 10.22 and the baseline below describes the original 10.22 rules: the verified 8/19–10/22 admission parity baseline **370 / 308W/62L / +$134,774.63**. The separate 10/22 Profit Capture base/STEP 9 (ex-7A) exit variants are **+$134,552.63 / +$136,118.63** and are not encoded as profile rules. **P1 applied 10/23 (Eric, pt36u):** three single-key max changes — TL1 PUT `bollinger_width` max 0.0227527219332 → 0.021206; ORB1 CALL `rsi` max 88.97122553280025 → 90.2; TL0 PUT `bollinger_position` max 55.4915556441078 → 82.0 (P1-stage sha256 `10b046e8…`, superseded by package H; backup `trade_profile_10.23.json.bak1023_p1`, sha `205b1602…`). Replay with the 10/23 interim collection (pulled 10:29 PT; 3 open rows excluded): **376 trades, 309W/67L, +$135,526.93 under Profit Capture STEP 9 (ex-7A) (+$133,960.93 base engine)**; 8/19–10/22 portion unchanged (370, 307W/63L, base +$134,552.63 / STEP 9 (ex-7A) +$136,118.63); 10/23 alone **6 trades, 2W/4L, −$591.70** (adds INTC-85p-263 +$107.80 and AVGO-327.5c-945 +$102.90, rejects IREN-25p-928 −$150.50; still enters NBIS, GLD, IBIT, HOOD losers). The 10/23 numbers are interim and may change when the session is final. The flat 8/19–10/22 admission parity baseline above describes the pre-P1 rules and is unchanged by P1. **Package H applied 10/23 (Eric, pt39u):** `trade_profile_10.23.json` now = recut package H (`pkgH_d23max`), sha256 `60f251a21980212d69a2dc331c1b9408978979d9f888fae692e240958a2ea86a`. It is the P1 file with 155 key-bound changes (min/max/doc only; 10 cells x 27 entries, same key order, `not_and` untouched; some one-sided keys gained a second bound, 11 `rule: PASS` entries became min/max bounds (ORB1 PUT is unchanged; 9 cells changed), and 5 derived `call_share`/`put_share` entries had min/max changed or added). Backup of the P1 file: `trade_profile_10.23.json.bak1023_P1_before_H` (sha256 `10b046e8…`). Real-gate replay of the on-disk file (loads under `TRADE_PROFILE_VERSION=10.23` and `tp1023`; 10/23 interim collection pulled 10:29 PT, 3 open rows excluded): **8/19–10/23 under Profit Capture STEP 9 (ex-7A): 475 trades, 373W/102L, wins +$147,961.92, losses −$6,537.13, net +$141,424.79** (base engine 475, 373W/102L, +$139,632.79); 8/19–10/22 portion 457 trades, 355W/102L, +$138,066.09 (STEP 9 (ex-7A)); **10/23 alone 18 trades, 18W/0L, +$3,358.70** (no 10/23 loser enters; all five original 10/23 losers are rejected). **Caveats:** the 10/23 data is interim; H is a deep fit (155 keys) and overfit on a holdout test (refit on data before 10/09, scored 10/09–10/23: deep fits scored about +$940 to −$608, versus about +$13,600 for the leaner 52-key Q-style fit); it gives up historical winners to enter more 10/23 wins. Nothing was committed or deployed. **8/20 A_cons applied 10/23 (Eric, pt43u):** `trade_profile_10.23.json` now = package H + 8/20 candidate A_cons, sha256 `1c0aa3b1c5ea7343edd7538444bc2ae65e760be721cd49b6975612c62812e0f3` at that stage (supersedes H sha `60f251a2…`; sha after the metadata-text refresh `fea583ea…`, then flattened to `aef9dfbb…`). Four Breakout CALL min/max changes only (min/max/doc; 10 cells x 27 entries unchanged): `put_chain_vol_oi` min 0.132 → 0.0574; `call_share` min 0.3016 → 0.501; `chain_sanity_best_same_side_spread_pct` min 0.597 → 1.23; `price_volume_efficiency_3m` max 1.807 → 1.045. Real-gate replay of the on-disk file (10/23 interim collection, 3 open rows excluded): **8/19–10/23 under Profit Capture STEP 9 (ex-7A): 462 trades, 368W/94L, wins +$147,721.52, losses −$6,066.53, net +$141,654.99** (base engine 462, 368W/94L, +$139,862.99), versus H 475, 373W/102L, +$141,424.79 (+$230.20 net, 8 fewer losers); 10/23 alone 16 trades, 16W/0L, +$3,123.00 (H had 18, +$3,358.70); 8/20 15 trades, 10W/5L, +$1,449.40 (unchanged); 10/09 7 trades, 1W/6L, −$762.70 (unchanged) and 9/04 8 trades, 3W/5L, −$201.10 (H was −$219.50, +$18.40). Loads under `TRADE_PROFILE_VERSION=10.23` / `10.23flat` / `tp1023`. Backup of the H file: `trade_profile_10.23.json.bak1023_H_before_A` (sha256 `60f251a2…`). Local only: nothing committed, pushed or deployed. (10/23 was interim then; reconciled final t54u.) **Consolidated 10.23 status (doc sync 10/23):** history 10.22 baseline (`e8760b2c…`, 370 / 308W/62L / +$134,774.63) → 10.23 flat copy (`205b1602…`, same result) → P1 (`10b046e8…`) → package H (`60f251a2…`, 155 min/max changes by cell: ORB1 CALL 19, TL0 CALL 24, TL0 PUT 23, TL1 CALL 9, TL1 PUT 19, TL2+ CALL 4, TL2+ PUT 22, Breakout CALL 21, Breakout PUT 14, ORB1 PUT 0) → 8/20 A_cons (`1c0aa3b1…`: 462 / 368W/94L / +$141,654.99 STEP 9 (ex-7A), base engine +$139,862.99) → JSON metadata-text refresh (`fea583ea5efa09bc117ab667ebabd10858f8aaf243481ed5d32b1ecf836e8ef7`) → flatten (`aef9dfbb804124c7561802a0ba2bc842f47799a228f4857cab542ca5697e3460`, same replay; later superseded — see the current file above). Backups beside the JSON: `.bak1023_p1`, `.bak1023_P1_before_H`, `.bak1023_H_before_A`, `.bak1023_pre_meta`. Flat-JSON rule: 10 cells × one ordered 27-key list, each key only min, max or min-to-max; known inherited deviations (22 `rule: PASS` strings, 11 derived share entries the loader requires, 13 `not_and` lines, 4 missing-reject keys) and the audit's findings (all fixed 10/23: `evaluate_trade_profile` line 910 now lists 10.23 so the decision version is `10.23`; JSON metadata text refreshed; `tests/test_trade_profile_10_23.py` added; the 22 `rule: PASS` strings stay because the loader requires them) are listed in [0.TradeProfiles.md](0.TradeProfiles.md) → *10.23 consolidated status*. Local only: nothing committed, pushed or deployed; a later deploy stays Demo with `SINGLE_TP_ADMIT=true` and `TRADE_PROFILE_VERSION=wide`. **10.22 source (baseline / parent of 10.23):** `trade_profile_10.22.json`, sha `e8760b2c…`; historical comparison baselines are 10.21 (`trade_profile_10.21.json`, sha `e15fd158…`) and 10.20 (`trade_profile_10.20.json`, sha `e3a59f89…`). `wide` remains the separate TP Wide profile; `TRADE_PROFILE_VERSION=10.2nh` rolls back. Deployed Research Demo admit remains `wide_collect`; PUT delta is read as \|delta\|. |

**10.16 verified replay:** the saved repaired Profit Capture addback artifact is **234 trades, 188W/46L**, wins `+$114,393.24`, losses `-$4,179.53`, net **+$110,213.71**. It includes the three approved existing-key missed-win packages and the TL2+ PUT `iv <= 0.695565` secondary recut. The lower fresh source-backed engine replay remains a diagnostic comparison because it uses a different exit-replay contract.

**Replay baseline:** the preferred t198u 10.5 configuration includes the TL0 PUT, Breakout CALL, and TL1 CALL epsilon-safe bounds documented in `0.TradeProfiles.md`. Its complete 8/19–10/15 replay is 241 trades, 183W/58L, +$98,919.02. Any 8/19–10/16 confirmation must append 10/16 to this baseline rather than use the older 231-trade preserved artifact.
| **Quality stack** | **Live (`00625-wwv`): single Trade Profile TP Wide.** 10/9–10/13 history: `wide_collect` with 10.5 observe-only. That branch is not selected. Local source removed it. |
| **Skip Signals** | Call SS and Put SS are **hard**, outside the Trade Profile. Path SS and `entry_part` stay **off**. The 110k / 11k entry and C2 / R-PUT tables are the 2026-09-27 record. |
| **MFE trail** | `OPTION_SINGLE_MFE_TRAIL_ENABLED=false` (12:30 −10pp still required) |
| **PC-PX2** | **On** · stall-bank leftover ≤ **$10** · 20pp / lock@200% · early premium trail (`PX2_EARLY_PREM_TRAIL_*`) · BE is a **decision** at 0% (fill = live bid) · expected **+$12,545 (56%)** plus Aug 17–26 early trail **+7945.9pp / 1 accepted worse** — [0DTEProfitCapture.md §3.5](0DTEProfitCapture.md#35-pc-px2-after-arm--modulespx2_exitpy) · session $ in [HISTORY](PCLegacy/0DTEProfitCapture.HISTORY.md) |
| **PX3** | **On** (`PX3_ENABLED=true`) · `premium_trail` handoff + qualified `mfe_take_profit` confirmation · bank-tick HWM · exceptional +100% winner handoffs ON (`PX3_EXCEPTIONAL_*`, `PX3_THIN_LEAD_EXCEPTIONAL_*`, `PX3_HIGH_HWM_EXCEPTIONAL_*`, `PX3_MFE_CONFIRM_*`) · `trend_runner` **keep_px2** if already through PX2 20pp (`px3_through_px2_giveback_floor`) · real restart sets `px3_skip_floor_once` — [0DTEProfitCapture.md §3.6](0DTEProfitCapture.md#36-px3-protected-runner--modulespx3_exitpy) |
| **Moon hold (H2)** | **Removed Aug 17** · +60% MFE → 0.50% `price_trail` · Telegram `Moon: ⭐️` on exit **> +200%** |
| **TL soft window** | `TRENDLINE_ENTRY_WINDOW_SEC=300` |
| **TL activate** | **0.08%** (`TRENDLINE_MIN_BREAK_DISTANCE_PCT=0.0008`) · detect=emit; continuation **deleted** |
| **TL activate age** | `TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC=10800` — **TL1–TL4** ARMED first-break **3h** after draw/inject; **TL0** expires at **07:25** (`tl0_geometry_cutoff_0710`) |
| **TL GATE** | Deployed Research Demo uses wide-collect gates; Trade Profile 10.5 observes/stamps only until TP admit is re-enabled. Call SS and Put SS are hard in the wide-collect stack per the 10/9 deploy notes at the top of this file. |
| **Liquidity Guard** | **v1.7** settings (local) · `LIVE_MAX_CONTRACTS_PER_TRADE=0` · vol **off** · OI **off** · ask depth **200%** · bid qty cap **off** · ADV **2.0%** · generic floor **off** · min bid/ask **1** · refresh ≤**5s** · `LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true` — [LiquidityGuard.md](LiquidityGuard.md) |
| **GCS** | `research_app/*` |

### Documentation map

| Guide | Scope |
|-------|--------|
| **This file** | Config ownership + operator reference |
| [configs/README.md](../configs/README.md) | Which file owns which concern |
| [0DTEORB1Breakout.md](0DTEORB1Breakout.md) | ORB SO + ORB1 Breakout |
| [0DTETrendline.md](0DTETrendline.md) | Trendline 0DTE |
| [0DTEORB2Breakout.md](0DTEORB2Breakout.md) | Breakout 0DTE |
| [0DTERisk.md](0DTERisk.md) | Options PP · Red Day · Position Sizing · **path consume** (ORB1 one-shot · ORB2 1h+re-entry · TL one-attempt) — Liquidity Guard numbers live in [LiquidityGuard.md](LiquidityGuard.md) |
| [LiquidityGuard.md](LiquidityGuard.md) | **v1.7 / Sep 1** size governors (vol off · OI off · ask 200% · bid MQ-only · min bid/ask 1 · ADV 2.0% · generic floor off) |
| [PositionMonitoring.md](PositionMonitoring.md) | Open-position monitor · exit spine · **post-exit cooldown** |
| [0DTEQualityGates.md](0DTEQualityGates.md) | Suggested QG 31 (research stamps) · live `GATE_*` stays **wide collect** until cert **30 / 30** |
| [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) | Absolute CALL / PUT + strategy/side Skip Signals (live `GATE_call_ss` / `GATE_put_ss` / `GATE_path_ss`) |
| [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) | Per-path/side 0-MFE mine board — live copies are `GATE_path_ss` |
| [SORisk.md](SORisk.md) | ORB SO ETF capital · packing · Entry Bar · equity stealth |
| [0DTEProfitCapture.md](0DTEProfitCapture.md) | Stealth exits / economics |
| [Alerts.md](Alerts.md) | Telegram |
| [Cloud.md](Cloud.md) | Deploy / Cloud Run |
| [LiveModeReadiness.md](LiveModeReadiness.md) | RESEARCH Live ship path · FINAL checklists |
| [Brokers.md](Brokers.md) | Brokers |

**Public document.** Account IDs, tokens, and chat IDs → [PrivateSecrets.md](PrivateSecrets.md) or `secretsprivate/`. Project IDs, service URLs, and revisions → [CloudSecrets.md](CloudSecrets.md). OAuth token policy → [OAuthSecrets.md](OAuthSecrets.md).

---

## Production Defaults Snapshot

Operator baseline — RESEARCH Demo Cloud Run (`easy-etrade-strategy`) vs local `configs/`. Cloud Run column is revision **`00577-bbl`** (Sep 28 evening, Trade Profile, no reseed). Deploy scripts pin **8Gi** + concurrency **4**.

### Deployment

| Key | Cloud Run (Demo `00577-bbl`) | Local `configs/` / deploy scripts |
|-----|------------------------------|-----------------------------------|
| **MODE** | `demo` | `demo` (`Shared.env`) |
| **BROKER** | `etrade` | `etrade` |
| **Cloud Run memory** | **`8Gi`** (live since `00534-bsh`) | **`8Gi`** (deploy scripts) |
| **ENABLE_ORB_STRATEGY** | n/a | n/a — SO ETF paused (`ENABLE_ORB_STRATEGY=false`) |
| **ENABLE_0DTE_STRATEGY** | `true` | `true` |
| **ENABLE_TRENDLINE_STRATEGY** | `true` | `true` |
| **ENABLE_BREAKOUT_STRATEGY** | `true` | `true` |
| **PORTFOLIO_PROTECTION_BLOCK_EXECUTION** | `false` | `false` (`ORBSO.env`) |
| **0DTE_POSITION_MONITOR_INTERVAL_SEC** | `2` | `2` |
| **0DTE_DISCOVERY_MAX_QUOTES_WHILE_OPEN** | `24` | `24` (`Shared.env`) |
| **0DTE_MONITOR_EVAL_BUDGET_MS** | `1200` | `1200` (`Shared.env`) |
| **0DTE_MONITOR_EVAL_BUDGET_MS_OPEN20** | `1800` | `1800` (`Shared.env`) — used at 20+ opens |
| **0DTE_MAX_SHADOW_LEFTOVER_PER_TICK** | `0` | `0` (`Shared.env`) — every winner leftover; positive = rotate cap |
| **0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW** | `true` | `true` (`Shared.env`) — skip leftover OCC while the live book is open so owned exact quotes keep the Market lane |
| **0DTE_PATH_REFRESH_SKIP_CYCLE_MS** | `8000` | `8000` (`Shared.env`) — discovery path-chain only, not leftover |
| **0DTE_UNDERLYING_FRESH_MAX_MS** | `4000` | `4000` (`Shared.env`) |
| **0DTE_UNDERLYING_LAST_GOOD_MAX_MS** | `8000` | `8000` (`Shared.env`) |
| **0DTE_UNDERLYING_OWNED_REFRESH_MAX** | `25` | `25` (`Shared.env`) — skip_cache last per tick for stale owned names |
| **0DTE_OPEN_EXACT_QUOTE_BATCH_SIZE** | **`25`** | **`25`** (`Shared.env`) — inner OCC wave; local waves sequential |
| **0DTE_EXIT_QUOTE_BATCH_CONCURRENCY** | **`4`** | **`4`** (`Shared.env` + deploy scripts). Local primary OCC does not stack HTTP |
| **0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN** | `0` | `0` (`Shared.env`) |
| **0DTE_DEMO_STARTING_BALANCE** | `5000` | `5000` (`Account0DTE.env`) |

### Broker selection

| Token | `BROKER` | Live cutover | Notes |
|-------|----------|--------------|-------|
| **E*TRADE** | `etrade` | ✅ | Certified Demo + Live |
| **Interactive Brokers** | `interactive_brokers` | ❌ | Stub |
| **Charles Schwab** | `schwab` | ❌ | Planned / fail-closed |
| **TastyTrade** | `tastytrade` | ❌ | Stub |

Canonical: `MODE=demo` + `BROKER=etrade` in `Shared.env`. **`ETRADE_MODE` retired.** Full readiness: [Brokers.md](Brokers.md).

---

## Strategy Quick Reference

| Strategy | Entry Logic | Exit Engine | Position Cap | Config File |
|----------|-------------|-------------|--------------|-------------|
| **ORB1 Breakout** | ORB1 watch **~06:45–07:25** (with TL0) → **Structure → Quality Gate → Confirm → PO Entry Snapshot → Enter Trade** | PATH_V2 (`path_v2_exit.py`) | `ORB_0DTE_MAX_POSITIONS=50` (live `00599-lhd`) | `ORB0DTE.env` · `Account0DTE.env` · `Shared.env` |
| **Trendline 0DTE** | TL0 **06:45–07:25** · TL1+ post-**07:30** structure break → **same five-step spine** → **12:30** cutoff | PATH_V2 for singles | `TRENDLINE_0DTE_MAX_POSITIONS=50` (live `00599-lhd`) | `Trendline0DTE.env` · `Account0DTE.env` · `Shared.env` |
| **Breakout 0DTE** | ORB2 freeze @ **≥07:30** → later high/low break → **same five-step spine** → **12:30** cutoff | PATH_V2 for singles | `BREAKOUT_0DTE_MAX_POSITIONS=50` (live `00599-lhd`) | `Breakout0DTE.env` · `Account0DTE.env` · `Shared.env` |

ORB SO ETF lives only in **RESEARCH** (`ORBSO.env`) — not shipped in this Live tree.

**Total 0DTE positions** (ORB + Trendline + Breakout): `TOTAL_0DTE_MAX_POSITIONS` / `0DTE_SLOT_COUNT` in `Account0DTE.env`.

---

## Configuration Ownership Matrix

**Rule:** Edit the owning file. Merge order is later-file-wins and deterministic. The same key with the same value in two files is harmless. The same key with two different values is a defect: the later file wins and the runtime value depends on that order. `CONFIG_DUPLICATE_KEY` logs flag overlaps; resolve conflicts, do not silence them.

| Concern | Source File |
|---------|-------------|
| Broker & accounts | `Data.env` |
| Cloud deployment | `Data.env` |
| Shared runtime (`MODE`, stealth, optimizers) | `Shared.env` |
| ORB SO ETF | `ORBSO.env` |
| Unified 0DTE account & portfolio risk | `Account0DTE.env` |
| ORB1 Breakout | `ORB0DTE.env` |
| Trendline 0DTE | `Trendline0DTE.env` |
| Breakout 0DTE | `Breakout0DTE.env` |
| Risk & sizing ceilings | `Risk.env` |
| Alerts | `Alerts.env` |

**Merge order:** `Data.env` → `Shared.env` → `ORBSO.env` → `Account0DTE.env` → `ORB0DTE.env` → `Trendline0DTE.env` → `Breakout0DTE.env` → `Risk.env` → `Alerts.env` (later overrides earlier). See [configs/README.md](../configs/README.md).

---

## Critical Trading Configs

These settings directly impact live PnL and should be modified only after session review.

### Entry logic

| Key | File | Role |
|-----|------|------|
| `0DTE_PRIORITY_USE_SO_RANK` | `ORB0DTE.env` | ORB1 Breakout rank mirrors SO priority (Rev 00374) |
| `0DTE_CONVEX_ORB_RANGE_MIN` | `ORB0DTE.env` | Convex ORB width floor (**0.25%**, strict) |
| `0DTE_CONVEX_REGIME_ORB_MULT` | `ORB0DTE.env` | Trend regime ORB multiplier (**1.0** ★) |
| `0DTE_CONVEX_V25_SYMMETRIC_ENABLED` | `ORB0DTE.env` | v2.5 symmetric side gates + score floor |
| `0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION` | `ORB0DTE.env` | Gate 3c PS floor **≥ 0.30** (★ stack — live) |
| `CALL_RECHECK_BREAKOUT_MULTIPLIER` | `ORBSO.env` | 7:30 fresh-quote filter — CALL + SO LONG vs ORB high |
| `PUT_RECHECK_BREAKDOWN_MULTIPLIER` | `0dte.env` | 7:30 fresh-quote filter — PUT vs ORB low |
| `TRENDLINE_MIN_SCORE` | `Trendline0DTE.env` | Post-draw break quality floor |
| `TRENDLINE_GEOMETRY_PHASE1_ENABLED` | `Trendline0DTE.env` | Dual-side 7:30 TL1 envelope draw |
| `TRENDLINE_BUILD_YF_1M_*` / `TRENDLINE_WATCH_YF_1M_*` | `Trendline0DTE.env` | Approved Yahoo RTH 1m structure supplement for TL A2 / TL2 / TL3 |
| `ENABLE_PORTFOLIO_PROTECTION` | `ORBSO.env` | 7:30 batch protection gate |

### Position sizing

**Edit `configs/Account0DTE.env` first** — keep `0DTE_SLOT_COUNT` = `TOTAL_0DTE_MAX_POSITIONS` (**50** RESEARCH Demo since `00599-lhd`; **7** FINAL Live Pilot). Full map: [0DTERisk.md §1](0DTERisk.md#0dte-position-sizing--caps-one-place-to-edit) · [§ Position Sizing Configuration](#-position-sizing-configuration).

| Group | Key | File | Default | Role |
|-------|-----|------|--------:|------|
| **Slots / capital** | `0DTE_SLOT_COUNT` | `Account0DTE.env` | **50** RESEARCH (`00599-lhd`) / **7** FINAL Live Pilot | Shared 1/N divisor (ORB1 + ORB2 + Trendline) |
| | `0DTE_TRADING_CAPITAL_PCT` | `Account0DTE.env` | 90 | Tradable pool → 10% sacred buffer |
| | `0DTE_MAX_POSITION_SIZE_PCT` | `Account0DTE.env` | 35 | Per-position pct ceiling. Looser than the 1/N slot and the $600 sum cap |
| | `0DTE_MAX_POSITION_SIZE_SUM` | `Account0DTE.env` + Cloud Run env | **600** (10/9; was 0) | Max $ per new position; `0` disables the dollar sum cap |
| | `0DTE_DEMO_STARTING_BALANCE` | `Account0DTE.env` + Cloud Run env | **100000** (RESEARCH, reseeded 10/9) / **100000** (FINAL) | Demo unified ledger |
| **Open caps** | `TOTAL_0DTE_MAX_POSITIONS` | `Account0DTE.env` | **50** RESEARCH / **7** FINAL | Combined ORB1 + ORB2 + Trendline (one variable) |
| | `0DTE_SLOT_COUNT` | `Account0DTE.env` | **50** RESEARCH / **7** FINAL | Sizing divisor — must equal TOTAL |
| | `ORB_0DTE_MAX_POSITIONS` | `Account0DTE.env` | **50** RESEARCH / **7** FINAL | ORB1 Breakout rolling open-count |
| | `BREAKOUT_0DTE_MAX_POSITIONS` | `Account0DTE.env` | **50** RESEARCH / **7** FINAL | ORB2 Breakout rolling open-count |
| | `TRENDLINE_0DTE_MAX_POSITIONS` | `Account0DTE.env` | **50** RESEARCH / **7** FINAL | Trendline rolling open-count |
| **Monitor** | `0DTE_MONITOR_MAX_SYMBOLS` | `Account0DTE.env` | **50** RESEARCH / **7** FINAL | Post-fill monitor (match combined book) |
| **Live market governors (ADV Guard)** | `LIVE_MAX_CONTRACTS_PER_TRADE` | `Account0DTE.env` | **1** | Live Pilot contract cap |
| | `LIVE_OPTION_VOLUME_PARTICIPATION_PCT` | same | **0** | Vol qty cap **off** (v1.6) |
| | `LIVE_OPTION_VOLUME_MIN_CONTRACTS` | same | **5** | Dormant until vol pct &gt; 0 |
| | `LIVE_OPTION_OI_PARTICIPATION_PCT` | same | **0** | OI qty cap **off** |
| | `LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT` | same | **0** | ask-depth qty cap **off** |
| | `LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT` | same | **0** | Bid not a qty cap |
| | `LIVE_UNDERLYING_ADV_PARTICIPATION_PCT` | same | **0.02** | ADV notional cap (binds only if oversize) |
| | `LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD` | `Shared.env` / `Account0DTE.env` / `Risk.env` | **0** | Generic missing-ADV floor **off** (fail-closed after broker + named floors) |
| | `LIVE_MIN_ASK_SIZE` / `LIVE_MIN_BID_SIZE` | same | **1 / 1** | MQ floors (fail-closed). Bid is **not** a qty cap |
| | `LIVE_REFRESH_QUOTES_BEFORE_SUBMIT` | same | **true** | Refresh before size/submit |
| | `LIVE_QUOTE_REFRESH_CACHE_MAX_AGE_SEC` | same | **5** | Reuse warm/manager chain if fresh + complete |
| | `LIVE_SIZE_CAP_ENFORCE_IN_DEMO` | same | **true** | Demo applies same guard clamps |
| **ORB funnel** | *(removed)* | — | — | Legacy 7:30 batch unused |
| | `0DTE_RANK_SIZING_ENABLED` | `ORB0DTE.env` | false | Equal slots unless true |
| **Aliases only (do not set)** | `UNIFIED_0DTE_*_MAX_POSITIONS` · `MAX_TOTAL_OPTION_POSITIONS` · `TRENDLINE_SLOT_COUNT` · `MAX_OPEN_POSITIONS` | leftover | — | `MAX_OPEN_POSITIONS` was a Prime/SO duplicate of TOTAL |
| **ORB SO** | *(retired)* | — | — | ETF path off; no SO max-size keys |

### Profit capture

| Prefix | File | Paths |
|--------|------|-------|
| `OPTION_*` / `OPTION_SINGLE_*` | `Shared.env` §2b (RESEARCH=FINAL) | Unified single-leg profit capture (ORB + TL) |
| `OPTION_STEALTH_*` | `Shared.env` §3 | Generic BE/trail/MFE (spreads / legacy) |
| `OPTION_STEALTH_ORB_*` | `Shared.env` §3 | ORB spread overlays + monitor helpers |
| `OPTION_STEALTH_TLINE_*` | `Shared.env` §3c | Trendline spread overlays + chop-hold |
| `STEALTH_*` | `Risk.env` §C | ORB SO ETF stealth |

Full ladder: [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Current PATH_V2 (11/04, Shared.env §2b; Save ON in `00624-8bn`).** One flat engine, STEP 0–30. **On:** STEP 1 R2X; STEP 2 20% stall save (`P20_STALL_SAVE`) plus UL-retrace extension (`P20_UL_RETRACE_SAVE`, both Save switches true; Cloud Run `ENABLED=true`, `SIDES=call`); STEP 3 shallow proof; STEP 4 cat stop; STEP 5 afternoon `mfe_trail` (12:30 **America/Los_Angeles**); STEP 6 absorption; STEP 7–8 350% runner (V3A timing); STEP 9 GBUL + UFR; STEP 12 spike fail + UL MFE capture; STEP 13 stale fail (high ceiling 0.34); STEP 17 shallow-green (SGUL hold); STEP 18 shallow time; STEP 19 bounded MFE capture (10:45–12:30 **America/Los_Angeles**); STEP 22 V3A shallow floor; STEP 23 immature stress; STEP 24 GB30 24pp + 38pp extension; STEP 25 HWM RTC; STEP 26 GB32 timed; STEP 27 deep green; STEP 28 V3A stall floor; STEP 29 ATR loss cut; STEP 30 VWAP cross. **Off:** STEP 10–11 hybrid trails; STEP 14 theta; STEP 15 Contract Fade; STEP 20–21 premium trails. Sparse-velocity is removed (no `OPTION_PATH_V2_VEL_SPARSE_ANCHOR_ENABLED` key). 11/04 wiring: ULR treats UL `<= 0` as missing; one evaluate per `(trade, tick)` with HOLD reuse; recorder writes every eval tick; exit bid is the frozen contract. Monitor/EOD: `no_data`, `degraded_data`, `end_of_day_exit` (12:50 PT). Settlement is fee-inclusive: `(exit − entry) × contracts × 100 − contracts × $1.30`. Rollback a feature with its own `*_ENABLED=false`; do not turn off the whole engine.

**PATH_V2 additions (Oct 17, still on):** `underlying_mfe_capture` and `bounded_mfe_capture` route through the passive midpoint-first ladder. The bounded rule is inactive at and after 12:30 PT, so `mfe_trail` remains the mandatory afternoon exit. Use only their feature-level kill switches for rollback.

**Current stale-high refinement (Oct 18):** `OPTION_PATH_V2_STALE_FAIL_HIGH_MAX_PRET=0.34` replaces `0.325` for the existing high stale-fail shape only. Full 8/19–10/16 tick replay adds **+$96** to Total Results and **+$20** to the current 261-trade Trade Profile book, with zero harmed winners and zero outcome flips. `0.36` harms one winner by $1,464, so do not widen this ceiling beyond `0.34`; rollback is `0.325`.

**20% Save (STEP 2, ON in `00624-8bn`, CALL-only):** `twenty_percent_save` / `P20_STALL_SAVE` then `P20_UL_RETRACE_SAVE`. Shared.env has `OPTION_PATH_V2_20P_SAVE_ENABLED=true` and `OPTION_PATH_V2_20P_SAVE_ULR_ENABLED=true` (both dataclass defaults still `false`). Cloud Run `OPTION_PATH_V2_20P_SAVE_ENABLED=true` on `00624-8bn`. ULR runs only when **both** switches are true. UL `<= 0` / `None` never arms or fires. Do not set a Cloud Run ULR key; let Shared.env apply. Stall knobs (`SIDES=call`, `MIN_T20_S=90`, `MAX_PEAK=0.50`, `MIN_SINCE_TOUCH_S=60`, `MIN_SEC_HWM=60`, `GIVEBACK=0.15`, `MIN_PRET=0.11`, `MAX_NEWHIGH_FRAC=0.05`, `SHADOW_ONLY=false`) and ULR knobs (`FRAC=0.90`, `ATR_K=0`, `ARM_S=0`, `MAX_ENTRY_VWAP_ATR=0.50`, `MAX_TOUCH_HOUR_ET=24`, `SYMBOLS` empty) are file-only (not on the service). Puts never save until `SIDES` is a config call. Missing `entry_distance_from_vwap_atr` never arms ULR. The 20p snapshot (`po_mfe20_snapshot`) stays decoupled; evaluate-path capture is `allow_sync_refresh=False` (local until next deploy). Details: [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Replay clock (11/04):** Live `evaluate_path_v2_tick` uses tick-start `now`. The recorder stores that as `eval_now` / `eval_age_s` and keeps `timestamp` as the monitor/write clock. Replay (`prep_replay_ticks`) uses `eval_now` and sorts by parsed datetime epoch, then `monitor_tick_id`. Historical rows without `eval_now` keep `timestamp` so the accepted book is unchanged. HOLD reuse keeps one evaluation per tick. Sparse-velocity is removed; IWM-type vel-window cases replay from the eval clock.

**STEP 9 (old STEP 7A) UL retrace capture (Oct 22, ON in Shared.env; in Demo `00620-btt`):** `guarded_retrace_exit` / `GBUL_RETRACE_05_65` and `ul_giveback_ratio_exit` / `UFR_GIVEBACK_30_70` (`OPTION_PATH_V2_GBUL_ENABLED=true`, `OPTION_PATH_V2_UFR_ENABLED=true`; dataclass defaults are false). They act on 30% up to 200% MFE winners with a fresh underlying quote (10s) and 2 consecutive ticks, route through the passive midpoint-first ladder, and each has its own kill switch (both false = the prior engine). Details: [0DTEProfitCapture.md](0DTEProfitCapture.md).

**STEP 17 shallow-green UL hold (SGUL; Oct 26, ON in Shared.env; in Demo `00620-btt`):** `shallow_green_exit` holds while the favorable UL move since entry is ≥ +0.5% and the UL giveback from its best is ≤ 0.1% (fresh UL only; re-checked every tick). Keys: `OPTION_PATH_V2_SG_UL_HOLD_ENABLED=true` (dataclass default `false`; kill switch), `OPTION_PATH_V2_SG_UL_HOLD_MIN_FAV=0.005`, `OPTION_PATH_V2_SG_UL_HOLD_MAX_GIVEBACK=0.001`. Details: [0DTEProfitCapture.md](0DTEProfitCapture.md).

**Oct 27 2026 — STEP 29 ATR loss cut + STEP 30 VWAP cross exit (ON in Shared.env; in Demo `00620-btt`; Eric approved 10/27):** two flat Profit Capture PC V2 steps after STEP 28 (`modules/path_v2_exit.py`), run only when STEP 0–28 gave no exit, no runner extension is active, the trade is past the **shared 120s checkpoint** (`OPTION_PATH_V2_MIN_HOLD_SECONDS=120`, the STEP 16 value, via `trade_past_120s_checkpoint`; used by STEP 29–30 only, no private 120s constant), and the position-monitor session-tech bar (`session_tech_bar_at`) is ≤ 180s old (missing bar time = no fire). Side-aware for CALL and PUT; reads `atr` / `vwap_distance_pct` from the live `osnap` overlay (`path_session_tech`).

| Key (Shared.env lines 469–481) | Shared.env | Dataclass default | Role |
|---|---:|---:|---|
| `OPTION_PATH_V2_ATR_LOSS_CUT_ENABLED` | `true` | `false` | STEP 29 kill switch |
| `OPTION_PATH_V2_ATR_LOSS_CUT_MAX_PRET` | `-0.30` | `-0.30` | bid ≤ −30% |
| `OPTION_PATH_V2_ATR_LOSS_CUT_ATR_MULT` | `2.0` | `2.0` | UL ≥ 2 ATR against since entry |
| `OPTION_PATH_V2_ATR_LOSS_CUT_MAX_PEAK` | `0.10` | `0.10` | confirmed (2-tick) peak < +10% |
| `OPTION_PATH_V2_ATR_LOSS_CUT_MIN_AGE_S` | `600` | `600` | age ≥ 600s |
| `OPTION_PATH_V2_ATR_LOSS_CUT_REQUIRE_VWAP_AGAINST` | `true` | `true` | UL on the wrong side of VWAP |
| `OPTION_PATH_V2_ATR_LOSS_CUT_BAR_MAX_AGE_S` | `180` | `180` | bar freshness |
| `OPTION_PATH_V2_VWAP_CROSS_EXIT_ENABLED` | `true` | `false` | STEP 30 kill switch |
| `OPTION_PATH_V2_VWAP_CROSS_MIN_PEAK` | `0.50` | `0.50` | armed after confirmed peak ≥ +50% and UL seen on the right side of VWAP |
| `OPTION_PATH_V2_VWAP_CROSS_ATR_BUFFER` | `0.25` | `0.25` | UL ≥ 0.25 ATR through VWAP |
| `OPTION_PATH_V2_VWAP_CROSS_CONFIRM_BARS` | `1` | `1` | distinct bars through VWAP |
| `OPTION_PATH_V2_VWAP_CROSS_MIN_PRET` | `0.10` | `0.10` | bid ≥ +10% at the exit |
| `OPTION_PATH_V2_VWAP_CROSS_BAR_MAX_AGE_S` | `180` | `180` | bar freshness |

Exit ids `atr_loss_cut` / `ATR_LOSS_CUT_30_2ATR` (MODERATE aggressive-limit ladder) and `vwap_cross_exit` / `VWAP_CROSS_50_025ATR` (PASSIVE midpoint-first ladder). Engine replay 8/19–10/26: both on **+$579 Total Results**, TP P3 **+$37** (ATR +$338 / +$16, VWAP +$241 / +$21; both off = 0 differences). Research had +$1,361 / +$277 with first-seen freshness on August ticks with no bar time; the approved "no bar time = no fire" rule removes those. Rollback: set either switch `false`. Details: [0DTEProfitCapture.md](0DTEProfitCapture.md).

### Portfolio risk

| Key | File | Role |
|-----|------|------|
| `MAX_DAILY_LOSS_PCT` | `Risk.env` | Daily loss halt; `0.0` disables for research, positive value restores live halt |
| `TOTAL_0DTE_MAX_POSITIONS` | `Account0DTE.env` | Combined 0DTE book cap (ORB1+ORB2+TL) |
| `0DTE_DEGRADED_ENTRY_KILL_SWITCH_*` | `Account0DTE.env` | Shared ORB+TL quote-health entry halt |
| `ENABLE_PORTFOLIO_PROTECTION_*` | `ORBSO.env` | Batch-day gate |

---

## High-Risk Configs (Do Not Change Lightly)

Changes to these values materially alter strategy behavior and require validation sessions (Demo session + chart/snapshot review).

| Key | Why high-risk |
|-----|---------------|
| `0DTE_PRIORITY_USE_SO_RANK` | Changes 0DTE execution order vs legacy blend |
| `0DTE_CONVEX_ORB_RANGE_MIN` | Gates Convex ORB width admission |
| `0DTE_ORB_LOTTO_FIRST` | Legacy env name — when `true`, **single-leg** is primary (see below) |
| `0DTE_CONVEX_ORB_RANGE_MIN` | Convex ORB width admission |
| `0DTE_SINGLE_LEG_OPENING_*` | Opening premium relax + soft cap (symmetric CALL/PUT) |
| `TRENDLINE_BREAK_MODE` | Production vs legacy emit stack |
| `TRENDLINE_GEOMETRY_PHASE1_ENABLED` | 7:30 structure selection |
| `TRENDLINE_GEOMETRY_*` | Dual-side TL1 envelope draw (mission contest removed) |
| `OPTION_STEALTH_BREAKEVEN_TRIGGER_PCT` | When BE arms (premium PnL) |
| `OPTION_STEALTH_BASE_TRAILING_PCT` | Trailing distance after activation |
| `OPTION_STEALTH_TLINE_*` | Trendline **spread** exit ladder + chop-hold helpers (singles use unified §2b Jul 1, 2026) |
| `OPTION_STEALTH_ORB_*` | ORB **spread** overlays + monitor helpers (degraded HWM, exit-grade relax) |
| `eval_now` / `eval_age_s` (JSONL) | Exact `now` and PATH_V2 age from `evaluate_path_v2_tick`. Replay clock. Not a threshold. |
| `OPTION_PATH_V2_20P_SAVE_ENABLED` / `_ULR_ENABLED` | Whole STEP 2 / ULR-only. ON in `00624-8bn`. `SIDES=call` is file-only. |

**Process:** See [Configuration Change Process](#configuration-change-process) below.

---

## Broker Truth & Reconciliation

In **Live Mode**, broker records are the source of truth. Live PnL, fills, positions, and reconciliation must come from broker-confirmed data — not local assumptions.

**Ownership chain (Jul 14):** Broker → `BrokerPositionSynchronizer` (sole Live registry writer) → `PremiumEngine` → `stealth.evaluate` → close executor → broker flat reconcile. Detail: [LiveModeReadiness.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/LiveModeReadiness.md#broker-truth-ownership-architecture-jul-14) · [0DTEProfitCapture.md §2.2](0DTEProfitCapture.md#22-monitor-cadence).

### Expected production audit log tokens

| Token | When | Meaning |
|-------|------|---------|
| **`BROKER_POSITION_SYNC`** | Each Live monitor tick | Synchronizer repair / pass / stale |
| **`BROKER_ACCOUNT_AUDIT`** | Startup / account refresh | Live account ID and balance readable |
| **`BROKER_FILL_CONFIRMED`** | After open order | Fill reconciled before position registration |
| **`broker_close_confirmed`** | After exit | Close fill reconciled before local position removed |
| **`BROKER_POSITION_FLAT_AUDIT`** | Post-close / EOD | Broker shows flat for closed symbol |
| **`LIVE_POSITION_MISMATCH`** | Residual orphan audit | Local registry ≠ broker after repair |
| **`SOLE_WRITER_VIOLATION`** | Bug | Live registry mutate without synchronizer token |
| **`LIVE_EXECUTION_BLOCKED`** | Fail-closed | Order rejected — no phantom position |

**Demo Mode:** Mock executor + simulated ledger; adapter blocks `submit_*`; synchronizer skipped (`demo_mode`). GCS under `research_app/*`.

**Live architecture:** [LiveModeReadiness.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/LiveModeReadiness.md) · Modules: `modules/broker_position_synchronizer.py` · `modules/premium_engine.py` · `modules/broker_adapter.py`

---

## Startup Validation

Verify deployment health immediately after startup. Grep Cloud Run logs for:

| Token | Purpose |
|-------|---------|
| **`PRODUCTION_STARTUP_AUDIT`** | Canonical snapshot: `MODE`, `BROKER`, strategies, executor, GCS namespace, `LIVE_SPREADS_ENABLED` |
| **`STARTUP_MODE_AUDIT`** | Mode resolved; `broker_orders=enabled\|disabled` |
| **`STARTUP_CONFIG_AUDIT`** | Missing env keys at cold start |
| **`CONFIG_MISSING_KEY`** | Key absent — fallback used (`fallback_used=true`) |
| **`CONFIG_DUPLICATE_KEY`** | Same key in multiple env files |
| **`CONFIG_MODE_CONSISTENCY_AUDIT`** | Mode alignment (`TradingConfig` vs `MODE`) |
| **`OPTIONS_LEVEL_AUDIT`** | E*TRADE options approval level vs launch profile |
| **`BROKER_CERTIFICATION_PROBE`** | Read-only cert result (CLI / `GET /api/broker-certification`) |
| **`BROKER_ACCOUNT_AUDIT`** | Live account wiring (certification + startup) |
| **`TRENDLINE_BROKER_SYNC`** | Trendline ↔ broker position sync (Live mode) |
| **`SPLIT_BRAIN_MODE_CONFIG`** | **Fatal** — conflicting mode keys |

**HTTP checks:**
```bash
curl -s https://<service>/health | jq .    # process up
curl -s https://<service>/ready | jq .      # Demo ready or Live probe
```

---

## Production Systems vs Research Systems

### Production systems (influence live/demo trade decisions)

| System | Config prefix / file |
|--------|----------------------|
| ORB SO | `ORBSO.env`, `STEALTH_*` |
| ORB1 Breakout | `ORB0DTE.env`, `OPTION_STEALTH_ORB_*` |
| Trendline 0DTE | `Trendline0DTE.env`, `OPTION_STEALTH_TLINE_*` |
| Option stealth (shared) | `OPTION_STEALTH_*` in `Shared.env` |
| Portfolio protection | `PORTFOLIO_PROTECTION_*` in `ORBSO.env` |

### Research systems (must not influence live trades unless promoted)

| System | Config prefix | Default |
|--------|---------------|---------|
| Profit optimizer | `PROFIT_OPTIMIZER_*` | Shadow path only |
| Priority optimizer | `PRIORITY_OPTIMIZER_*`, `ENABLE_EXECUTION_FEATURE_SNAPSHOT` | Snapshots only |
| Exit monitoring | `EXIT_MONITORING_*` | **`false` in production** |
| Trendline measurement | `ENABLE_TRENDLINE_MEASUREMENT` | JSON research artifacts |
| Trendline emit shadow | `ENABLE_TRENDLINE_EMIT_SHADOW` | Failed-emit research |

Research systems record data for tuning — they do **not** change execution, ranking, or exit decisions unless explicitly promoted into production logic and documented here.

---

## Configuration Change Process

1. **Modify local config** — edit the owning file per [Configuration Ownership Matrix](#configuration-ownership-matrix)
2. **Run tests** — path-specific + broker/mode suites when touching mode/broker:
   ```bash
   pytest tests/test_live_mode_readiness.py tests/test_production_soak_readiness.py \
     tests/test_config_mode_consistency.py tests/test_broker_certification_probe.py -q
   ```
3. **Update Configs.md** — if behavior or defaults changed
4. **Update strategy guide** — [0DTEORB1Breakout.md](0DTEORB1Breakout.md), [0DTETrendline.md](0DTETrendline.md), or Improvement Guide if logic changed
5. **Deploy** — only when user says `deploy now`
6. **Verify startup audits** — grep `PRODUCTION_STARTUP_AUDIT`, `STARTUP_CONFIG_AUDIT`, `CONFIG_MISSING_KEY`
7. **Verify runtime audits** — session-specific grep tokens (e.g. `0DTE_HARD_GATE_SYMBOL_SUMMARY`, `TRENDLINE_PIPELINE`)
8. **Record change** — session summary under `docs/doc_elements/Sessions/`

Prevents undocumented configuration drift.

---

## Live Deployment Readiness Checklist

Complete before setting `MODE=live` in production. RESEARCH path: **[LiveModeReadiness.md](LiveModeReadiness.md)**. FINAL checklists: **[FINAL LiveModeReadiness.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/LiveModeReadiness.md)**.

### Pre-cutover

- [ ] Deploy Jun 5 local bundle (`MODE=demo` first) — explicit `deploy now` only
- [ ] Demo production soak complete (≥ 1 session, no broker submits in logs)
- [ ] **`broker_certification_passed`** on the 0DTE options account (`ETRADE_0DTE_ACCOUNT_ID`; [PrivateSecrets.md](PrivateSecrets.md)) (`--broker-certification-probe` or `GET /api/broker-certification`)
- [ ] **`OPTIONS_LEVEL_AUDIT pass=true`** · **`CONFIG_MODE_CONSISTENCY_AUDIT aligned=true`**
- [ ] `PRODUCTION_STARTUP_AUDIT` shows expected values
- [ ] Demo EOD alerts **DEMO**; demo GCS paths only
- [ ] Tests green: **79/79** local broker/mode suite (see [Configuration Change Process](#configuration-change-process))
- [ ] Cloud Run env: `MODE=live`, `BROKER=etrade`, `LIVE_SPREADS_ENABLED=false`, `ETRADE_0DTE_ACCOUNT_ID=<see PrivateSecrets.md>`
- [ ] OAuth valid; Live account ID confirmed
- [ ] `--live-readiness-probe` or `GET /api/ready` returns `"ready": true`

### Cutover (market closed)

- [ ] Deploy with `MODE=live` (explicit `deploy now` only)
- [ ] Confirm `broker_orders=enabled` in startup audit
- [ ] Re-run broker certification + Live probe post-deploy
- [ ] Zero phantom positions in executor registry

### First Live session

- [ ] Opens show `broker_fill_confirmed` before monitor registration
- [ ] Exits show broker close confirmation
- [ ] Failed close triggers `LIVE CLOSE FAILED` alert
- [ ] Daily loss limits and kill switch verified
- [ ] Consider `0DTE_MAX_POSITIONS=1` for first Live week

**Current status:** Live cutover **blocked** — Demo validation + broker cert in prod required first. Full guide: [FINAL LiveModeReadiness.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/LiveModeReadiness.md).

---

## Broker Certification Status

Only **E*TRADE** is certified for Live Mode today. Interactive Brokers, Schwab, and TastyTrade are **planned (P2)** — config keys/stubs/placeholders exist; adapters are fail-closed until certified.

| Broker | Data | Orders | Exits | Reconciliation | Probe | Status |
|--------|------|--------|-------|----------------|-------|--------|
| **E*TRADE** | ✅ Real API | ✅ Adapter | ✅ Fill confirm | ✅ `reconcile_order` | ✅ Cert (13) + Live probe | **Code complete** · ⏳ prod cert gate |
| **Interactive Brokers** | Stub | Stub | Stub | Stub | ❌ `broker_not_etrade` | Not certified — P2 |
| **Charles Schwab** | Planned | Planned | Planned | Planned | ❌ `broker_not_etrade` | Not certified — P2 |
| **TastyTrade** | Stub | Stub | Stub | Stub | ❌ `broker_not_etrade` | Not certified — P2 |
| Robinhood / Alpaca / Tradier | — | — | — | — | — | Removed from `Data.env` Jun 18 |

**E*TRADE certification (13 read-only audits):** OAuth · 0DTE options account (`ETRADE_0DTE_ACCOUNT_ID`; [PrivateSecrets.md](PrivateSecrets.md)) · balance · **`OPTIONS_LEVEL_AUDIT`** · buying power simulation · market hours · SPY/QQQ chains · contract resolution · preview/model/position/status.

```bash
MODE=demo BROKER=etrade python3.11 main.py --broker-certification-probe
curl -s https://<service>/api/broker-certification | jq .
```

Checklist: [BrokerCertificationChecklist.md](BrokerCertificationChecklist.md) · Module: `modules/broker_certification_probe.py` · Factory: `create_broker_adapter()` in `modules/broker_adapter.py`

---

## 📋 **Table of Contents**

### Operator handbook
1. [Production Defaults Snapshot](#production-defaults-snapshot)
2. [Strategy Quick Reference](#strategy-quick-reference)
3. [Configuration Ownership Matrix](#configuration-ownership-matrix)
4. [Critical Trading Configs](#critical-trading-configs)
5. [High-Risk Configs](#high-risk-configs-do-not-change-lightly)
6. [Broker Truth & Reconciliation](#broker-truth--reconciliation)
7. [Startup Validation](#startup-validation)
8. [Production vs Research Systems](#production-systems-vs-research-systems)
9. [Configuration Change Process](#configuration-change-process)
10. [Live Deployment Readiness](#live-deployment-readiness-checklist)
11. [Broker Certification Status](#broker-certification-status)

### Configuration reference
12. [Configuration Overview](#configuration-overview)
13. [Configuration File Structure](#configuration-file-structure)
14. [Configuration taxonomy (by concern)](#configuration-taxonomy-by-concern)
15. [Core Configuration Files](#core-configuration-files)
16. [Strategy Configuration](#strategy-configuration)
17. [Risk Management Configuration](#risk-management-configuration)
18. [Position Sizing Configuration](#position-sizing-configuration)
19. [Broker Configuration](#broker-configuration)
20. [Alert Configuration](#alert-configuration)
21. [Deployment Configuration](#deployment-configuration)
22. [Secrets Management](#secrets-management)
23. [Environment Variables](#environment-variables)
24. [Configuration Best Practices](#configuration-best-practices)
25. [Configuration Validation](#configuration-validation)
26. [Troubleshooting](#troubleshooting)
27. [Additional Resources](#additional-resources)

### Appendices
28. [Appendix A — Configuration History](#appendix-a--configuration-history)
29. [Revision History](#revision-history)

---

## 🎯 **Configuration Overview**

The Easy ORB Strategy uses a **modular configuration system** with environment-style files stored in the `configs/` directory. The system supports:

- **ORB SO ETF** (`ENABLE_ORB_STRATEGY` in `ORBSO.env`): 7:30 AM PT ETF batch from SO Signal Collection — `MockTradingExecutor` (demo) or broker (live)
- **Easy 0DTE**: Options trading across **ORB1 Breakout**, **Trendline TL0/TL1/TL2/TL3/TL4**, and **ORB2 Breakout**. ORB1 + TL0 run in the early 06:45–07:25 PT window; 07:25–07:30 is the ORB2 capture gap; ORB2 + TL1+ begin after 07:30 PT.
- **Easy Trendline 0DTE**: Structure-first trendlines on the **full 0DTE list**, post-7:30 event entries, **premium stealth** exits (`modules/prime_options_stealth_trailing_tp.py`)
- **Easy Collector**: ML data collection service

### **Supported Brokers (Live product scope)**

| Broker | Status | Live cutover |
|--------|--------|--------------|
| **E*TRADE** | ✅ Complete — Demo + Live | ✅ Allowed |
| **Interactive Brokers** | 🚧 P2 planned | ❌ Blocked |
| **TastyTrade** | 🚧 P2 planned | ❌ Blocked |

Robinhood, Alpaca, and Tradier were **removed** from `Data.env` (Phase 2 trim, Jun 18). Product roadmap: **E*TRADE · IB · TastyTrade · Schwab** — see [Brokers.md](Brokers.md).

**Trading modes (E*TRADE):**
- **Demo:** Real E*TRADE data · mock execution · no broker submit
- **Live:** Real data · `ETradeBrokerAdapter` · 0DTE options account (`ETRADE_0DTE_ACCOUNT_ID`; [PrivateSecrets.md](PrivateSecrets.md))

E*TRADE uses one production API (no sandbox). `MODE` controls execution layer, not API endpoint.

### **Configuration Hierarchy**

1. **Primary**: `configs/` directory (env-style files)
2. **Runtime Overrides**: Environment variables (Cloud Run / shell)
3. **Local Development Secrets**: `secretsprivate/` (gitignored)
4. **Production Secrets**: Google Secret Manager (Cloud Run deployments)

### **Configuration Loading Order**

1. Default values (hardcoded in code)
2. Configuration files (`configs/*.env`) — see **`modules/config_loader.py`** for merge order: **`Data.env`** → **`Shared.env`** → **`ORBSO.env`** → **`Account0DTE.env`** → **`ORB0DTE.env`** → **`Trendline0DTE.env`** → **`Breakout0DTE.env`** → **`Risk.env`** → **`Alerts.env`** (later files override earlier). Former `base.env` / `deployment.env` / `trading-parameters.env` slices were merged into these files (May 2026).
3. Environment variables (override config file values when `get_config_value` runs — checked **before** the in-memory config dict)
4. Secret Manager (production secrets, where applicable)

**Startup export:** After `load_app_config()` in `main.py`, the **merged** configuration dict is copied into **`os.environ`** (string values). That makes file-based keys available to modules that call `os.getenv` directly.

---

## 📁 **Configuration File Structure**

```
configs/
├── README.md                        # Load order + single-source-of-truth map ⭐
├── CONFIG_AUDIT_ORB_0DTE_TRENDLINE.md
├── Data.env                         # Broker + data stack + paths + deploy slices (merged at runtime ⭐)
├── Shared.env                       # Cross-path orchestration
├── ORBSO.env                        # ORB Standard Order (ETF) path
├── Account0DTE.env                  # Unified 0DTE ledger, slots, caps, 0DTE_DEGRADED_*, LIVE_*
├── ORB0DTE.env                      # ORB1 Breakout options path
├── Trendline0DTE.env                # Trendline 0DTE path
├── Risk.env                         # Sizing + risk + slip
├── Alerts.env                       # Telegram only (7 keys)
├── config_manifest.yaml             # Key ownership audit (Pass 4 — Jun 2026)
└── (former `configs/*.env.template` files removed May 2026 — keys live in the eight path files; use `secretsprivate/etrade.env.template` for broker secrets)

modules/strategy_mode_presets.py     # advanced / quantum overlays (merged after the eight .env files)
```

**Note**: Broker consumer secrets use **`secretsprivate/etrade.env.template`** → **`etrade.env`** (not under `configs/`).

Each `.env` file uses **numbered section headers** in the file body (open the file and search for `# ---` or `# ===`). Path files (`ORBSO`, `ORB0DTE`, `Trendline0DTE`) share the same **seven groups** (§1–§7); shared infrastructure uses the taxonomy below.

---

## Configuration taxonomy (by concern)

**Quick index:** [configs/README.md](../configs/README.md#configuration-taxonomy-by-concern)

| Concern | Edit here | Key prefixes (examples) |
|---------|-----------|-------------------------|
| **Account & broker** | `Data.env` §1–§5 | `BROKER_*`, `ETRADE_*`, `IB_*`, `TASTYTRADE_*`, `SCHWAB_*` |
| **Deployment & cloud** | `Data.env` §7 | `DEPLOYMENT_MODE`, `GCP_*`, `GCS_*`, `DESK_SNAPSHOT_MARK_INTERVAL_SEC` |
| **Watchlists & paths** | `Data.env` §6 | `0DTE_WATCHLIST_*`, `*_PATH` |
| **Runtime shell** | `Shared.env` §8 | `MODE`, `BROKER`, `TRADING_MODE`, `ENABLE_PRIME_SYSTEM`, `LOG_*` |
| **Path toggles** | `ORBSO` / `ORB0DTE` / `Trendline0DTE` §1 | `ENABLE_ORB_STRATEGY`, `ENABLE_0DTE_STRATEGY`, `ENABLE_TRENDLINE_STRATEGY` |
| **Session timing** | `Shared.env` §5 | `TOKEN_PREP_*`, `ORB_WINDOW_*`, `CAPTURE_ORB2_*`, `DISCOVERY_ORB1_TL0_*`, `DISCOVERY_ORB2_TL_*`, `EOD_FLATTEN_*`, `EOD_REPORT_*`, `MORNING_ALERT_*` |
| **Ranking & filters** | Path §3 | `SO_*`, `0DTE_CONVEX_*`, `0DTE_CONVEX_SHORT_BREAKDOWN_*`, `0DTE_SINGLE_LEG_OPENING_*`, `0DTE_SINGLE_LEG_DIRECTIONAL_*`, `0DTE_DELTA_VIABILITY_*`, `TRENDLINE_*`, `TRENDLINE_GEOMETRY_*`, `TRENDLINE_CONTROL_LINE_*` |
| **Position sizing & capital** | `Account0DTE.env` (+ path aliases) · `ORBSO.env` · `Risk.env` | **0DTE:** `0DTE_SLOT_COUNT` · `0DTE_*_MAX_POSITIONS` · `TOTAL_0DTE_MAX_POSITIONS` · `0DTE_MONITOR_MAX_SYMBOLS` · **SO:** `SO_CAPITAL_PCT` · `MAX_CONCURRENT_TRADES` |
| **Instruments & liquidity** | `ORB0DTE` / `Trendline0DTE` §5 | `0DTE_DEBIT_*`, `TRENDLINE_OPTION_*` |
| **Execution gating** | `ORBSO` §6 · `ORB0DTE` §6 · `Shared.env` | `ENABLE_PORTFOLIO_PROTECTION_*`, `0DTE_HARD_GATE_*`, `0DTE_EXEC_SIGNAL_EXPIRE_MINUTES` |
| **Profit capture (live)** | `Shared.env` **§2b** (singles) · §3 overlays · `Risk.env` §C (SO equity) | `OPTION_*` / `OPTION_SINGLE_*`, `OPTION_STEALTH_*`, `STEALTH_*` |
| **Profit optimizer (research)** | `Shared.env` §7 | `ENABLE_PROFIT_OPTIMIZER`, `PROFIT_OPTIMIZER_*` |
| **Priority optimizer (research)** | `Shared.env` §6a | `ENABLE_EXECUTION_FEATURE_SNAPSHOT`, `PRIORITY_OPTIMIZER_*` |
| **Trendline measurement (research)** | `Shared.env` | `ENABLE_TRENDLINE_MEASUREMENT`, `ENABLE_TRENDLINE_EMIT_SHADOW` → `modules/trendline_measurement.py` |
| **Portfolio risk** | `Risk.env` §A | `MAX_OPEN_POSITIONS`, `MAX_DAILY_*`, `STOP_LOSS_*` |
| **Liquidity Guard (size)** | `Shared.env` / `Account0DTE.env` | Vol **off** · OI **off** · ask **200%** · bid qty cap **off** · ADV **2.0%** · generic floor **off** · min bid/ask **1** — [LiquidityGuard.md](LiquidityGuard.md) |
| **Liquidity Guard ADV data** | `Risk.env` | `LIQUIDITY_GUARD_ADV_LOOKBACK_DAYS` (0DTE only — [LiquidityGuard.md](LiquidityGuard.md) §5) |
| **Alerts** | `Alerts.env` | `TELEGRAM_*`, `ALERT_COOLDOWN_SECONDS` |

### Profit capture (live production exits)

| Path | Config location | Module |
|------|-----------------|--------|
| **ORB SO (ETF)** | `Risk.env` §C — `STEALTH_*` | `modules/prime_stealth_trailing_tp.py` |
| **ORB1 Breakout** | `Shared.env` §2b–§3 — `OPTION_*` / `OPTION_SINGLE_*`, `OPTION_STEALTH_ORB_*` | `modules/prime_options_stealth_trailing_tp.py` |
| **Trendline 0DTE** | `Shared.env` §2b–§3c — singles: `OPTION_*` / `OPTION_SINGLE_*`; spreads: `OPTION_STEALTH_TLINE_*`; helpers in `Trendline0DTE.env` §7 | same options stealth engine |
| **ORB1 Breakout spreads** | `Shared.env` §3 — `OPTION_STEALTH_SPREAD_*` | same |

Full ladder reference: [0DTEProfitCapture.md](0DTEProfitCapture.md). Path file **§7** holds path-specific monitor cadence and EOD windows — not the canonical `OPTION_STEALTH_*` block.

### Profit optimizer (read-only research)

| Key | Default | File |
|-----|---------|------|
| `ENABLE_PROFIT_OPTIMIZER` | `true` | `Shared.env` §7 |
| `PROFIT_OPTIMIZER_SHADOW_PATH_AFTER_EXIT` | `true` | `Shared.env` §7 |
| `PROFIT_OPTIMIZER_RECORDING_WINDOW_SECONDS` | `18000` | `Shared.env` §7 — fallback only if session clock import fails (live + **winning** shadow use EOD flatten) |
| `PROFIT_OPTIMIZER_LOSS_SHADOW_SECONDS` | `0` | `Shared.env` §7 — losing / flat exits **do not** shadow; winners shadow to EOD. Set `>0` to restore a post-loss window. |
| `PROFIT_OPTIMIZER_MAX_SHADOW_CONTINUATIONS` | `0` | `0` = every profitable leftover; losers never enroll. Ranked cap if set `>0`. |
| `PROFIT_OPTIMIZER_SHADOW_SAMPLE_SEC` | `2` | Post-exit leftover sample clock. Live PATH_V2 eval rows write on `evaluated=true` (not this throttle). Closed OCC off the owned batch; leftover quoted after evaluate on a single-flight task. Tape must be dense enough for PX2 / trail / giveback review. |
| `PROFIT_OPTIMIZER_SHADOW_FIRST_JUMP_RATIO` | `3.0` | Quarantine leftover when first shadow mark jumps vs last live; drop off the quote list immediately |
| `PROFIT_OPTIMIZER_SHADOW_STOP_LOSS_PCT` | `0` | unused / disabled — not a −20% shadow floor; losers stop via `LOSS_SHADOW_SECONDS` instead |
| `PROFIT_OPTIMIZER_GCS_SYNC` / `PROFIT_OPTIMIZER_GCS_RETENTION_DAYS` | `true` / `2` | `Shared.env` §7 — prune `daily/` · `paths/` · `cohorts/` |
| `PROFIT_OPTIMIZER_MAX_PATH_SAMPLES` | `20000` | RAM safety ceiling only. 2s cadence is **not** stride-capped |
| `PROFIT_OPTIMIZER_MAX_DAILY_JSON_BYTES` | `32000000` | Metrics-only daily JSON guard (Shared.env and code default 32,000,000). Over-cap skips GCS daily JSON. JSONL is the replay source. |
| `PROFIT_OPTIMIZER_CHECKPOINT_SECONDS` / `PROFIT_OPTIMIZER_PERSIST_MIN_WRITE_GAP_SEC` / `PROFIT_OPTIMIZER_JSONL_MIN_WRITE_GAP_SEC` | `90` / `45` / `5` | Dirty-mark + gaps; JSONL append every 5s; daily metrics rewrite every 45s. Row counts stay in `path_jsonl._LINE_COUNTS` (locked; cleared after a GCS download). |
| `PROFIT_OPTIMIZER_STALE_QUOTE_SECONDS` | `30` | `Shared.env` §7 — stale quote threshold for shadow-path recording (Rev **00379**) |

Does **not** change stealth or execution. See [profit_optimizer/README.md](../profit_optimizer/README.md).

### Priority optimizer (entry research)

| Key | Default | File |
|-----|---------|------|
| `ENABLE_EXECUTION_FEATURE_SNAPSHOT` | `true` | `Shared.env` §6a |
| `ENABLE_TRENDLINE_BREAK_SNAPSHOT` | `true` | `Shared.env` §6a |
| `ENABLE_TRENDLINE_LEGACY_JSONL_DATASET` | `false` | `Shared.env` §6a — retired JSONL event stream |
| `TRENDLINE_GEOMETRY_AUDIT_LOGS` | `false` | `Trendline0DTE.env` — Oct 13: per-candidate `TRENDLINE_DRAW_AUDIT` / `TRENDLINE_STRUCTURE_COMPARISON` at DEBUG only when true (was unconditional WARNING ×241 per build). `GEOMETRY_SELECTION_AUDIT` now INFO. No selection change |
| `0DTE_PRIORITY_COLLECTOR_ENABLED` | `false` | `Shared.env` §6a — retired sidecar; use execution snapshots |
| `EXECUTION_SNAPSHOT_LOCAL` | `false` | `Shared.env` §6a — Cloud Run GCS-only writes |
| `PRIORITY_OPTIMIZER_DAILY_SIGNALS_RETENTION_DAYS` | `2` | `Shared.env` §6a |
| `ENABLE_TRENDLINE_MEASUREMENT` | `true` | `Shared.env` — daily JSON under `priority_optimizer/trendline_measurement/` |
| `ENABLE_TRENDLINE_EMIT_SHADOW` | `false` | `Shared.env` — failed-emit OCC shadow off; leftover is for filled profitable trades |
| `ENABLE_TRENDLINE_PROFIT_OPTIMIZER` | `true` | `Shared.env` — Trendline fill/exit PO snapshots on |

See [PriorityOptimizer.md](PriorityOptimizer.md) · [0DTETrendline.md § Phase Next measurement](0DTETrendline.md).

### Entry spread policy (path-owned)

**Module:** `modules/spread_policy.py` — routes broker **gate 4** and entry checks by `strategy_type`. **Do not** reintroduce global `LIVE_MAX_SPREAD_PCT` (deprecated; removed from env Jun 2026).

| Path | File | Key | Default | Enforcement |
|------|------|-----|---------|-------------|
| **ORB chain filter** | `ORB0DTE.env` | `0DTE_MAX_BID_ASK_SPREAD_PCT` | **8.0** (% points) | `orb_chain_manager_spread_points()` |
| **ORB pick + entry** | `ORB0DTE.env` | `0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT` | **8.0** | `orb_entry_max_spread_points()` |
| **Trendline pick + entry** | `Trendline0DTE.env` | `TRENDLINE_OPTION_MAX_SPREAD_PCT` | **0.30** (fraction) | `trendline_entry_max_spread_points()` |

**ORB-only opening widen:** Hard-gate execution window + `0DTE_SINGLE_LEG_MOMENTUM_MAX_SPREAD_PCT` may relax ORB chain/selector to **10%** — does **not** apply to Trendline.

**Demo vs live:** Same numeric spread caps (**ORB 8%** · **Trendline 30%**). **No spread relax** at pick, entry, or submit. Position Sizing (90%/10 slots) is primary; Liquidity / ADV Guard clamps only when oversize (`LIVE_MAX_CONTRACTS_PER_TRADE=0`). Desk soak: `LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true`. Market quality (spread + min bid/ask size) enforced when `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS=true`. See [0DTERisk.md §11](0DTERisk.md#11-liquidity--adv-guard-execution-risk-layer).

**Removed / deprecated:** `LIVE_MAX_SPREAD_PCT`, `0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT` (use `0DTE_MAX_BID_ASK_SPREAD_PCT`).

**Tests:** `tests/test_spread_policy.py` · **Risk detail:** [0DTERisk.md §11](0DTERisk.md#11-liquidity--adv-guard-execution-risk-layer) · [§14.2a](0DTERisk.md#142a-entry-spread-policy-path-owned).

### Executable pricing (true-to-live PnL)

**Module:** `modules/option_executable_pricing.py` — long CALL/PUT single-leg economics on live E*TRADE chain quotes.

| Action | Order type | Demo sim fill | Live broker fill | Monitor / stealth exit |
|--------|------------|---------------|------------------|------------------------|
| **Enter** | Buy to Open | **Ask** (`MockOptionsExecutor`) | First limit is that ask. One lift of $0.05–$0.15. Recorded price is E*TRADE `average_fill_price` | `entry_premium` = fill price |
| **Exit** | Sell to Close | **Bid** (mock close) | Broker STC reconcile | `resolve_option_price()` → **bid** |

| Key | Default | File |
|-----|---------|------|
| `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS` | `true` | `Shared.env` §3e |
| `OPTION_USE_BID_FOR_LONG_EXIT` | `true` | `Shared.env` §3e |
| `OPTIONS_OPEN_COMMISSION_PER_CONTRACT` | **0.65** | `Shared.env` §3e |
| `OPTIONS_CLOSE_COMMISSION_PER_CONTRACT` | **0.65** | `Shared.env` §3e |

**Trade economics:** `modules/options_trade_economics.py` — `resolve_true_to_live_exit_fill` · `net_result_pnl_dollars` · `breakeven_exit_premium` · `TRADE_CLOSE_CERTIFICATION`. Protect floors are **not** fills. [0DTEProfitCapture.md §3.11](0DTEProfitCapture.md#311-fills-and-net-pnl-true-to-live) · [0DTERisk.md §14.2c](0DTERisk.md#142c-trade-economics-fees--net-pnl--breakeven).

**Demo PnL fidelity:** Spread / book-size market quality enforced when true-to-live marks on (default) + ask/bid fills above. Desk soak: `LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true` so Demo qty matches Live Liquidity Guard (`LIVE_MAX_CONTRACTS=0`). **Tests:** `tests/test_option_executable_pricing.py` · `tests/test_options_trade_economics.py` · `tests/test_pre_arm_profit_protect.py` · `tests/test_max_pnl_telegram_units.py` · [0DTEProfitCapture.md §3.11](0DTEProfitCapture.md#311-fills-and-net-pnl-true-to-live) · [0DTERisk.md §11](0DTERisk.md#11-liquidity--adv-guard-execution-risk-layer).

### ORB1 Breakout single-leg primary (local — `ORB0DTE.env` §3)

**Confirmed winning path (Jun 3–12 desk; Jul 27 band/premium update; Aug 21 cap relax):** `select_single_leg_contract(profile="directional")` → δ **0.18–0.42**, premium floor **≥ $0.03** (+ spot-scaled max via **`max($2.50, spot×0.028)`**), spread **≤8%**, `strategy_type=single_leg`. Rank **near-ATM / higher δ** (`0DTE_ENTRY_PREFER_LOWEST_DELTA=false`). Shared with Trendline/Breakout via `unified_contract_select.py`. Legacy selector profile **`lotto`** and env **`0DTE_LOTTO_BREAKDOWN_*`** are retired (code aliases old keys to `0DTE_SINGLE_LEG_OPENING_*`).

| Key | Default | Role |
|-----|---------|------|
| `0DTE_ORB_LOTTO_FIRST` | `true` | **Legacy name** — forces **single-leg** primary; spreads off |
| `0DTE_ORB_SPREAD_PRIMARY_ENABLED` | `false` | Legacy SO-tier **debit_spread** override (inactive) |
| `0DTE_ORB_SPREAD_FALLBACK_ENABLED` | `false` | No spread ladders after single-leg miss |

**Opening selector (symmetric CALL/PUT):** `easy0DTE/modules/options_chain_manager.py` → `select_single_leg_contract()` · `dte0_entry_selection.py` delta band · grep **`0DTE_SINGLE_LEG_SELECTED`**, **`ORB_0DTE_DIRECTIONAL_CAP_AUDIT`**.

| Key | Default | Role |
|-----|---------|------|
| `0DTE_ENTRY_DELTA_MIN` / `0DTE_ENTRY_DELTA_MAX` | `0.18` / `0.42` | Unified entry band (ORB + Trendline + Breakout) |
| `0DTE_ENTRY_PREFER_LOWEST_DELTA` | `false` | Prefer **near-ATM / higher δ** among passers (Jul 27) |
| `0DTE_MAX_ENTRY_PREMIUM` | `2.50` | Shared max per-share option entry premium across ORB, ORB2 Breakout, and Trendline |
| `0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT` | `8.0` | **Strict** spread cap — no momentum/opening widen at execution |
| `0DTE_MAX_BID_ASK_SPREAD_PCT` | `8.0` | Chain manager spread filter (canonical; replaces `0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT`) |

**Shared entry policy (final gate before submit — ORB + Trendline + Breakout):** `Shared.env` + `easy0DTE/modules/option_entry_liquidity_validator.py`

| Key | Default | Role |
|-----|---------|------|
| `0DTE_ENTRY_MIN_VOLUME` | `50` | Canonical min contract volume at execution |
| `0DTE_ENTRY_MIN_OPEN_INTEREST` | `100` | Canonical min OI at execution |
| `0DTE_MIN_ENTRY_PREMIUM` | `0.03` | Shared min per-share option entry premium |
| `0DTE_LIVE_MAX_QUOTE_AGE_SECONDS` | `10` | Pre-execution quote staleness (alias: `ORB_0DTE_LIVE_MAX_QUOTE_AGE_SECONDS`) |

`0DTE_MAX_ENTRY_PREMIUM`, `0DTE_MIN_ENTRY_PREMIUM`, `0DTE_ENTRY_DELTA_MIN/MAX`, `0DTE_ENTRY_MIN_VOLUME`, `0DTE_ENTRY_MIN_OPEN_INTEREST`, and `LIVE_MIN_ASK/BID_SIZE` are owned only by `Shared.env`. ORB selector/scoring floors (`0DTE_SINGLE_LEG_MIN_VOLUME`, opening-window relaxations) remain strategy-specific; execution must still pass the shared policy above.

Cold-start audit: `0DTE_EXECUTION_POLICY_AUDIT | entry_min_volume=… | entry_min_open_interest=… | entry_min_premium=… | entry_max_premium=… | orb_max_spread_pct=… | trendline_max_spread_pct=… | live_max_quote_age_seconds=… | entry_fill=ask | exit_fill=bid | …`

See [0DTEORB1Breakout.md §12](0DTEORB1Breakout.md#12-strategy-types-and-execution) · [ORB0DTEImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/ORB0DTEImprovementGuide.md) · [Jun 4 session](doc_elements/Sessions/2026/Jun%204%20Session/SESSION_SUMMARY_JUN04_2026.md).

### ORB1 Breakout — ORB1 watch → Execution (env map)

**Desk reference:** [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTEQualityGates.md](0DTEQualityGates.md) · legacy batch: [0DTEORB1Breakout.md §8](0DTEORB1Breakout.md#8-orb-0dte--signal-pipeline).

**Matrix baseline (Aug 7):** ORB1 watch · dir% hard · **pc_oi off** · orb **0.25–8%** · vol **0.12** · MACD eps **99** + mild deadband · E10/E11/E7 · **PS v2.1**. E14 off. Full PS: [0DTEORB PS](0DTEORB1Breakout.md#priority-score-ps-v21).

| Pipeline step | Config file | Keys |
|---------------|-------------|------|
| **Session clocks** | `Shared.env` §5 | `TOKEN_PREP_*` · `ORB_WINDOW_*` (ORB#1) · `CAPTURE_ORB2_*` · `DISCOVERY_ORB1_TL0_*` · `DISCOVERY_ORB2_TL_*` · `EOD_FLATTEN_*` · `EOD_REPORT_*` · `MORNING_ALERT_*` · `MARKET_TIMEZONE_*` · `modules/session_clock.py` |
| **ORB1 discovery** | `ORB0DTE.env` | `ORB_0DTE_DISCOVERY_MODE=watch` · clocks in Shared §5 · `expire_orb1_discovery_after_cutoff` · activate with TL0 after structure |
| **TL0 discovery + geometry cutoff** | `Trendline0DTE.env` | Same Shared `DISCOVERY_ORB1_TL0_*` (end **07:25**) · `tl0_geometry_cutoff_0710` reason token (Prime / engine / simple) |
| **TL GATE admit** | `Trendline0DTE.env` | Deployed Research Demo: wide-collect gate stack; Trade Profile 10.5 observe-only. Staged TP-admit candidate: 10.5 t155u in [0.TradeProfiles.md](0.TradeProfiles.md). |
| **Paused SC / validation / SO** | `ORBSO.env` | `ENABLE_ORB_SIGNAL_COLLECTION=false` · `ENABLE_VALIDATION_CANDLE=false` · `ENABLE_ORB_STRATEGY=false` |
| **Shared discovery** | `Shared.env` | `0DTE_DISCOVERY_SYMBOLS_PER_TICK=24` · `0DTE_DISCOVERY_INTERVAL_SEC=10` (118 @ 24/10s → every symbol ≤**50s**; quotes only — ORB1→TL0/ORB1 BO; ORB2→TL1–6/ORB2 BO). CONFIRM/READY do **not** pin slots. |
| **Quality Gate** | `easy0DTE/modules/quality_gate_stack.py` · `easy0DTE/modules/trade_profile.py` · `easy0DTE/configs/trade_profile_10.5.json` · `easy0DTE/configs/trade_profile_10.2_nohist.json` | Deployed Research Demo admit is `wide_collect` (`00596-sqc` / `00597-gdh`, 10/9). The 10.5 Trade Profile still loads, stamps, and records observe-only decisions. Current staged TP-admit candidate: 10.5 t155u (`2f241665…`, 215 / 167W/48L / +$93,331.32). Prior TP-admit deploy was `easy-etrade-strategy-00594-zn5` (10/8); rollback `easy-etrade-strategy-00593-mkh`. Catalog [0.TradeProfiles.md](0.TradeProfiles.md) |
| **Skip Signals** | `Shared.env` | `GATE_call_ss=off` · `GATE_put_ss=off`. Not on the stack. Record only: [retired keys](#absolute-call--put-skip-signals--live-keys) |
| **Hard Gate** | `ORB0DTE.env` | Watch execute clock = Shared `DISCOVERY_ORB1_TL0_*` · `0DTE_HARD_GATE_ALIGN_CONTINUATION_VOL=true` · `0DTE_HARD_GATE_*_VOL_FLOOR=0.012` (Nov 2; matches Wide) |
| **Priority Score PS v2.1** | `ORB0DTE.env` | `0DTE_PRIORITY_USE_PS_V2=true` · fallback `0DTE_PRIORITY_USE_SO_RANK=true` |
| **Gate 3c ★** | `ORB0DTE.env` | `0DTE_SHALLOW_E14_ENABLED=false` · `0DTE_CONVEX_E10/E11/E7` · chase skip · `0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION=0.30` · `ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX=50` (RESEARCH) |
| **PP 3a/3b @ execute** | `Risk.env` §E | **Paused Aug 7:** `ENABLE_PORTFOLIO_PROTECTION=false` · `ENABLE_RED_DAY_TIER_GATE=false` (rollback: re-enable + SC) |
| **Sizing / caps** | `Account0DTE.env` | `0DTE_SLOT_COUNT=50` · path caps **50** · total **50** (live `00599-lhd`) · `0DTE_MAX_POSITION_SIZE_SUM=600` · `LIVE_MAX_CONTRACTS_PER_TRADE=0` · `0DTE_MONITOR_MAX_SYMBOLS=50` · `0DTE_DEGRADED_*` |
| **Execution** | `Shared.env` + `ORB0DTE.env` | Shared entry policy (`0DTE_MAX_ENTRY_PREMIUM=2.50`, `0DTE_MIN_ENTRY_PREMIUM=0.03`, `0DTE_ENTRY_DELTA_MIN/MAX=0.18/0.42`) · ORB path execution (`0DTE_ORB_LOTTO_FIRST=true`, `0DTE_ENTRY_PREFER_LOWEST_DELTA=false`, `0DTE_MAX_BID_ASK_SPREAD_PCT=8.0`) |

### Convex eligibility (Jun 29 — v2.5 + ★ stack)

| Key | Default | Role |
|-----|---------|------|
| `0DTE_CONVEX_ORB_RANGE_MIN` | `0.25` | Minimum ORB width % for range pass |
| `0DTE_CONVEX_REGIME_ORB_MULT` | **`1.0`** | Trend regime: `orb_range ≥ ORB_MIN × mult` (symmetric CALL/PUT). **1.0** = min-width ORB counts as trend |
| `0DTE_CONVEX_REGIME_VWAP_ABS` | `1.0` | Trend regime when `\|vwap_distance_pct\|` exceeds this |
| `0DTE_CONVEX_TREND_DAY_REQUIRED` | `true` | Hard-reject **rotation** regime |
| `0DTE_CONVEX_V25_SYMMETRIC_ENABLED` | `true` | Side-symmetric VWAP extension / MACD disagreement / wide-ORB gates |
| `0DTE_CONVEX_MACD_DISAGREE_EPS` | **`99`** | **E16** — deep soft-neg observe; mild band handled by deadband quality |
| `0DTE_QG_MACD_DEADBAND_QUALITY_ENABLED` | **`true`** | ORB-only Layer B companion to eps; near-zero wrong-side hist must pass deadband quality |
| `0DTE_QG_MACD_DEADBAND_ORB_MAX_PCT` | **`1.0`** | Deadband quality requires tight ORB; preserves GLD/IBIT and avoids global ORB max tighten |
| `0DTE_QG_MACD_DEADBAND_MACD_ABS_MIN` | **`0.60`** | Deadband quality strong MACD-line branch (`|macd|≥0.60`) |
| `0DTE_QG_MACD_DEADBAND_VOL_MAX` | **`0.20`** | Deadband quality thin-tape branch (`volume_ratio<0.20`) |
| `0DTE_CONVEX_SESSION_TECH` | `true` | **Session tech master switch** — hydrate VWAP/MACD/RSI/volume_ratio from **today RTH 1m** for ORB Convex, Trendline quality, and Breakout (`modules/session_tech_indicators.py`) |
| `0DTE_SESSION_TECH_YF_1M_BACKFILL` | `true` | **Aug 4** — bounded Yahoo RTH 1m history when priced closes &lt; 15 (RSI depth); broker quote minutes stay authoritative for `volume_ratio` |
| `SESSION_TECH_WARMUP_PRIOR_SESSION` | `true` | **Oct 5** — from 06:00 PT, one background batched Yahoo 1m fetch (`period=2d`, RTH, prior session only) keeps the last N prior-session bars per watchlist symbol; used only for RSI-14 / MACD 12/26/9 / SMA-20/50 / EMA-12/26 / Bollinger-20 / momentum-10 while today's closes are too few (VWAP/ATR/volume stay today-only; stamps `tech_warmup_used`, `tech_warmup_bars`). `modules/session_tech_warmup.py` |
| `SESSION_TECH_WARMUP_BARS` | `60` | Prior-session bars kept per symbol (35–240; MACD needs 34, SMA-50 needs 50) |
| `SESSION_TECH_WARMUP_START_PT` | `06:00` | Earliest PT time for the prior-session fetch (≤4 attempts, 120 s apart, until 12:30) |
| `SESSION_TECH_WARMUP_BATCH_SIZE` | `20` | Yahoo symbols per batch for the warm-up fetch |
| `SESSION_TECH_TL0_BAR_SEED` | `true` | **Oct 5** — merge the TL0 build's real Yahoo 06:30–06:45 1m bars into the session-tech cache, so RSI / ATR / VWAP-ATR / 3m PVE exist at the first ORB1 / TL0 gate |
| `CHAIN_RING_PREWARM_ENABLED` | `true` | **Oct 5** — live 0DTE chain sample per watchlist symbol into the central chain ring so `dir_pct_change_5m` has a real prior at the first ORB1 / TL0 gate; chain evicted from the decision cache, not added to discovery warm-refresh, yields to the open-position monitor |
| `CHAIN_RING_PREWARM_TIMES_PT` | `06:40,06:50,07:00,07:15,07:26,07:33` | Pre-warm slots = the dir5m open seed (first slot: all symbols; later: only symbols with no ring sample in 4 min). 06:40 / 06:50 / 07:00 / **07:15** cover the 06:45–07:25 ORB1 / TL0 gates; 07:26 / 07:33 cover the 07:25–07:30 capture gap and 07:30 TL1 + ORB2 build |
| `CHAIN_RING_PREWARM_WINDOW_SEC` | `240` (code; doc said 180 before 10/14) | Window after each slot in which it may start (30–900) |
| `CHAIN_RING_PREWARM_CONCURRENCY` | `4` (code; doc said 2 before 10/14) | Parallel chain fetches during pre-warm (1–6) |
| `DIR5M_RING_V2_ENABLED` | `true` | t166u ring v2: F1 observation-time samples, F2 fallback, F3 reason codes, TTL; also stamps the t168u lead / source keys. `false` = pre-t166u behaviour |
| `DIR5M_FALLBACK_ENABLED` | `true` | Nearest-prior fallback when the strict 4–7 min window is empty (`DIR5M_FALLBACK_MIN_AGE_SEC` 180 / `DIR5M_FALLBACK_MAX_AGE_SEC` 600); stamped `dir_pct_prior_window=fallback` |
| `DIR5M_FALLBACK_MIN_AGE_SEC` / `DIR5M_FALLBACK_MAX_AGE_SEC` | `180` / `600` (deploy: default) | F2 fallback window (3–10 min) |
| `DIR5M_RING_TTL_SEC` | `3600` | Ring sample TTL (strict mode; pruned on record after the R6 check) |
| `DIR5M_RING_PERSIST_ENABLED` | `true` | Restart persistence `{research_app}/daily_markers/chain_ring_5m/{date}.json` (same mechanism as the TL / ORB day freeze; same-day reload; `DIR5M_RING_PERSIST_DEBOUNCE_SEC` 60, `DIR5M_RING_PERSIST_MAX_PER_SYMBOL` 16) |
| `DIR5M_RING_PERSIST_DEBOUNCE_SEC` | `60` (min 5; deploy: default) | Ring persist write debounce |
| `DIR5M_RING_PERSIST_MAX_PER_SYMBOL` | `16` (min 4; deploy: default) | Newest samples per symbol persisted (in-memory ring holds up to 64) |
| `DIR5M_APPROACH_SAMPLER_ENABLED` | `false` (**deploy: `true`**, 00600-fpn) | t168u path-agnostic 5m pre-sampler (`central_tech/dir5m_presample.on_discovery_quotes`, one call per unified discovery tick, all paths and sides). Off = no-op |
| `DIR5M_APPROACH_MAX_PER_MIN` | `6` (**deploy: `12`**) | Sampler chains queued per rolling 60 s (2 Market calls each: 6 ≈ 12/min, 12 ≈ 24/min) |
| `DIR5M_APPROACH_RESAMPLE_SEC` | `300` | One sample per symbol per 300 s (also across the TTL re-arm); a symbol whose ring already has a sample this young is skipped with no API |
| `DIR5M_APPROACH_TTL_SEC` | `1800` | Re-arm window for the lead-time clock |
| `DIR5M_APPROACH_PCT` / `DIR5M_APPROACH_ATR` | `0.004` / `0.25` | Approach band = max(PCT × level, ATR × candidate ATR) |
| `DIR5M_APPROACH_RANK` | `close` | In-band ranking per tick (`close`; research: `toward`, `toward_repeat`, `repeat`) |
| `DIR5M_SAMPLE_OPEN_POSITIONS` | `false` (**deploy: `true`**, t194u) | Held-position dir5m sampling: monitor singleton → P2 chain sample per held symbol (exit / 20p priors and a fresh now-chain). `false` = legacy `CHAIN_RESAMPLE_OPEN_POSITIONS` path (P0; found no positions) |
| `DIR5M_OPEN_POSITION_RESAMPLE_SEC` | `300` (**deploy `180`**, t202u; 60 min) | 1 sample per held symbol per N s (180 lifts the 10/15 sim to exit 98 % / 20p 71 % at about +7 calls/min peak) |
| `DIR5M_OPEN_POSITION_MAX_PER_MIN` | `6` (**deploy `12`**, t202u; 10/15 peak 34 held underlyings) | Held-position samples per rolling 60 s, oldest ring sample first (≈ 12 Market calls/min max) |
| `DIR5M_APPROACH_AGING_FIRST` | `true` | Rank order: armed, then symbols whose last ring sample is `DIR5M_APPROACH_AGING_MIN_SEC` (240) to `DIR5M_FALLBACK_MAX_AGE_SEC` (600) s old, then closest. `false` = t168u closest-first |
| `DIR5M_APPROACH_BOOST_MAX_PER_MIN` / `DIR5M_APPROACH_BOOST_WINDOW_PT` | `0` (off) / `06:30-09:00` | Optional sampler cap inside the PT window (e.g. 18). Off: at 18 the coincident peak exceeds the 140/min app cap |
| `DIR5M_APPROACH_NOTE_CANDIDATE` | `false` | `true` also arms the manager's own 285 s / 900 s resample loop (outside the sampler caps) |
| `DIR5M_SNAPSHOT_ASOF_ENABLED` | `true` (deploy: default) | t172u: 20p / exit / mid-trade / flight-recorder snapshots compute dir% as of their own event time; handoff / entry use the decision clock. `false` = legacy row clock |
| `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC` | `60` (min 5; **deploy: `210`**, t202u (t194u 180); age stamped `dir_pct_now_chain_age_s`) | Max gap between a snapshot event and its now-chain (row chain, else newest ring sample); beyond it → `no_current_chain` |
| `DIR5M_KEEPALIVE_ENABLED` | `false` (**deploy: `true`**, t202u) | Handoff keep-alive: every live non-terminal candidate symbol (any path, from registration) gets a P2 chain sample when its newest ring sample is older than the cadence, until execute / expire / retire |
| `DIR5M_KEEPALIVE_SEC` / `DIR5M_KEEPALIVE_WIDE_SEC` | `210` / `360` (max 400) | Cadence for armed + in-band / other live candidates (any sample gap ≤ 420 s keeps a 180–600 s prior available) |
| `DIR5M_KEEPALIVE_MAX_PER_MIN` | `24` | Keep-alive chains per rolling 60 s (own cap, separate from the approach cap; ≈ 48 Market calls/min max) |
| `DIR5M_KEEPALIVE_SCOPE` | `live` | `live` = every live candidate; `band` = armed + in-band only |
| `DIR5M_KEEPALIVE_RETRY_SEC` / `DIR5M_ARM_NOW_SAMPLE_SEC` | `60` / `60` | Re-enqueue gap for a not-yet-fetched keep-alive; an armed (must-execute) symbol with no sample this young is sampled on that tick |
| `DIR5M_KEEPALIVE_DEDUP_SEC` | `45` | Manager drain drops a `dir5m_keepalive` / `dir5m_20p` item only if the ring got a sample this recently (generic 285 s otherwise) |
| `DIR5M_OPEN_SHORT_WINDOW_PT` / `DIR5M_OPEN_SHORT_MIN_AGE_SEC` | empty (off; **deploy `06:30-06:36\|06:45-06:51\|07:30-07:36`**) / `60` | Inside these PT minutes only: a 60–180 s prior when strict + fallback find none, stamped `dir_pct_prior_window='open_short'` (filterable) |
| `DIR5M_20P_PREFETCH_ENABLED` | `false` (**deploy: `true`**) | t204u. Pre-cross 20p chain prefetch: while live return is in [`DIR5M_20P_PREFETCH_MFE`, 0.20) and the ring's newest chain is older than `DIR5M_20P_PREFETCH_REFRESH_SEC`, queue one P0 fetch (`dir5m_20p_prefetch`). Stops at the 20p event; never fetches after it. Stamp `dir_pct_20p_prefetch_n` |
| `DIR5M_20P_PREFETCH_MFE` | `0.08` | Live return at which 20p prefetch starts |
| `DIR5M_20P_PREFETCH_REFRESH_SEC` | `30` | Prefetch only if the ring's newest chain for the underlying is older than this |
| `DIR5M_20P_PREFETCH_RETRY_SEC` | `15` | Min seconds between prefetches for one trade |
| `DIR5M_20P_PREFETCH_MAX_PER_MIN` | `6` | Global prefetch cap (10/15 replay peak 9 uncapped, avg 0.5/min) |
| `DIR5M_20P_NOW_MAX_AGE_SEC` | unset → `DIR5M_SNAPSHOT_NOW_MAX_AGE_SEC` (**deploy: `90`**) | 20p now-chain max age (observed ≤ event). Older → `now_chain_stale` |
| `OPTION_API_HARD_CAP_PER_MIN` | `140` (**deploy: `180`**, t202u; was hard-coded) | App throttle on real Market calls/min; keep in step with `CHAIN_FETCH_HARD_CAP_PER_MIN` |
| `TP_DIR5M_PRIOR_MIN_AGE_SEC` / `TP_DIR5M_PRIOR_MAX_AGE_SEC` | `240` / `420` (deploy: default) | Strict 5m prior window (4–7 min) |
| `0DTE_CONVEX_VWAP_EXTENSION_REJECT_PCT` | `15.0` | Reject extended VWAP (±%) |
| `0DTE_CONVEX_ORB_RANGE_MAX_PCT` | **`8.0`** | E15/E16 widen (MELI/HOOD headroom) |
| `0DTE_CONVEX_MIN_SCORE` | unused | Was Convex floor 0.75 — score is now observe extension ranker |
| Directional % | profile key `dir_pct_change_5m` | PASS on every profile. Not a separate gate. |
| `0DTE_DIR_VOLUME_MIN_PCT` | **`0.10`** | Strict **>** floor (CALL call/(c+p) · PUT put/(c+p)). Noted E16/LS1 book was **0.54**. |
| `0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION` | **`0.30`** | Gate 3c PS floor — symmetric CALL/PUT (★ stack live) |
| `0DTE_SHALLOW_E14_ENABLED` | **`false`** | E14 admit OFF (telemetry) — conflicts with Jun 26 E11 below-VWAP CALL winners |
| `0DTE_CONVEX_MACD_DISAGREE_EPS` | **`99`** | E16 deep soft-neg observe |
| `0DTE_QG_MACD_DEADBAND_*` | **`true` / `1.0` / `0.60` / `0.20`** | ORB-only deadband quality; TL/BO MACD remains stamp/report only |
| `0DTE_CONVEX_CHASE_SKIP_ENABLED` | **`true`** | Compound vol+VWAP+adverse pc_oi/index chase skip |
| `0DTE_CONVEX_E10_ENABLED` / `E11` / `E7` | **`true`** | Protection CALL block + carve-out + index grind skip |
| `0DTE_SHALLOW_CALL_MACD_MIN` | **`2.0`** | CALL reject when `vwap < 0` and `macd < 2.0` |
| `0DTE_SHALLOW_PUT_MACD_MAX` | **`-2.0`** | PUT reject when `vwap > 0` and `macd > −2.0` |
| `CALL_RECHECK_BREAKOUT_MULTIPLIER` | `1.000` | 7:30 recheck — SO LONG + 0DTE CALL vs ORB high |
| `PUT_RECHECK_BREAKDOWN_MULTIPLIER` | `1.000` | 7:30 recheck — 0DTE PUT vs ORB low |

**Canonical Quality Gate (RESEARCH Demo):** **since 10/13** the live admit is `SINGLE_TP_ADMIT=true` / `TRADE_PROFILE_VERSION=wide` on `easy-etrade-strategy-00625-wwv`. The `wide_collect` stack (`QUALITY_GATE_ADMIT=wide_collect`, 10/9 `00596-sqc` / `00597-gdh`) with 10.5 Trade Profile observe-only is **10/9–10/13 history**. Local source no longer has that branch. The staged local TP-admit candidate remains 10.23 S0_maxnet (sha `a449ab0d…`; not the deployed admit). Prior hard TP-admit deploy was `easy-etrade-strategy-00594-zn5` (10/8; rollback `00593-mkh`). FINAL Live is retired.

**Session tech (Jul 16 · unified Jul 29 · Aug 4 invent-clear):** With `0DTE_CONVEX_SESSION_TECH=true`, VWAP/MACD/RSI/volume_ratio for **ORB Convex, Trendline, and Breakout** come from one hub (`ensure_session_tech_bars` → `get_session_tech` → `apply_session_tech` · `tech_source=session_1m_rth`). Bar order: broker quote minutes → day_seed when thin → optional Yahoo RTH 1m backfill when closes &lt; 15 (`0DTE_SESSION_TECH_YF_1M_BACKFILL`). Missing RSI/MACD stamp as **null** + `*_missing` (never invent 50/0). Missing RSI is soft-skip. ORB1/TL0 volume missing **skip** (first-hour); BO volume missing fail-closed. Grep: `SESSION_TECH` · `0DTE_SESSION_TECH` · [Data.md §4](Data.md#4-session-tech-hub) · [0DTEORB1Breakout.md §8.2](0DTEORB1Breakout.md#82-hydrate--session-tech--chain-skew-steps-3–3b).

**Runtime (RESEARCH Demo):** session tech + single Trade Profile admit (TP Wide) on `00625-wwv`. Gate 3c PS / cap-10 text below is the pre-10/13 `wide_collect` stack. FINAL is retired. E14 telemetry/off.

### Quality Gate Settings — ORB · Trendline · Breakout

**Canonical guide:** **[0DTEQualityGates.md](0DTEQualityGates.md)**.

| Setting | ORB1 Breakout (**E16**) | Trendline 0DTE | Breakout 0DTE (**beginner**) |
|---------|----------|----------------|---------------|
| Score | Extension **observe** (not Convex 0.75) | scaffolding (`TRENDLINE_MIN_SCORE=55` logs only) | Extension **observe** (not Quality 55) |
| Break | min **0.08%** beyond H/L · width **0.25%–8.0%** · max **2.50%** | close-beyond **0.08%** (`TRENDLINE_MIN_BREAK_DISTANCE_PCT=0.0008`) | first print **0.08%** · max **2.50%** |
| Follow-through / continuation | ORB path-specific | **deleted** (one min-break key only) | **deleted** (one min-break key only; reclaim still invalidates) |
| Volume ratio | Wide-collect gate stack currently admits; profile reads/stamps its own keys observe-only. | same | same |
| BB | — | **observe** CALL **≤70** / PUT **≥30** (`GATE_TL_BB` + `QG_TL_BB_*`) | A7 mid-stretch **≥50** / **≤50** |
| Directional % and open interest | Wide-collect has its own dir%/volume/contract gates; profile keys `dir_pct_change_5m` and same-side chain open interest are stamped observe-only until TP admit is re-enabled. | same | same |
| Rank | **PS v2.1** · floor **≥0.30** · cap **10** | slot/cap | slot/cap + same-side cooldown |
| Contract | Wide-collect contract gates are active per 10/9 deploy notes; order path still last-looks contract spread. | same | same |
| `price_stop` | Shared **−0.80%** (before ARM) | Shared **−0.80%** (before ARM) | Shared **−0.80%** (before ARM) |
| VWAP | **observe** \|vwap%\| **≤1.00** stamp (`GATE_ORB_VWAP`) | **observe** (`GATE_TL_VWAP`) | **observe** \|vwap%\| **≤1.00** stamp (`GATE_BO_VWAP`) |
| RSI | anti-chase CALL **≤60** / PUT **≥40**; rsi_band **off** | **observe** CALL **≤80** / PUT **≥20** (`GATE_TL_RSI` + `QG_TL_RSI_*`) | momentum floor **off** |
| MACD | eps **99** + mild deadband ON (noise **±0.50**, ORB≤**1.0%**, line≥**0.60** or vol&lt;**0.20**); E14 off | stamp/report only | observe (`BREAKOUT_MACD_HOLD_GATE_ENABLED=false`) |
| **Trade Profile** | Observe-only while deployed admit is `wide_collect`; staged hard profile settings in [0.TradeProfiles.md](0.TradeProfiles.md). | same · slot `trendline0` / `trendline1` / `trendline2+` | same · all breakout slots |
| **Call SS** | **hard** in deployed wide-collect (`GATE_call_ss` / path Call SS hard per 10/9 notes) | same | same |
| **Put SS** | **hard** in deployed wide-collect (`GATE_put_ss` / path Put SS hard per 10/9 notes) | same | same |
| **Path SS** | **off** | **off** | **off** |

These are the current profitable-trade levels documented in [0DTEQualityGates.md](0DTEQualityGates.md) · [0DTERisk.md §2.2](0DTERisk.md#22-quality-gate-profitable-trade-settings). PS: [0DTEPriorityScore.md](0DTEPriorityScore.md).

**Uniform 0DTE entry spine (ORB1 Breakout · TL0+ · ORB2 Breakout)**

| Step | Name | What happens |
|------|------|----------------|
| 1 | **Structure** | Trade signal: ORB#1 / ORB#2 box break or TL line break |
| 2 | **Quality Gate / Trade Profile stamp** | Deployed Research Demo (`00625-wwv`) admits with `SINGLE_TP_ADMIT=true` / `TRADE_PROFILE_VERSION=wide`. Bars, chain, and quote ring still hydrate the freeze for handoff stamping. Staged local TP-admit candidate is 10.23 S0_maxnet (`trade_profile_10.23.json`, sha `a449ab0d…`; not the live admit). |
| 3 | **Confirm** | Same stamp, then the profile once. No second dir% or open-interest gate. |
| 4 | **PO Entry Snapshot** | `*_emit_handoff` Priority Optimizer entry snap |
| 5 | **Enter Trade** | Path executor submit |

```bash
# Shared.env — exec-ready TTL for TL/BO capital/slot wait
0DTE_EXEC_SIGNAL_EXPIRE_MINUTES=1
```

See [ARCHITECTURE.md](ARCHITECTURE.md) · [ProcessFlow.md](ProcessFlow.md).

#### Skip Signals — retired on the research app {#absolute-call--put-skip-signals--live-keys}

Entry, Skip, and Delta Moon are **off**. The admit is the Trade Profile in [0.TradeProfiles.md](0.TradeProfiles.md). `GATE_entry_part`, `GATE_call_ss`, and `GATE_put_ss` are **off** in `Shared.env` and the path env files. The tables below are the 2026-09-27 record. They do not score a signal.

Before accept or reject, `stamp_admit_tech` fills this signal from its bars, its chain, and its quote ring. The profile runs on that stamp. Compact `qg_admit` persists on `watch_fsm`.

**Desk column order** follows `modules/po_key_usefulness.py`: `call_chain_volume`, `put_chain_volume`, `representative_call_delta`, `representative_put_delta`, `price_volume_efficiency_3m`, `break_distance_pct`, `call_chain_volume_delta_5m`, `total_chain_open_interest`, `rsi`, `total_chain_volume`, `implied_volatility`, `spy_change_pct`, `theta`, `vwap_distance_pct`, `distance_from_vwap_atr`, `dir_pct_change_5m`, then the replay rank. The pocket numbers below stay the evaluation order.

**Record only (2026-09-27).** The CALL 110k / PUT 11k entry and the skip pockets below are not the admit. The Trade Profile replaced them.

| Side | Admit when | Env |
|------|------------|-----|
| CALL | `call_chain_volume` is present, finite, and **>= 110000**. Missing/non-finite rejects. | `GATE_entry_part=hard` · `QG_CALL_ENTRY_CHAIN_MIN=110000` |
| PUT | `put_chain_volume` is present, finite, and **>= 11000**. Missing/non-finite rejects. No five-minute entry check. | `QG_PUT_ENTRY_CHAIN_MIN=11000` |

Delta Moon remains implemented but is **disabled** for this architecture. Keep `QG_CALL_DELTA_MOON_MIN`, `QG_CALL_GAMMA_MOON_MIN`, and `QG_PUT_DELTA_MOON_MIN` omitted until a future certified Delta Moon key is identified. When re-enabled later, it must be documented here and re-tested against Entry plus Skip.

**2026-09-27 PC V2 replay (9/10–9/25), local, not deployed.** Current 9/27 Skip Signals retire CALL C1 (`price_volume_efficiency_3m >= 0.549`), keep CALL C2 (`break_distance_pct <= 0.00086`), and replace the old PUT P1-P9 package with R-PUT (`call_chain_volume >= 18800 AND break_distance_orb_ratio <= 0.287`). Under the prior CALL 170k / PUT 11k Entry, Entry + C2 + R-PUT replayed at **+$14,311** versus **+$5,491** for the old live stack. With the current CALL 110k / PUT 11k Entry, the same current skip stack replays at **+$18,456**.

##### CALL skip — Research app config (10/15, local; deployed 00600-fpn still runs C2)

**2026-10-15 (local, Research app, not deployed):** zero-win Skip Signals. CALL none; PUT `break_distance_pct >= 0.055`; C2 and R-PUT retired. Blank, unset or commented-out `QG_*` keys are off (`_env_float_opt` → None; no code default). The 18 dormant pockets listed as Off below must stay off: if configured they reject on missing data and cut winners. The app admits on the Trade Profile (wide / 10.5); the Skip runs after the chain and before the contract pick.

Skip **CALL** if any pocket matches. PUT always n/a. `GATE_call_ss` = **`hard`**. Missing does not skip.

| # | Skip if | Env keys | Live | Hydrate field | What it is |
|---|---------|----------|------|---------------|------------|
| 1 | **none** — every CALL key blank/unset → `unconfigured`; deep CALL skip `break_distance_pct >= X` is **off** | `QG_CALL_SS_BREAK_DIST_GE` | **blank (off)** | `break_distance_pct` | 2026-10-15 zero-win (Eric). No CALL level cuts zero wins and still blocks meaningful loss. |
| ~~2~~ | ~~break_distance_pct ≤ 0.00086~~ (C2) | `QG_CALL_SS_BREAK_DIST_MAX` | **commented out in Shared.env — not set (retired 10/15)** | `break_distance_pct` | LEGACY 2026-09-27. Cut 2 winning 10.5 CALLs / 11 Total winners. |

Off (keys omitted): `QG_CALL_SS_PVE_3M_MIN` / C1 (`price_volume_efficiency_3m >= 0.549`), RSI+cumulative volume, spread+small chain, put chain ≤ 435, volume_5m ≤ 24183, dir_pct_change_5m ≥ 0.21953, relative_volume_time_adjusted ≤ 0.441022, representative_ask ≤ 0.05.

##### PUT skip — Research app config (10/15, local; deployed 00600-fpn still runs R-PUT)

Skip **PUT** if any pocket matches. CALL always n/a. `GATE_put_ss` = **`hard`**. `GATE_path_ss` = **`off`**. Missing does not skip.

| # | Skip if | Env keys | Live | Hydrate field | What it is |
|---|---------|----------|------|---------------|------------|
| 1 | break_distance_pct **≥ 0.055** | `QG_PUT_SS_BREAK_DIST_GE` | **0.055** | `break_distance_pct` | 2026-10-15 zero-win (Eric). Above the max PUT winner (0.05456) on the 10.5 and Total books. Missing / NaN admits. Reason `skip_signal:put:break_dist_ge` (detail `break_dist_ge_…`). |
| ~~2~~ | ~~call_chain_volume ≥ 18800 AND break_distance_orb_ratio ≤ 0.287~~ (R-PUT) | `QG_PUT_SS_CALL_CHAIN_MIN` · `QG_PUT_SS_BREAK_ORB_RATIO_MAX` | **commented out in Shared.env — not set (retired 10/15)** | — | LEGACY 2026-09-27. Cut 36 winning 10.5 PUTs / 171 Total winners. |

Off (keys omitted): old PUT P1-P9 package (`QG_PUT_SS_TOTAL_CHAIN_OI_MAX`, RSI/chain, RSI/IV, SPY, theta, call-delta/theta, abs VWAP, dir/theta, VWAP ATR), plus volume_1m ≤ 11763 AND call_chain_open_interest ≤ 21037.

##### Prior Absolute stamps (not live — do not re-enable omitted keys)

| Stamp | When | Notes |
|-------|------|-------|
| CALL-SS-1724 / PUT-SS-1724 | Aug 27 | W40 6-day. `QG_CALL_SS_SVA_MAX` · `QG_CALL_SS_IV_HIGH` · `QG_CALL_SS_DIR_MAX` · `QG_CALL_SS_VWAP_ABS_MIN` · `QG_CALL_SS_RSI_MIN` / PUT dir-RSI / thin-vol. |
| CALL-SS-PC918 / PUT-SS-PC918 | Sep 20 | 8.24–9.18 PE100. CALL c1–c5 (`QG_CALL_SS_VWAP_MIN` · `QG_CALL_SS_CHAIN_MAX` · `QG_CALL_SS_L1D5_MAX` · `QG_CALL_SS_SAME_SIDE_MAX` · `QG_CALL_SS_SECONDS_MIN`). PUT p1–p5. |
| CALL-SS-PC921-C / PUT-SS-PC921-C | Sep 21 afternoon | PC918-safe + STRUCTURAL-4 / ABS-NOCLOCK-5. Dropped CALL c2 and PUT p1. Extra keys (`QG_CALL_SS_SAME_SIDE_5M_AGO_MAX` · `QG_PUT_SS_ATR_MAX` · `QG_PUT_SS_PVE1_MAX` · PUT `QG_PUT_SS_ASK_MAX`, …) **omitted** from PRUNED-8/8. Freeze `ABS-SS-STRUCT-AE-922` historical. |

Re-adding omitted keys expands the knife beyond the 9.21 freeze. Detail: [0DTESkipSignal.md §5.5–5.6](SkipSignal/0DTESkipSignal.md).




<a id="trade-profile-decision-freeze-oct-7-2026"></a>
### Trade Profile decision freeze (Oct 7 2026)

**Added 10/7/2026.**
- **What it is.** Time-correct data for the Trade Profile accept/reject decision, and the matching handoff snapshot. The full design is in [Data.md §5.3](Data.md#53-time-correct-data-for-trade-profile-decisions).
- **Settings.** Eric's 10/7 17:25 PT settings are the code defaults: strict 5m window, default budgets, stale policy `defer`. The missing-key rule is unchanged.
- **Where to set them.** None of these keys is in `configs/*.env` or `config_manifest.yaml`. The defaults below come from the code (`central_tech/decision_freeze.py`, `central_tech/chain_ring.py`, `modules/tp_decision_record_store.py`). An override goes on the Cloud Run env with `--update-env-vars`.
- **Read with `os.environ.get`.** They are read on every call, so an override applies without a module reload. Because they bypass the `os.getenv` hook, a missing key does **not** log `CONFIG_MISSING_KEY`.
- **Bad values.** Numbers fall back to the default when the value is not numeric. A floor is applied where shown.

| Key | Default (code) | Meaning | Rollback / tuning use |
| --- | --- | --- | --- |
| `TP_FREEZE_ENABLED` | `true` | Master switch for the age checks, targeted refreshes, carried-key clearing, the carry rule and stale defers (TL/BO `confirm_chain_skew_before_execution` and the ORB1 watch). `0` / `false` / `no` / `off` disable it. | `false` brings back the pre-10/7 gate data flow. These stay on: the freeze object (values and `gate_at`, no ages), handoff parity, decision records, null `entry_premium`, the handoff link, the 45 s SPY fallback and the ratio locks. |
| `TP_FREEZE_CHAIN_MAX_AGE_SEC` | `60` (floor 5) | Maximum decision-chain age. An older cache inside the reuse window (300 s, or 600 s while a position is open) gets one budgeted live refresh. A cache **younger than 60 s with no two-sided bid/ask** also `force_live` (`reason=no_two_sided_quotes`) — same Demo/Live door. | Raise it to cut age refreshes. Empty-book live fetch is not this budget. |
| `TP_FREEZE_QUOTE_MAX_AGE_SEC` | `120` (floor 2) | Maximum underlying L1 quote age, by L1 ring receipt time. The scan quote is the decision quote for this long, so Trade Profile does not call `get_quotes` once per name. Serving image `00618-cvb`. | Raise it to cut quote refreshes. A 45s cap re-quoted the whole window (Oct 29, one task 486s). |
| `TP_FREEZE_SPY_MAX_AGE_SEC` | `45` (floor 5) | Maximum SPY pin age (`spy_change_pct`) | — |
| `TP_FREEZE_SESSION_TECH_MAX_AGE_SEC` | `90` (floor 10) | Maximum age of the symbol's last 1m bar upsert (`atr`, `rsi`, `distance_from_vwap_atr`, `price_volume_efficiency_3m`). Also the carry cap: a carried indicator is restored only when `seconds_since_break` is at or below this value. | — |
| `TP_FREEZE_5M_STRICT` | `true` | Strict 5m prior for `dir_pct_change_5m`: a ring sample within horizon ± tolerance, otherwise missing (no oldest-sample fallback). Read in `central_tech/chain_ring.py`. | `false` brings back the old selection (nearest within max(90 s, 0.75 × horizon), otherwise the oldest sample). Eric kept it `true` on 10/7. |
| `TP_FREEZE_5M_HORIZON_SEC` | `300` | Defined in code (`prior_5m_horizon_sec()`) but **not read by any path today**. The 5m horizon is the fixed 300 s `horizon_sec` default of `central_tech/chain_ring.derive_chain_trajectory_features`. | No effect. Do not set it. |
| `TP_FREEZE_5M_PRIOR_TOL_SEC` | `90` (floor 10) | Strict-window tolerance, so 300 ± 90 = 210–390 s. | Widen it so more signals have a 5m prior. Since 10/9 t146u no ORB1/TL0 cell gates `dir_pct_change_5m` (TL0 PUT line removed), so this no longer causes `missing_dir_pct_change_5m` rejects. |
| `TP_FREEZE_CHAIN_REFRESH_BUDGET_SESSION` | `300` | Extra (`tp_age_cap`) chain refreshes allowed per process per PT day | Budget. The 10/7 replay used about 206 (p95 225). |
| `TP_FREEZE_CHAIN_REFRESH_PER_MIN` | `8` | Extra chain refreshes per rolling 60 s (each one is 2 real broker calls) | Budget |
| `TP_FREEZE_QUOTE_REFRESH_BUDGET_SESSION` | `300` | Targeted `get_quotes([symbol, SPY])` calls per process per PT day | Budget. The 10/7 replay used about 249 (p95 272). |
| `TP_FREEZE_QUOTE_REFRESH_PER_MIN` | `10` | Targeted quote calls per rolling 60 s | Budget |
| `TP_FREEZE_STALE_POLICY` | `defer` | What happens when the budget is exhausted, or a gated source is still over its cap: `defer` holds the break (`required_data_not_ready`); `reject` rejects (`tp_freeze_budget_exhausted` / `tp_freeze_stale_source`). Any other value means `defer`. | Eric chose `defer` on 10/7. |
| `TP_FREEZE_REFRESH_DEDUPE_SEC` | `20` (floor 1) | Defined in code (`refresh_dedupe_sec()`) but **not read by any path today**. Dedupe is the per-symbol lock plus the age re-check. | No effect. Do not set it. |
| `TP_DECISION_RECORD_ENABLED` | `true` | Append one JSON decision record per TP evaluation | `false` turns records off. |
| `TP_DECISION_RECORD_DIR` | `<app>/tmp/tp_decision_records` | Folder for the daily local file `{YYYY-MM-DD PT}.jsonl`. Under pytest the default is the system temp folder. | — |
| `TP_DECISION_RECORD_GCS` | `true` | Upload the daily file to `{ns}/profit_optimizer/tp_decision_records/{day}/{HHMMSS_pid}.jsonl`. It also needs `GCS_PERSISTENCE_ENABLED`. | `false` keeps the files local only. |
| `TP_DECISION_RECORD_UPLOAD_SEC` | `300` (floor 30) | Minimum seconds between uploads. Checked on the next append; there is also a flush at exit. | — |

**Fixed values that are not env keys:**
- **SPY fallback cap.** `central_tech/spy_change.py` caps the discovery-quote SPY fallback at `spy_max_age_sec()` (**45 s**). Quote reuse reads `quote_max_age_sec()` (**120 s**). Same table as the gate.
- **OPTION_API hard-cap check.** No extra refresh runs while `_options_api_hard_cap_extra_sec > 0` (140 real calls/min).

**Expected cost** at 10/7 volumes: ≈ 660 extra real broker calls per session (+27% over 2,418). The busiest minute rises to about 67/min, against the 140/min cap.

**Two keys missing on revision `00591-d2g`.** These keys are not set in any `configs/*.env` file or in the service env. They are read through `os.getenv`, so 00591 logged `CONFIG_MISSING_KEY | key=… | fallback_used=true` and used the code defaults:

| Key | Code default (`modules/session_tech_warmup.py`) | Effect of the fallback |
| --- | --- | --- |
| `CHAIN_RING_PREWARM_ENABLED` | `true` | The chain-ring pre-warm runs at 06:40 / 06:50 / 07:00 / 07:26 / 07:33 PT (t168u; was 06:36 / 06:53 / 07:00). |
| `SESSION_TECH_WARMUP_PRIOR_SESSION` | `true` | The prior-session 1m warm-up runs from 06:00 PT. |

Both fallbacks are the intended values, so behavior is correct. The warning is cosmetic until the keys are added to `Shared.env`.

---

## ⚙️ **Core Configuration Files**

### **1. Broker Configuration** (`configs/Data.env`) ⭐ **PRIMARY**

**Purpose**: Centralized broker and account selection for all strategies

**Key Settings**:
```bash
# Canonical broker (Shared.env — preferred)
# MODE=demo
# BROKER=etrade

# Legacy fallback (Data.env)
BROKER_TYPE=etrade  # etrade | interactive_brokers | schwab | tastytrade

# Strategy-specific broker overrides (optional)
# ORB_BROKER_TYPE=etrade
# 0DTE_BROKER_TYPE=etrade

# E*TRADE Account Configuration
ETRADE_ORB_ACCOUNT_ID=<see PrivateSecrets.md>       # ORB SO (Demo research)
ETRADE_0DTE_ACCOUNT_ID=<see PrivateSecrets.md>     # ORB1 Breakout + Trendline + Breakout Live options
ETRADE_DEFAULT_ACCOUNT_ID=<see PrivateSecrets.md>   # Fallback

# Options approval (documented defaults — see options_level_audit.py)
# REQUIRED_OPTIONS_LEVEL=2
# REQUIRED_OPTIONS_LEVEL_LONG_CALLS=2
# REQUIRED_OPTIONS_LEVEL_LONG_PUTS=2
# REQUIRED_OPTIONS_LEVEL_DEBIT_SPREADS=3
# REQUIRED_OPTIONS_LEVEL_CREDIT_SPREADS=3
```

**Account Selection Priority**:
1. Strategy-specific account (e.g., `ETRADE_ORB_ACCOUNT_ID`)
2. Default account (e.g., `ETRADE_DEFAULT_ACCOUNT_ID`)
3. Auto-select first available active account

**Integration**: Used by `BrokerConfigManager` (`modules/broker_config_manager.py`) for centralized broker and account selection.

### **Execution policy (optional — May 15, 2026)**

**Purpose**: Order-time execution quality (fills, slippage, exit routing) without changing signal engines. Read via `get_config_value` / `os.getenv` in `modules/execution_routing.py` and wired paths.

| Key | Default | Role |
|-----|---------|------|
| `USE_MARKET_ORDERS` | `false` | Enables smart execution ladders. 0DTE option closes still use `resolve_options_exit_plan()` by reason: urgent risk/data/EOD → MARKET; profit/trail/breakeven/no_progress → LIMIT ladder. |
| `ENABLE_SMART_EXECUTION` | `true` | Smart entry/equity path allowed only when `USE_MARKET_ORDERS=false`; option-close urgency routing remains active either way. |
| `EXEC_LAST_LOOK_MAX_SPREAD_PCT` | `2.5` | Last-look spread cap; opening profile can widen internally. |

**Modules:** `execution_telemetry.py`, `execution_routing.py`, `smart_equity_execution.py`, `execution_profiles.py`, `execution_fill_reconcile.py`. **Grep:** `EXECUTION_FILL_SUMMARY`, `EXECUTION_AGGRESSION_ESCALATED`, `EXECUTION_FILL_RECONCILED`. Full table: [0DTEORB1Breakout.md](0DTEORB1Breakout.md) and [May 15 session](doc_elements/Sessions/2026/May15%20Session/SESSION_SUMMARY_MAY15_2026.md).

### **2. Deployment / cloud shell** (`configs/Data.env` + `configs/Shared.env`)

**Purpose**: Broker defaults, GCP/GCS persistence keys, and cross-path feature flags. **Phase 2 trim (Jun 18):** removed legacy cache/provider rows and container sizing keys from `Data.env` — Cloud Run sizing is deploy-script owned.

**Key Settings**:
```bash
# Deployment Mode
DEPLOYMENT_MODE=demo  # demo or live

# Broker Configuration
BROKER_TYPE=etrade
BROKER_DATA_ONLY=true  # All data from configured broker only

# Google Cloud Configuration (see CloudSecrets.md for actual values)
GCP_PROJECT_ID=your-project-id
GCP_REGION=us-central1
GCP_ZONE=us-central1-a
GCP_SERVICE_NAME=your-service-name
GCS_BUCKET_NAME=your-project-id-data
GCS_PERSISTENCE_ENABLED=true
```

### **3. Base / app-wide configuration** (`configs/Shared.env`, `configs/Data.env`, `configs/Risk.env`)

**Purpose**: Core system settings merged into the seven canonical files. **`OPTION_STEALTH_*`** (generic) and cross-path orchestration live in **`Shared.env`** / **`Risk.env`**; Trendline-specific keys live in **`Trendline0DTE.env`** (see below).

**Key Settings** (representative):
```bash
# System Identity
PROJECT_NAME=Easy ORB Strategy
VERSION=2.0
ENVIRONMENT=development  # development or production

# Trading Mode (canonical — Shared.env §8)
MODE=demo    # demo | live
BROKER=etrade
TRADING_MODE=demo  # legacy sync key; MODE is authoritative

# Performance Optimization
MAX_WORKERS=8
BATCH_SIZE=20
CACHE_TTL_SECONDS=60
MEMORY_LIMIT_MB=2048
POLL_SECONDS=1.0

# Position Monitoring
POSITION_MONITORING_INTERVAL=30  # 30-second monitoring

# Market Hours
TZ=America/New_York
PREP_START_ET=08:30
RTH_OPEN_ET=09:30
RTH_CLOSE_ET=16:00
AFTER_HOURS_END_ET=20:00

# Holidays
HOLIDAYS_ENABLED=true
HOLIDAYS_CUSTOM_PATH=data/holidays_custom.json

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_TO_STDOUT=true
LOG_ROTATION=true
LOG_MAX_SIZE_MB=100
```

`dynamic_holiday_calculator.py` is the canonical policy engine. `HOLIDAYS_CUSTOM_PATH` remains an override/static input path, while runtime decisions come from `should_skip_trading()` and include `MARKET_CLOSED`, `LOW_VOLUME`, and `PRE_HOLIDAY` sessions. Full calendar, clocks, and 2026 skip table: [MarketCalendar.md](MarketCalendar.md).

### **4. Strategy Configuration** (`configs/Shared.env` + path `.env` files)

**Purpose**: Cross-path orchestration in **`Shared.env`**; **capital %** and **ORB SO schedule** in **`ORBSO.env`**; **ORB1 Breakout** in **`ORB0DTE.env`**; **Trendline 0DTE** in **`Trendline0DTE.env`**.

**Key Settings** (illustrative — live **`SO_CAPITAL_PCT` / `ORR_*` / `CASH_RESERVE_PCT`** are in **`ORBSO.env`**):
```bash
# Strategy Modes
STRATEGY_MODES=standard,advanced,quantum
DEFAULT_STRATEGY_MODE=standard
# Path enablement: ENABLE_ORB_STRATEGY (ORBSO.env) · ENABLE_0DTE_STRATEGY · ENABLE_TRENDLINE_STRATEGY

# Capital Allocation (CRITICAL: Must sum to 100%)
SO_CAPITAL_PCT=90.0      # Standard Order allocation (90%)
ORR_CAPITAL_PCT=0.0      # Opening Range Reversal (disabled)
CASH_RESERVE_PCT=10.0    # Cash reserve (auto-calculated)

# Standard Strategy Settings
STANDARD_TARGET_WEEKLY_RETURN=0.01
STANDARD_BASE_RISK_PER_TRADE=0.02
STANDARD_MAX_RISK_PER_TRADE=0.05
STANDARD_MIN_QUALITY_SCORE=60
STANDARD_MIN_CONFIDENCE_SCORE=0.9
STANDARD_POSITION_SIZE_PCT=10.0

# Advanced Strategy Settings
ADVANCED_TARGET_WEEKLY_RETURN=0.10
ADVANCED_BASE_RISK_PER_TRADE=0.05
ADVANCED_MAX_RISK_PER_TRADE=0.15
ADVANCED_MIN_QUALITY_SCORE=70
ADVANCED_POSITION_SIZE_PCT=20.0

# Quantum Strategy Settings
QUANTUM_TARGET_WEEKLY_RETURN=0.50
QUANTUM_BASE_RISK_PER_TRADE=0.10
QUANTUM_MAX_RISK_PER_TRADE=0.25
QUANTUM_MIN_QUALITY_SCORE=80
QUANTUM_POSITION_SIZE_PCT=30.0
```

**Capital Allocation Rules**:
- `SO_CAPITAL_PCT + ORR_CAPITAL_PCT + CASH_RESERVE_PCT = 100%`
- Current: 90% SO, 0% ORR, 10% Reserve
- Adjust `SO_CAPITAL_PCT` to change overall capital deployment

---

## 📊 **Strategy Configuration**

### **ORB Strategy Configuration**

**Location**: `configs/ORBSO.env`, `configs/ORB0DTE.env`, `configs/Trendline0DTE.env`, `configs/Shared.env`, `configs/Risk.env`

**Key Settings**:
- **Capital Allocation**: 90% SO, 10% Reserve (configurable)
- **ORB SO ETF @ 7:30**: up to **15** simultaneous SO executions / greedy sizing divisor — `MAX_CONCURRENT_TRADES` in **`configs/ORBSO.env`** (read by `prime_risk_manager` SO sizing). Not the same knob as ORB1 Breakout or Trendline.
- **ORB1 Breakout ORB1 watch / funnel**: max **50** (RESEARCH) — `ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX` in **`configs/ORB0DTE.env`** (legacy `ORB_0DTE_BATCH_MAX_POSITIONS` · `0DTE_MAX_POSITIONS`)
- **ORB1 Breakout Positions** (account open cap): **50** — `ORB_0DTE_MAX_POSITIONS` in **`configs/Account0DTE.env`**
- **Trendline 0DTE Positions**: max **50** — `TRENDLINE_0DTE_MAX_POSITIONS` in **`configs/Account0DTE.env`** (alias `TRENDLINE_MAX_OPEN_POSITIONS` in `Trendline0DTE.env`)
- **Total 0DTE Positions** (ORB + Trendline + Breakout): `TOTAL_0DTE_MAX_POSITIONS=50` in **`configs/Account0DTE.env`** (shared slot pool; legacy alias `MAX_TOTAL_OPTION_POSITIONS`)
- **Monitor symbols**: `0DTE_MONITOR_MAX_SYMBOLS=50` in **`configs/Account0DTE.env`**
- **Risk manager ceiling** (count of tracked strategy positions, all paths): `MAX_OPEN_POSITIONS` — default **70** in **`configs/Risk.env`** so SO + 0DTE + Trendline maxima do not hit the generic limiter before path-specific caps.
- **Entry Window**: 7:15-7:30 AM PT (SO signals)
- **Execution Time**: 7:30 AM PT
- **Watchlist**: `data/watchlist/core_list.csv` (~**91** symbols; dynamic — leveraged ETFs plus dual-listed spot names for ORB SO). **`0dte_list.csv`** (~**80** symbols). **May 26:** **TOL** removed from both lists (operator request — no future trades).
- **EOD flatten window (PT)** — **`configs/Shared.env` §5** (`EOD_FLATTEN_START_PT` / `EOD_FLATTEN_END_PT`, default **12:50** / **12:51**, end exclusive). Alias `SO_ETF_EOD_CLOSE_*` still read. Applies to **`flatten_all_paths_for_eod_scheduler()`**. Not the 12:30 no-new-entries cutoff. After 12:30, `mfe_trail` keeps running until flatten.
- **EOD Telegram (PT)** — Cloud Scheduler job `end-of-day-report` (`55 15 * * 1-5` America/New_York → **12:55 PT**) `POST /api/end-of-day-report` is the **only** sender of unified `🏦 END-OF-DAY 0DTE`. The trading loop does not send this Telegram. `EOD_REPORT_*` clocks document that job; GCS marker skips scheduler retries.

**Priority rank (current — step 3 SO · step 5 0DTE):**

**ORB SO** (`modules/orb_priority_score.py` → `calculate_so_priority_score()`):

```text
base = 0.36 × continuation_quality
     + 0.64 × (0.24×VWAP + 0.18×RS + 0.28×vol + 0.10×conf + 0.14×RSI + 0.06×orb_range)
priority_score = base × quality_multiplier − stack_penalties → max(0, …)
```

Config: `configs/ORBSO.env` — `SO_CONTINUATION_MOMENTUM_WEIGHT=0.36`, `SO_ENTRY_*`, `SO_SKIP_ZERO_PRIORITY_SCORE`, `SO_MIN_PRIORITY_SCORE_FOR_EXECUTION=0.001`

**Portfolio Protection Gate (step 4 SO · step 6 0DTE) — `configs/ORBSO.env`:**

```bash
# Unified 7:30 batch protection (modules/portfolio_protection.py)
ENABLE_PORTFOLIO_PROTECTION=true
ENABLE_PORTFOLIO_PROTECTION_DAY=true
PORTFOLIO_PROTECTION_VWAP_BELOW_PCT=50          # Trigger A: % pool below VWAP
PORTFOLIO_PROTECTION_MEDIAN_VOLUME_MAX=0.45     # Both triggers: weak median vol
# Split/chop tape (Rev 00375 — Trigger B)
ENABLE_PORTFOLIO_PROTECTION_SPLIT_TAPE=true
PORTFOLIO_PROTECTION_MEDIAN_VWAP_MIN_PCT=2.0    # Skip when median vwap_distance < 2%
PORTFOLIO_PROTECTION_VWAP_COVERAGE_MIN_PCT=80    # Suppress split-tape when VWAP sparse
PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT=80  # Defer PP evaluate when <80% pool has real volume_ratio
PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false       # Observe-only: evaluate + OBSERVE ONLY alert; no execution block
```

**Step 5 GCS marker (Jun 26 local):** evaluating instance writes `daily_markers/portfolio_protection_step5/{YYYY-MM-DD}.json` (block state, trigger_id, metrics, `volume_coverage_pct`). Other instances hydrate via `try_hydrate_pp_from_gcs()` before SO / 0DTE execution. Zero-fill alert path retries via `ensure_pp_ready_before_execution(context="zero_exec_alert")`. Greps: `PORTFOLIO_PROTECTION | audit` · `volume_coverage_insufficient` · `context=zero_exec_alert`.

**Registry replay:** May 18/27 (Trigger A) · May 26 chop (Trigger B) · Jun 1 momentum (pass). See [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md#portfolio-protection-day-gate) (gate spec) · [gate replay](PortfolioProtectionGuide.md#gate-replay-key-sessions).

**ORB1 Breakout pipeline @ 7:30** (`listen_to_orb_signals`, Rev **00374** + Jun 2026 Hard Gate reorder):

```text
Convex → Hard Gate → priority rank → Gate 3c (PS floor ★ + shallow + cap 10)
```

| Side | Score source |
|------|----------------|
| **CALL** | Stamped `so_priority_score` @ 7:30 (00373) or `calculate_so_priority_score()` |
| **PUT** | `calculate_so_put_priority_score()` (inverted tape) |

Gate **3c** runs **after** Hard Gate + rank: PS floor **≥ 0.30** (canonical ★ spec) + E14 shallow symmetric + cap **`ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX=50`** (RESEARCH). Legacy **00352** blend via `0DTE_PRIORITY_USE_SO_RANK=false` — weights **`0DTE_PRIORITY_RANK_W_*`**.

Detail: [SORisk.md](SORisk.md) · [0DTEORB1Breakout.md §11](0DTEORB1Breakout.md#11-priority-ranking-at-730)

### **Easy 0DTE Configuration**

**Location:** Integrated trading app — **`configs/Account0DTE.env`** (slots / caps / health) · **`configs/ORB0DTE.env`** (Convex, Hard Gate, funnel, entry δ) · stealth in **`configs/Shared.env` §2b** (RESEARCH=FINAL). Standalone package reference: `easy0DTE/configs/0dte.env` (not the primary desk source).

**Position sizing / caps** (edit `Account0DTE.env` first — keep all **50**s aligned on RESEARCH):

```bash
0DTE_SLOT_COUNT=50
0DTE_TRADING_CAPITAL_PCT=90
ORB_0DTE_MAX_POSITIONS=50
TRENDLINE_0DTE_MAX_POSITIONS=50
TOTAL_0DTE_MAX_POSITIONS=50
0DTE_MONITOR_MAX_SYMBOLS=50
ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX=50
0DTE_RANK_SIZING_ENABLED=false
# Legacy funnel aliases (ORB0DTE.env) — keep =50
0DTE_MAX_POSITIONS=50
0DTE_MAX_EXECUTION_CANDIDATES=50
```

**Enablement + Convex / Hard Gate / single-leg** (`ORB0DTE.env` + mode from Shared):

```bash
ENABLE_0DTE_STRATEGY=true
MODE=demo
BROKER=etrade
DEPLOYMENT_MODE=demo

# Quality Gate (ORB admit_pre_exec + shared helpers) + session tech (Jul 16 / Aug 5 absorb)
QUALITY_GATE_STACK_SHADOW=true
QUALITY_GATE_STACK_CUTOVER=true
0DTE_CONVEX_ORB_RANGE_MIN=0.25
0DTE_CONVEX_REGIME_ORB_MULT=1.0
0DTE_CONVEX_V25_SYMMETRIC_ENABLED=true
0DTE_CONVEX_MACD_DISAGREE_EPS=99
0DTE_QG_MACD_DEADBAND_QUALITY_ENABLED=true
0DTE_QG_MACD_DEADBAND_ORB_MAX_PCT=1.0
0DTE_QG_MACD_DEADBAND_MACD_ABS_MIN=0.60
0DTE_QG_MACD_DEADBAND_VOL_MAX=0.20
0DTE_CONVEX_SESSION_TECH=true
0DTE_CONVEX_VWAP_EXTENSION_REJECT_PCT=15.0
0DTE_CONVEX_ORB_RANGE_MAX_PCT=8.0
0DTE_CONVEX_MIN_SCORE=0.75
CALL_RECHECK_BREAKOUT_MULTIPLIER=1.000
PUT_RECHECK_BREAKDOWN_MULTIPLIER=1.000
# Hard Gate vol floors (aligned with TP Wide equity_volume_ratio 0.012)
0DTE_HARD_GATE_ALIGN_CONTINUATION_VOL=true
0DTE_HARD_GATE_CONTINUATION_VOL_FLOOR=0.012
0DTE_HARD_GATE_BREAKDOWN_VOL_FLOOR=0.012
# Jun 22 — single-leg opening premium relax (symmetric CALL/PUT).
# Shared.env 0DTE_MAX_ENTRY_PREMIUM is the hard cap when set.
0DTE_SINGLE_LEG_OPENING_PREMIUM_RELAX=true
0DTE_SINGLE_LEG_OPENING_MOMENTUM_MIN=50.0
0DTE_SINGLE_LEG_OPENING_ORB_RANGE_MIN=0.35
# Single-leg primary (0DTE_ORB_LOTTO_FIRST = legacy env name)
0DTE_ORB_LOTTO_FIRST=true
0DTE_ORB_SPREAD_PRIMARY_ENABLED=false
0DTE_ORB_SPREAD_FALLBACK_ENABLED=false
# Delta viability (deployed 00381 — sync to Cloud Run)
0DTE_DELTA_VIABILITY_STEP_UP_MIN=0.15
0DTE_DELTA_VIABILITY_MOONSHOT_MOMENTUM=85.0
0DTE_PRIORITY_USE_SO_RANK=true
0DTE_SHALLOW_E14_ENABLED=false
0DTE_SHALLOW_CALL_MACD_MIN=2.0
0DTE_SHALLOW_PUT_MACD_MAX=-2.0
0DTE_SKIP_ZERO_PRIORITY_SCORE=true
0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION=0.30
0DTE_STRATEGY_SO_DEBIT_SPREAD_MIN_PS=0.17

# Rev 00352 — execution priority (legacy when 0DTE_PRIORITY_USE_SO_RANK=false)
0DTE_PRIORITY_RANK_W_BREAKOUT_CONT=0.30
0DTE_PRIORITY_RANK_W_VWAP_CONT=0.24
0DTE_PRIORITY_RANK_W_SO_ALIGN=0.24
0DTE_PRIORITY_RANK_W_VOLUME=0.18
0DTE_PRIORITY_RANK_W_CONFIDENCE=0.08
0DTE_PRIORITY_SO_TOP_TIER=0.98
0DTE_PRIORITY_SO_TOP_TIER_MULT=1.10
# Rev 00352 — geometry tier + Tier-A CALL Convex exception (deployed 00348)
0DTE_PRIORITY_TIER_A_GEOMETRY_MULT=1.12
0DTE_PRIORITY_TIER_B_GEOMETRY_MULT=0.88
0DTE_CONVEX_TIER_A_CALL_EXCEPTION=true
0DTE_CONVEX_TIER_A_SO_PRIORITY_FLOOR=0.50

# Debit Spread Settings — 0DTE_DEBIT_SPREAD_TARGET_DELTA_* removed Jun 18 (no runtime readers)

# Target Symbols
# Loaded dynamically from data/watchlist/0dte_list.csv (current full universe),
# not hardcoded in config.
# 0DTE_TARGET_SYMBOLS is optional override for special testing only.

# Account Configuration (Rev 00245)
# Managed by merged config (primarily Data.env + secretsprivate/secret manager)
# See PrivateSecrets.md for actual account ID values
# Backward compatible: Also checks 0DTE_ETRADE_ACCOUNT_ID

# Execution quality guardrails (Rev 00319)
0DTE_MIN_RISK_REWARD=0.20
0DTE_MIN_MAX_PROFIT_PER_SPREAD=0.15
0DTE_MAX_DEBIT_TO_WIDTH_PCT=0.92
```

**ORB1 Breakout Priority Score (PS v2.1 — default):** **[0DTEPriorityScore.md](0DTEPriorityScore.md)** · summary [0DTEORB1Breakout.md — Priority Score](0DTEORB1Breakout.md#priority-score-ps-v21) (`0DTE_PRIORITY_USE_PS_V2=true`).

**Legacy SO Priority Score Formula** (v1.1 - Rev 00246 — SO / SO-mirror fallback):
- Breakout: **35%** (↑ from 30%)
- Range: **30%** (↑ from 25%)
- Volume: **20%** (same)
- Eligibility: **15%** (same)
- RS vs SPY: **REMOVED** (not relevant for 0DTE options)
- Momentum: **REMOVED** (redundant with breakout score)

**Red Day Filtering** (current policy):
- **LONG (CALL) non-Tier-1 trades**: Rejected on Red Days
- **LONG (CALL) Tier-1 trades**: Allowed on Red Days
- **SHORT (PUT) trades**: Allowed and encouraged on Red Days

**Integration with ORB Strategy**:
1. ORB1 Breakout watches ORB#1 after TL0 is ready, with discovery ending at 07:25 PT.
2. Trendline runs TL0 with ORB1, then TL1/TL2/TL3/TL4 after 07:30 PT.
3. ORB2 Breakout watches the first-hour high/low after 07:30 PT.
4. Each path uses the same five-step spine: **Structure → Quality Gate → Confirm → PO Entry Snapshot → Enter Trade**.
5. Filled options share the unified Profit Capture monitor and E*TRADE Live close path.

**0DTE selector threshold defaults** (env-driven loose-start profile):
```bash
0DTE_STRONG_MOMENTUM_MIN=70.0
0DTE_STRONG_BREAKOUT_DISTANCE_RATIO_MIN=0.08
0DTE_STRONG_MIN_CONFIDENCE=0.72
0DTE_STRONG_MIN_VOLUME_RATIO=1.05

0DTE_MODERATE_MOMENTUM_MIN=55.0
0DTE_MODERATE_BREAKOUT_DISTANCE_RATIO_MIN=0.02
0DTE_MODERATE_MIN_CONFIDENCE=0.58
0DTE_MODERATE_MIN_VOLUME_RATIO=0.90

0DTE_WEAK_MOMENTUM_MAX=50.0
0DTE_WEAK_BREAKOUT_DISTANCE_RATIO_MAX=0.015
0DTE_WEAK_MIN_CONFIDENCE=0.55
0DTE_NEAREST_EXPIRY_MAX_DAYS=10
0DTE_EXPIREDATE_RESOLVE_FIRST=weekday
0DTE_EMPTY_SAME_DAY_TTL_SEC=2700
0DTE_EMPTY_EXPIRY_TTL_SEC=14400
0DTE_NEAREST_EXPIRY_MEMO_TTL_SEC=14400
0DTE_EXPIRE_DATES_TTL_SEC=14400
```

**0DTE chain data policy (`configs/ORB0DTE.env` + `configs/Shared.env`):**

| Key | Repo value | Role |
|-----|------------|------|
| `REQUIRE_LIVE_OPTION_DATA` | `true` | Fail-closed when live chain unavailable at entry |
| `0DTE_DEMO_SYNTHETIC_CHAIN` | `false` | Prefer E*TRADE chain; spot-based synthetic is fallback only |
| `OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA` | `true` | Stealth prefers live marks for HWM / floors |

**0DTE chain request + expiry (single door):**
- Source of truth is ``data/watchlist/0dte_list.csv`` (all tiers = 0DTE-intent). There is no SPY/QQQ/IWM-only “native” hardcode.
- **Central owner:** `fetch_0dte_intent_chain` is the only app door. `chain_fetch_manager` queues discovery/dir%; `OptionsChainManager` owns expiry + cache; `ETradeOptionsAPI` is HTTP only. PrimeDataManager does not fetch chains. ORB SO is equity-only.
- **Aug 4 API:** `get_option_chains` must pass **`expiryYear` / `expiryMonth` / `expiryDay`** + **`noOfStrikes`** (not ignored `expiryDate` / `strikeCount`). Per-leg stamp: OCC osiKey → Product `expiryYear/Month/Day` → requested session day; SelectedED is advisory only.
- Same-day US/Eastern expiry is the default **when listed**. `0DTE_EXPIREDATE_RESOLVE_FIRST=weekday` (default): Mon–Thu one `optionexpiredate` list, then **one** chain for today if present else the earliest later listed day. Friday keeps same-day-first (`never` forces that every day; `always` lists first every day).
- If same-day is empty: harvest the nearest listed expiry already on the response (2DTE / 3DTE / 4DTE / weekly). Else one `optionexpiredate` list (`GET /v1/market/optionexpiredate`) then **one** chain fetch for the earliest listed day within `0DTE_NEAREST_EXPIRY_MAX_DAYS` (default 10). Weekends and known-empty days are never probed. Never reject a signal solely because same-day is absent when a nearer listed day exists. **Never remap** that later payload onto session today when SelectedED is blank or equals the request — that stamped Monday OCC on a Friday weekly and caused E*TRADE `1002` on the 2s tick (Nov 2 CSCO/RTX). Keep `contract.expiry` / fill OCC / quote wire on `selected_expiry`.
- Session memos (`modules/option_expiry_memo.py`): 10031 / empty days (same-day default Mon–Wed 4h / Thu–Fri 45m unless `0DTE_EMPTY_SAME_DAY_TTL_SEC` is set; `0DTE_EMPTY_EXPIRY_TTL_SEC` 4h), cached nearest listed (`0DTE_NEAREST_EXPIRY_MEMO_TTL_SEC` 4h), cached expire-date list (`0DTE_EXPIRE_DATES_TTL_SEC` 4h). After the first miss, `intent_expiry_from_listed` skips the empty same-day and the chain cache is read from the selected weekly key so a 300s same-day stale does not re-issue 10031. Owned exact quotes and OCC repair read the same memo: a memo-empty day skips 1002 spelling recovery (`empty_expiry_memo_skip_1002_recovery`) and repair fetches the fill `selected_expiry_ymd` or `nearest_for` — one listed day, no calendar walk.
- Runtime diagnostics:
  - `SYMBOL_CLASSIFICATION` (`0DTE_WATCHLIST` + tier vs `OFF_WATCHLIST`)
  - `CHAIN_EXPIRY_SELECTION` (`0DTE`, `NEAREST`, `NONE`, `EXPLICIT`)
  - `CHAIN_EXPIRY_NEAREST_FROM_PAYLOAD` / `CHAIN_EXPIRY_NEAREST_FALLBACK` / `CHAIN_EXPIRY_MEMO_NEAREST` / `CHAIN_EXPIRY_LISTED_INTENT` / `CHAIN_EXPIRY_CACHE_RESOLVED` / `OPTION_CHAIN_EMPTY_EXPIRY` / `OCC_EXPIRY_REPAIR` (`stage=empty_same_day_use_listed`)
  - `CHAIN_REQUEST` (`door=fetch_0dte_intent_chain` · `reason=no_manager` when the door cannot resolve `OptionsChainManager`)
  - `EXACT_CONTRACT_QUOTE_HARD_FAIL` · `EXACT_CONTRACT_QUOTE_1002_RECOVERY` (spelling only; not used when the expiry is memo-empty)

### Desk snapshot pulse {#desk-snapshot-pulse}

Hosted desk objects under `{ns}/desk_public/`. Writer is Cloud Run `modules/desk_snapshot_pulse.py`. Hosted Firebase is a **reader only** (`pollMs` **1500**). See [Data.md](Data.md) desk prefix table · [Frontend.md](Frontend.md#live-freshness).

| Key | Default | Role |
|-----|---------|------|
| `DESK_SNAPSHOT_MARK_INTERVAL_SEC` | **1.0** | Off-thread heartbeat while the open book is non-empty. Reads executor memory (no extra Market API), maps fee-inclusive Cur% / `$` (`net_result_pnl_dollars` / `net_result_pnl_fraction`), bumps `head.seq`, uploads `snapshot.json` + `head.json`. Fill/close still flush immediately. Empty book: heartbeat sleeps. Not on the trading / monitor asyncio loop. |

**Serving now (`00625-wwv`; pulse first in `00617-992`):** the 1.0s mark pulse is in this image (code default `DESK_SNAPSHOT_MARK_INTERVAL_SEC=1.0`). It runs off-thread while the open book is non-empty. Fill/close still flush immediately. Do not lower this to chase the 1.5s hosted poll — the poll already GETs on every `seq` change. Raising it (e.g. 2–5s) is the throttle if GCS write volume ever matters.

**ORB1 Breakout opening execution (May 28 — code constants, not env)**

Implemented in `modules/orb_0dte_opening_execution.py`. **No new keys in `ORB0DTE.env`** — tune via deploy of code changes only.

| Constant / behavior | Value | Notes |
|---------------------|-------|-------|
| `OPENING_WINDOW_MINUTES` | **10** | 07:30–07:40 PT |
| `CONVEX_PRESERVATION_MIN` | **0.95** | Tier E / some premium relax paths |
| `CONVEX_DEFERRED_RETRY_MIN` | **0.90** | Opening selector + deferred retry + early preservation |
| `DEGRADED_CAPITAL_PCT` | **0.375** | ~37.5% size on opening/preservation fills |
| `ABSOLUTE_MICRO_PREMIUM` | **$0.03** | Hard exitability reject — aligned with `0DTE_MIN_ENTRY_PREMIUM` (Jul 27; OPEN **5C @ $0.035** still rejected as sub-floor / lotto class) |
| `PENNY_EXITABILITY_PREMIUM` | **$0.12** | Aligns with sub-$25 opening `premium_min` — not lowered globally |
| `HARD_MAX_SPREAD_PCT` | **20%** | Exitability hard reject |
| Liquidity classes | hardcoded sets | `ultra_liquid_etf_index`, `mid_liquid_momentum_equity`, `vol_exotic`, `thin_or_speculative` |

**Grep after deploy:** `ORB_0DTE_OPENING_SELECTOR_*` · `ORB_0DTE_CONVEX_PRESERVATION_EARLY_*` · `ORB_0DTE_NEAREST_LIQUID_SCORE` · `ORB_0DTE_EXITABILITY_SCORE` · `ORB_0DTE_BEST_EXECUTABLE_MISSED_CONTRACT` · **`ORB_0DTE_SELECTOR_FAILURE_AUDIT`**. Monitor (May 29 local): **`ORB_0DTE_MONITOR_CHAIN_HYDRATED`** · **`ORB_0DTE_MONITOR_LATENCY_AUDIT`** · **`ORB_0DTE_STAGE_B_PROMOTION_AUDIT`** · **`ORB_0DTE_SPREAD_HYDRATION_AUDIT`**. Full execution ladder: [0DTEORB1Breakout.md §9](0DTEORB1Breakout.md#9-strategy-types-and-execution).

**June 2 ORB1 Breakout profitability recovery** (`configs/ORB0DTE.env` — local; monitor `quote_grade` fix in code):

```bash
# Historical Jun 2 keys replaced by Shared.env entry policy.
0DTE_MAX_ENTRY_PREMIUM=2.50
0DTE_MIN_ENTRY_PREMIUM=0.03
0DTE_ENTRY_DELTA_MIN=0.18
0DTE_ENTRY_DELTA_MAX=0.42
0DTE_SINGLE_LEG_OPEN_WINDOW_MIN_OI=100
0DTE_SINGLE_LEG_MOMENTUM_MAX_SPREAD_PCT=10.0
0DTE_HARD_GATE_EXEC_SPREAD_RELAX_MULT=1.25
```

Tests: `tests/test_orb_0dte_jun02_2026_profitability_recovery.py` (**11/11**). Session: [Jun 2](doc_elements/Sessions/2026/Jun%202%20Session/SESSION_SUMMARY_JUN02_2026.md).

**ORB SO demo ledger (May 28 — env):** `SO_DEMO_STARTING_BALANCE=5000.0` in `configs/ORBSO.env`; one-time GCS migration log `SO_DEMO_BALANCE_MIGRATION` when upgrading from **$1k** ledger.

---

### **Easy Trendline 0DTE Configuration**

**Location**: Trendline signal/hold, option selection, and Trendline-specific stealth keys live in **`configs/Trendline0DTE.env`** (and **`OPTION_STEALTH_*`** where shared, in **`configs/Shared.env`** / **`configs/Risk.env`**). **Path enablement** and **Trendline operational caps** (`ENABLE_TRENDLINE_STRATEGY`, daily cap, slot sizing, `TRENDLINE_DEMO_STARTING_BALANCE`) live in **`configs/Trendline0DTE.env`**; ORB SO and ORB1 Breakout toggles live in **`configs/ORBSO.env`** and **`configs/ORB0DTE.env`**. Override in Cloud Run only if needed. See **`configs/README.md`** for load order.

At runtime, `main.py` loads:

- `TrendlineConfig` → `easyTrendline.trendline_config_loader.load_trendline_config_from_env(get_config_value)`
- `TrendlineOptionSelectionConfig` → `load_trendline_option_selection_config(get_config_value)`
- `OptionStealthConfig` → `modules.prime_options_stealth_trailing_tp.load_option_stealth_config(get_config_value)`

**Enablement / universe**:
```bash
ENABLE_TRENDLINE_STRATEGY=true
TRENDLINE_USE_FULL_0DTE_LIST=true   # default: full 0dte_list / dte0_manager.target_symbols
```

- At **6:45 AM PT**, after ORB1 secure: **Yahoo 1m [06:30–06:45]** → **TL0 dual-side** envelope draw (A1 = ORB1 H/L). Keys `SYM:bull|bear:trendline0` — isolated from TL1. Discovery via shared **24/10s** scan through **&lt;07:25**. 07:25–07:30 is the ORB2 capture gap.
- At **7:30 AM PT**, each symbol with ORB2 context gets a **TL1 dual-side** envelope draw (0–2 lines: bull PUT and/or bear CALL when intact + relevant). No options fire at build.
- **ORB1 levels (TL0 A1):** ORB#1 **06:30–06:45** H/L from `orb_strategy_manager.orb_data` · Yahoo 1m path **[06:30, 06:45]** (`_yf_tl0_structure_1m_bars` → `YahooBackfillManager`).
- **ORB2 levels (TL1 A1 price):** E*TRADE **session quote high/low at ~7:30** = 6:30–7:30 box (`capture_orb2_breakout_ranges` → `etrade_session_ohlc`). A1 **time** pinned `orb_*_extreme_ts` @ **06:30 PT** (session OHLC has no candle timeline).
- **TL1 path bars (envelope A2):** **batched** Yahoo RTH **1m** in **[06:30, 07:30] PT** (`TRENDLINE_BUILD_YF_1M_BACKFILL=true`, `TRENDLINE_BUILD_YF_1M_BATCH_SIZE=20`, `TRENDLINE_BUILD_YF_1M_BATCH_SLEEP_SEC=0.5`, `TRENDLINE_BUILD_YF_1M_MAX_SYMBOLS=148`). One `yf.download` per chunk — not per-symbol. Broker `bars=1` is ORB-timed merge **fallback** only (not real multi-bar history).
- **TL2/TL3 path:** Empty TL1 still **seeds** engine `_bar_cache` from path bars. Watch **hydrates** ORB2 universe first when cache thin (`TRENDLINE_WATCH_YF_1M_BACKFILL=true`, `TRENDLINE_WATCH_YF_1M_MIN_BARS=15`, `TRENDLINE_WATCH_YF_1M_MAX_SYMBOLS=148`, same batch size/sleep as build) **before** `maybe_inject_orb2_extension_trendlines`. Synth is last-resort only and must **not** bake ORB H/L into every bar.
- **Yahoo request manager:** `YAHOO_BACKFILL_MANAGER_ENABLED=true` in `Trendline0DTE.env`. TL0, TL1, watch TL2+, and session-tech 1m backfills all route through `YahooBackfillManager` (coalesce, cache TTLs, concurrency=1, pacing). Broker prices stay broker-only. Catalog: [Data.md § YahooBackfillManager](Data.md#yahoobackfillmanager-third-party-request-manager).
- Near-equal **MSE** ties between both geometries log **`setup_tie_break`** and pick deterministically—symbols are not skipped for that alone.
- **ORB ETF** and **ORB1 Breakout** lists and batch execution are unchanged.
- **Intraday (break mode — default):** Live break → promote → **Trade Profile** → **PO Entry Snapshot** (`trendline_emit_handoff`) → **Enter Trade**. Call SS and Put SS are **hard**, outside the profile. Watch batch: fair-rotate **24**/10s (no extras queue). A slow exit quote does not skip the break check. Local source compares the scan quote first; Yahoo structure bars run after the order. Full path: [0DTETrendline.md §8](0DTETrendline.md#8-simple-break-mode-default-runtime).
- **Confirm:** one hydrate of this signal's bars and chain, then the Trade Profile. Directional percent and same-side open interest are profile keys.
- **Pre-draw (TL0 on ORB1 / TL1 on ORB2):** Dual-side envelope draw when `TRENDLINE_GEOMETRY_PHASE1_ENABLED=true`. TL0 uses `a1_policy=tl0` · TL1 uses ORB2 box. Mission contest **removed**. Grep: **`GEOMETRY_SELECTION_AUDIT`**, **`GEOMETRY_REJECTION_AUDIT`**, `geometry_dual_side_watch`, `SCHEDULER_ORDER | stage=orb2_capture`, `stage=tl0`. See [0DTETrendline.md §730 formula](0DTETrendline.md#730-trendline-build--design-formula).
- Hard quality / post-exec rejects **expire that `{SYM}:{TL0|1|2|3|4}:{side}` envelope** (A1/A2 persist). Soft window / capital wait still re-arm. Sibling slots and opposite envelope stay open. See [0DTETrendline.md reject / one-attempt](0DTETrendline.md).
- **`TRENDLINE_ENTRY_PROFILE`:** **Deprecated for post-draw** — profile merge skipped when `TRENDLINE_BREAK_MODE=true` (`modules/config_loader.py`). `modules/trendline_entry_defaults.py` holds only `TRENDLINE_BREAK_MODE=true`.
- **Legacy emit path (compare-only):** Set `TRENDLINE_BREAK_MODE=false` to exercise old confirm/emit stack with in-code defaults (`trendline_legacy_config_defaults.py`). Not for production. Historical keys documented in [0DTETrendline.md § Legacy](0DTETrendline.md#legacy-emit-path-compare-only).
- **Breakout:** `ENABLE_BREAKOUT_STRATEGY` + **`Breakout0DTE.env`** — activate **right after ORB2** (TL discovery waits for TL1); shared discovery with TL; same spine (**Structure → Trade Profile → PO Entry Snapshot → Enter Trade**); quality / structural / market-quality rejects **or a fill** → **1h same-side cooldown + inside-ORB2 re-entry** (`BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS=3600`); fill does **not** day-lock the symbol; opposite side open. Cutoff **`BREAKOUT_NO_NEW_ENTRIES_AFTER_PT=12:30`**. ORB1 does **not** use this re-entry path. See [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · [0DTERisk §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit).

**Trendline capacity + slot sizing** (also in `prime_trading_system` via `get_config_value`):

```bash
# Shared equal-slot pool — keep SLOT / path / TOTAL aligned at 50 RESEARCH (Account0DTE.env canonical)
0DTE_SLOT_COUNT=50
0DTE_TRADING_CAPITAL_PCT=90
0DTE_MONITOR_MAX_SYMBOLS=50
ORB_0DTE_MAX_POSITIONS=50
TRENDLINE_0DTE_MAX_POSITIONS=50
TRENDLINE_SLOT_COUNT=50             # alias of 0DTE_SLOT_COUNT
TRENDLINE_MAX_OPEN_POSITIONS=50     # alias of TRENDLINE_0DTE_MAX_POSITIONS
TOTAL_0DTE_MAX_POSITIONS=50
MAX_TOTAL_OPTION_POSITIONS=50       # legacy alias of TOTAL
0DTE_DEMO_STARTING_BALANCE=1000000.0
# TL/BO exec-ready TTL (capital slot wait): Shared.env 0DTE_EXEC_SIGNAL_EXPIRE_MINUTES=1
# ORB1 Breakout is one-shot batch execution with no later grace window.
# Quality/structure rejects do not use the TTL retry window.

# §6 — Red Day Portfolio Protection (mirror ORB1 Breakout Step 5)
TRENDLINE_RED_DAY_GATE_ENABLED=true
TRENDLINE_RED_DAY_ALLOW_TIER1_CALL=true
TRENDLINE_RED_DAY_ALLOW_PUT=true

# Alert order + measurement (Trendline0DTE.env §6 + Shared.env)
TRENDLINE_DEFER_POST_BUILD_WATCH_UNTIL_ORB_BATCH=true
TRENDLINE_DEFER_BUILD_UNTIL_ORB_BATCH=true   # Arm once @ 07:10 (watch); single-flight build after ORB2; post-watch reason=watch_mode_post_tl1
TRENDLINE_NO_NEW_ENTRIES_AFTER_PT=12:30
TRENDLINE_EXPIRATION_TIME_PT=12:30
ENABLE_TRENDLINE_MEASUREMENT=true
ENABLE_TRENDLINE_EMIT_SHADOW=true
```

**Removed / dead (do not reintroduce):** `TARGET_TRENDLINE_ACTIVE` · `TRENDLINE_MAX_TRADES_PER_DAY` · `TRENDLINE_MAX_POSITION_PCT` · `MAX_TRENDLINE_OPTION_SYMBOLS` (use `0DTE_MONITOR_MAX_SYMBOLS`).

**Capital model:** Each watch cycle refreshes slot target from equity (cash + open entry costs). Entry gate is **rolling open positions** within the shared total: execute while combined opens &lt; `TOTAL_0DTE_MAX_POSITIONS` and path opens &lt; `TRENDLINE_MAX_OPEN_POSITIONS`. Capacity/Red Day blocks: **silent Telegram** (rearm only).

**Capital model (shared sacred buffer — Jul 14):**

```text
equity           = cash + sum(open entry_cost)
buffer_reserve   = max(session_floor @ 7:30, equity × 10%)
tradable_equity  = equity − buffer_reserve
slot_target      = tradable_equity / 0DTE_SLOT_COUNT
available_cash   = max(0, cash − buffer_reserve)
entry_budget     = min(slot_target, available_cash)   ← qty sizing uses this
```

- Buffer **never shrinks** intraday on losses; **ratchets up** with profits.
- After a **profitable** exit, next entry still sizes at **1/N tradable** — not full available cash.
- After a **total-loss** exit with pool full, **$0** deployable until another slot closes.
- ORB batch uses the **same** allocator (`calculate_position_sizing` → `compute_0dte_capital_snapshot`).
- **Module:** `easyTrendline/trendline_capital_allocation.py` · **Tests:** `tests/test_trendline_capital_allocation.py` · `tests/test_0dte_max_position_size_sum.py`.
- Full detail: [0DTERisk.md §5–6](0DTERisk.md#5-orb-0dte-position-sizing) · [0DTETrendline.md §11](0DTETrendline.md#11-account-management).

**7:30 build — broker request budgets** (paired **one intraday + one quote** call per chunk; 148 names at 25/chunk = **6** calls, under the 8-call cap). Yahoo 1m structure is a **separate** managed download (`YahooBackfillManager`, 148/20 = 8 chunks) and is not limited by these broker-call caps:

```bash
TRENDLINE_DATA_CHUNK_SIZE=25
TRENDLINE_MAX_INTRADAY_BATCH_CALLS_PER_BUILD=8
TRENDLINE_MAX_QUOTE_BATCH_CALLS_PER_BUILD=8
TRENDLINE_MAX_SYMBOLS_PER_BUILD=200
TRENDLINE_ENABLE_BUILD_DEGRADATION=true
TRENDLINE_MAX_BUILD_DURATION_MS=0
```

**Simple post-draw break mode** (env → `TrendlineConfig` via `load_trendline_config_from_env`; canonical keys in **`configs/Trendline0DTE.env`**). Post-geometry admit is **GATE_TL_*** only — [0DTEQualityGates.md §5](0DTEQualityGates.md#5-trendline-quality-gate).

```bash
TRENDLINE_BREAK_MODE=true
TRENDLINE_MIN_SCORE=55
TRENDLINE_MIN_BREAK_DISTANCE_PCT=0.0008
GATE_TL_VWAP=observe
QG_TL_VWAP_REJECT_AT=true
GATE_TL_RSI=observe
QG_TL_RSI_CALL_MAX=80
QG_TL_RSI_PUT_MIN=20
GATE_TL_BB=observe
QG_TL_BB_CALL_MAX=70
QG_TL_BB_PUT_MIN=30
GATE_TL_Vol_Ratio=hard
GATE_TL_Vol_Ratio_MIN=0.012
GATE_TL_dir_pct=hard
TRENDLINE_DIR_VOLUME_PCT=0.10
TRENDLINE_MAX_ENTRY_DISTANCE_PCT=0.05
TRENDLINE_ENTRY_WINDOW_SEC=300
TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC=10800
TRENDLINE_ENFORCE_GEOMETRY_SIDE=true
```

Phase 2 emit is **geometry side + min break only** (`apply_priority_emit_gates`). `TRENDLINE_MIN_CONTINUATION_PCT` / `TRENDLINE_EMIT_MIN_BREAK_DISTANCE_PCT` / FT / Grade A sustain keys were **deleted** — they are not live and are not listed as `=off`. Canonical admit: [0DTEQualityGates.md §5](0DTEQualityGates.md#5-trendline-quality-gate). Historical Jun 12–17 desks stay in [0DTETrendline.md Phase 2](0DTETrendline.md#phase-2-priority-gate) as research narrative, not operator knobs.

**Code-level Jun 17 fixes (no env keys — deploy with app revision):**

| Change | Module | Desk symbols |
|--------|--------|--------------|
| Material score refresh on promotion + watch bars | `trendline_simple_mode.py` | **UBER** · **SMCI** |
| Sustain continuation floor (historical Jun 17) | **deleted** — not in `trendline_emit_gates.py` | **IBM** (archive) |
| 135-key unified break snapshot hydration | `trendline_break_snapshot.py` | All Grade A breaks |
| `trendline_emit_handoff` snapshots | `prime_trading_system.py` | All emit passes (pre-broker) |

**Priority Optimizer snapshot stages (ORB1 Breakout parity — `Shared.env` §6a):**

```bash
ENABLE_EXECUTION_FEATURE_SNAPSHOT=true
ENABLE_TRENDLINE_BREAK_SNAPSHOT=true
ENABLE_TRENDLINE_LEGACY_JSONL_DATASET=false   # retired
0DTE_PRIORITY_COLLECTOR_ENABLED=false         # retired
```

| Stage | File pattern | When |
|-------|--------------|------|
| `orb_0dte_execution_window_input` | `*_orb_0dte_execution_window_input.json` | ORB1 Breakout @ 7:30 after Convex + Hard Gate + options enrich |
| `trendline_break_snapshot` | `trendline_break_snapshots/YYYY-MM-DD_breaks.json` | First material break per symbol |
| `trendline_emit_handoff` | `*_trendline_emit_handoff.json` | Emit gate pass, pre-broker |
| `trendline_options_executed` | `*_trendline_options_executed.json` | Each broker attempt — fill **or** reject (`broker_success`) |

> **No separate `trendline_emit_execution_failed` writer.** Broker rejects use `trendline_options_executed` with `broker_success=false`.

**GCS layout:** Execution snapshots → `{research_app|final_app}/priority_optimizer/execution_snapshots/`. Trendline breaks → flat `priority_optimizer/trendline_break_snapshots/` (not namespaced). **RESEARCH app only** — FINAL live service does not write PO snapshots. Canonical names are create-if-absent (`{date}__{family}__{identity}.json`). The first body stays. `00619-xr6` takes one lock per name so two exit ticks do not both POST that create. The same local persist slices session tech to bars closed by the event, copies the handoff freeze onto executed, and ages `first_red` from the live OCC quote on that tick — [PriorityOptimizer.md](PriorityOptimizer.md).

Retrieve: `python3.11 priority_optimizer/retrieve_execution_snapshots.py --date YYYY-MM-DD` (DEMO research path).

**Jun 17 desk expectation after deploy:** Grade A PUT **QQQ, SPY, GLD, SMCI, IWM, IBM, UBER, RUT, HD** → emit → execute on comparable tape. Skip **CRWV, PANW** remain blocked (`entry_distance_extended` · defer+chaos). See [TrendlineJun17Review](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Trendline%20Improvements/TrendlineJun17Review.md).

**Pre-draw selector v3** (7:30 build only — not post-draw emit gates):

```bash
TRENDLINE_CONTROL_LINE_V3_ENABLED=true
TRENDLINE_CONTROL_LINE_MIN_SCORE=25
TRENDLINE_DUAL_LANE_MIN_SCORE_RATIO=0.70
TRENDLINE_COMPRESSION_CALL_BAND_LOW=0.20
TRENDLINE_COMPRESSION_CALL_BAND_HIGH=0.55
TRENDLINE_ORB_BIAS_MSE_SOFT_RATIO=8.0
TRENDLINE_PRIMARY_LANE_ENFORCE_GEOMETRY_ALIGNMENT=true
TRENDLINE_NO_NEW_ENTRIES_AFTER_PT=12:30
TRENDLINE_EXPIRATION_TIME_PT=12:30
```

**Geometry Phase 1 — dual-side TL0/TL1 watch** (draw only — does not change post-draw `TRENDLINE_*` break/entry gates; Phase 1 keys in `configs/Trendline0DTE.env` §3):

```bash
TRENDLINE_GEOMETRY_PHASE1_ENABLED=true
TRENDLINE_MAX_LINE_DISTANCE_AT_CUTOFF_PCT=0.02
TRENDLINE_LINE_RELEVANCE_RETRY_ALTERNATES=true
TRENDLINE_MAX_LINE_DISTANCE_VOLATILITY_ADJUST=false
TRENDLINE_MSE_OVERRIDE_BLOCK_RATIO=3.0
TRENDLINE_MSE_OVERRIDE_REQUIRES_RELEVANCE_PASS=true

# Mission contest removed — dual-side TL1 watch only (no TRENDLINE_GEOMETRY_MISSION_* knobs)
TRENDLINE_GEOMETRY_SKIP_TREND_CONTINUATION=true
# Cutoff-control line — quarantined for winners (envelope-only)
TRENDLINE_CUTOFF_CONTROL_LINE_ENABLED=false
TRENDLINE_CUTOFF_CONTROL_NEAR_CUTOFF_MINUTES=45
```

| Key | Default | Role |
|-----|---------|------|
| `TRENDLINE_GEOMETRY_PHASE1_ENABLED` | `true` | Route **inside / above / below** ORB2 through dual-side envelope draw |
| `TRENDLINE_MAX_LINE_DISTANCE_AT_CUTOFF_PCT` | **0.02** | Reject lines farther than 2% from cutoff at TL1 |
| `TRENDLINE_LINE_RELEVANCE_RETRY_ALTERNATES` | `true` | Relaxed alternate if strict gate empty |
| `TRENDLINE_MSE_OVERRIDE_BLOCK_RATIO` | **3.0** | Soft dominance ratio for ORB-side rank / expansion lane |
| `TRENDLINE_MSE_OVERRIDE_REQUIRES_RELEVANCE_PASS` | `true` | Override only if alternate passes relevance |
| `TRENDLINE_GEOMETRY_SKIP_TREND_CONTINUATION` | `true` | No Trendline build on pure ORB continuation |
| `TRENDLINE_CUTOFF_CONTROL_LINE_ENABLED` | `false` | Quarantined for winners — envelope-only; see [0DTETrendline.md §730 formula](0DTETrendline.md#730-trendline-build--design-formula) |
| `TRENDLINE_CUTOFF_CONTROL_NEAR_CUTOFF_MINUTES` | **45** | Anchor-two search window before cutoff (legacy path) |

- **TL0 draw:** dual-side A1→A2 on ORB1 box after ≥06:45 (`modules/trendline_tl0_build.py` · `a1_policy=tl0`).
- **TL1 draw:** dual-side A1→A2 on ORB2; mission contest **removed**. Live break: fill = EXECUTED · hard reject = EXPIRED (one attempt).
- **Module:** `easyTrendline/trendline_geometry_selection.py` · **Tests:** `tests/test_trendline_tl0.py` · `tests/test_geometry_phase1.py` · `tests/test_pre730_structure_envelope.py` · `tests/test_path_exec_day_exclude.py`.
- **Desk cohorts (historical intent):** [Grade A](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Grade%20A%20Trendlines.md) · [Skip](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Skip%20Trendlines.md).

- **`TRENDLINE_NO_NEW_ENTRIES_AFTER_PT` / `TRENDLINE_EXPIRATION_TIME_PT`:** **12:30** PT (Jul 30). Loaded into `TrendlineConfig` / Prime to stop **new** Trendline entries after that Pacific clock; monitoring and exits continue.
- **`BREAKOUT_NO_NEW_ENTRIES_AFTER_PT`:** **12:30** PT (Jul 30) in `Breakout0DTE.env`.
- **Path consume (Aug 16):** **ORB1** reject = 1h same-side (other H/L open until 07:25); fill = one trade, no re-entry. **ORB2** reject **or fill** = 1h + inside-ORB2 re-entry (`BREAKOUT_ALLOW_REENTRY_AFTER_PROFITABLE_EXIT` still applies to BO). **TL** hard reject/fill expire that slot/side envelope. [0DTERisk §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit) · [PositionMonitoring §8.4](PositionMonitoring.md#84-same-side-1h-cooldown-after-exit).
- **Production visibility logs (simple mode):** **`TRENDLINE_SIMPLE_CONFIG_AUDIT`**, **`TRENDLINE_SIMPLE_BREAK_DETECTED`**, **`TRENDLINE_SIMPLE_BREAK_PROMOTED`**, **`TRENDLINE_SIMPLE_QUALITY_SCORE`**, **`TRENDLINE_SIMPLE_SIGNAL_EMITTED`**, **`TRENDLINE_SIGNAL_RETURNED_TO_PRIME`**, **`TRENDLINE_PRIORITY_DATASET_CAPTURED`**, **`TRENDLINE_LEGACY_COMPARE_MODE`** (compare-only), **`TRENDLINE_LEGACY_PATH_BLOCKED`**. Build path: `TRENDLINE_PIPELINE`, `TRENDLINE_SELECTOR_*`, `TRENDLINE_CONTROL_LINE_FALLBACK`, **`GEOMETRY_SELECTION_AUDIT`**, **`GEOMETRY_REJECTION_AUDIT`** (Phase 1 local).
- **Capital logs (local 00372+):** **`buffer_floor_locked`**, **`slot_cap_refresh`** (includes `entry_budget`, `available_cash`, `buffer_reserve`), **`buffer_reserve_exhausted`**, **`slot_signal_rejected`** (full-slot / no-slot current-signal skip/reject), **`execution_rearm`**, **`exec_ready_ttl_expired`**. Execution: `TRENDLINE_EXECUTION_HANDOFF`, `TRENDLINE_EXECUTION_ALLOWED`, `TRENDLINE_EXECUTION_BLOCKED`.
- **Legacy telemetry (compare-only path):** `TRENDLINE_EMIT_GATE_AUDIT`, `TRENDLINE_EMIT_LIVE_BREAK_DISTANCE`, `TRENDLINE_EXPANSION_SCORE`, etc. — see [0DTETrendline.md § Legacy](0DTETrendline.md#legacy-emit-path-compare-only).

**0DTE contract selection** (Trendline + Breakout share `unified_contract_select.py` — Jul 27):
```bash
# Shared.env canonical entry policy
0DTE_MAX_ENTRY_PREMIUM=2.50
0DTE_MIN_ENTRY_PREMIUM=0.03
0DTE_ENTRY_DELTA_MIN=0.18
0DTE_ENTRY_DELTA_MAX=0.42
0DTE_ENTRY_MIN_VOLUME=50
0DTE_ENTRY_MIN_OPEN_INTEREST=100
# Skip Signals — extra CALL/PUT + strategy/side 0-MFE reject during collection (Configs.md)
GATE_call_ss=hard
QG_CALL_SS_SVA_MAX=0.20
QG_CALL_SS_IV_HIGH=0.60
QG_CALL_SS_DIR_MAX=0.40
QG_CALL_SS_IV_MID=0.50
QG_CALL_SS_VWAP_ABS_MIN=0.80
QG_CALL_SS_RSI_MIN=70
GATE_put_ss=hard
QG_PUT_SS_DIR_MAX=0.20
QG_PUT_SS_RSI_MAX=45
QG_PUT_SS_RSI_MIN=70
QG_PUT_SS_VOL_MAX=5.0
QG_PUT_SS_RSI_THIN_MAX=40
QG_PUT_SS_VOL_THIN=0.20
QG_PUT_SS_IV_HIGH=0.70
GATE_path_ss=off
LIVE_MIN_ASK_SIZE=1
LIVE_MIN_BID_SIZE=1

# Path-specific Trendline controls
TRENDLINE_DELTA_TOLERANCE=0.02
TRENDLINE_OPTION_STRIKE_MODE=otm_1_to_2
TRENDLINE_OPTION_LOTTO_MODE=true
TRENDLINE_OPTION_MAX_SPREAD_PCT=0.30
TRENDLINE_ALLOW_SYNTHETIC_ENTRY=false
```

**Rank / walk:** near-ATM / higher δ among passers; ask below floor or δ below min → walk closer; δ above max → one step further OTM. OTM ≤**5.5%** (prefer == hard, Jul 28). `delta_out_of_band` with δ≈0/missing is **transient** (no day-exclude); empty band / OTM-too-deep stays terminal.

Final execution uses shared policy `0DTE_ENTRY_MIN_VOLUME` / `0DTE_ENTRY_MIN_OPEN_INTEREST` / `0DTE_MIN_ENTRY_PREMIUM` / `0DTE_MAX_ENTRY_PREMIUM` via `option_entry_liquidity_validator.py`. Executable pricing: **BTO at ask**, **STC at bid** (`modules/option_executable_pricing.py`). See [0DTEORB1Breakout.md § Unified entry](0DTEORB1Breakout.md#unified-entry-contract-package-jul-27--local-rf).

Rejection reasons are logged as `TRENDLINE_PIPELINE | stage=contract_rejected | reason=...` (delta band, spread, liquidity, contract too expensive, etc.). Full-slot/no-slot misses log `TRENDLINE_PIPELINE | stage=slot_signal_rejected | classification=skip_reject` and reject only the current TL1/TL2/TL3 signal.
When `delta > 0DTE_ENTRY_DELTA_MAX` but `delta <= 0DTE_ENTRY_DELTA_MAX + TRENDLINE_DELTA_TOLERANCE`, contract selection can continue and logs `TRENDLINE_DELTA_TOLERANCE_USED`.

### **0DTE options stealth (hybrid premium + underlying profit capture)**

**Go-to guide:** [0DTEProfitCapture.md](0DTEProfitCapture.md) — full stealth reference (exit taxonomy, und runner, env index, grep playbook).

**Module:** `modules/prime_options_stealth_trailing_tp.py` — shared by **ORB1 Breakout** and **Trendline 0DTE** (not ETF `prime_stealth_trailing_tp.py`). Monitor path also uses `modules/premium_engine.py` + `modules/exit_decision_dispatch.py` · und runner `modules/price_runner_exit.py` · PC-PX2 `modules/px2_exit.py` · PC-PX3 `modules/px3_exit.py` (`PX3_ENABLED=true`).

**Canonical file:** `configs/Shared.env` **§2b** `OPTION_*` / `OPTION_SINGLE_*` (RESEARCH + FINAL) · §3 generic/path overlays. Deprecated aliases removed from env Jun 18 (code fallbacks remain). See `configs/config_manifest.yaml`.

#### Hybrid rule (Aug 17 — PC-PX2 · Pareto leftover ≤ $10 · previous = thin TP30 + 0.20% then 0.50%)

**Doctrine:** Premium **+30% ARM_TRAIL** then **PC-PX2**: stall-bank when und+prem stop extending (leftover ≤ **$10** is the same QQQ/INTC-14 dumper class); else **20pp** premium giveback (lock@**200%** same width — SPY +470% / MSFT +312%). Before ARM, **pre-ARM profit protect** **decides** +20% to &lt;+30% HWM fades to **+2%** only for weak/degraded exit candidates after 30m held, 10m since HWM, and current PnL ≤ **−10%**; near-ARM red hold runs **only after** pre-ARM/low-ARM/CAP wrap attempt fails (Band B 2026-09-21). **PX2 early premium trail** protects exceptional +175% premium HWM winners before 12:30 (`premium_trail`, `early_prem_trail=true`); BE **fires** at 0% premium (never last-green as the decision); **fill** = live bid (may print red vs entry). **0.50%** und trail only after und HWM ≥0.5% AND prem ≥60%. Required **12:30** `mfe_trail` −10pp (do not skip GDX). Expected book **+$12,545 (56%)** vs overlay **+$12,200 (54%)** vs live thin TP **+$4,809 (21%)**; Aug 17–26 early premium trail adds **+7945.9pp** with **1 accepted worse**. ARM-tick thin TP30 is the **previous working** stack — rollback via `PX2_ENABLED=false` · `0DTE_MFE_TAKE_PROFIT_IF_PRICE_THIN=true`. **Moon hold Removed Aug 17.** Telegram star = realized **> +200%**. — [0DTEProfitCapture.md §3.5](0DTEProfitCapture.md#35-pc-px2-after-arm--modulespx2_exitpy) · [§3.11](0DTEProfitCapture.md#311-fills-and-net-pnl-true-to-live).

| Layer | Uses underlying? | Uses option premium? |
|-------|------------------|----------------------|
| Signal / entry (ORB SO, Trendline break) | Yes (ORB range, trendline geometry) | Contract selection only |
| **`price_stop` (−0.80% und, all 0DTE, only before ARM_TRAIL)** | **Yes** — vs frozen `entry_underlying` | No (stop is chart-based) |
| **Stall-bank (after ARM)** | **Yes** — und **and** prem stop new highs **8** ticks. Leftover ≤ **$10** same class | Banks current profit `mfe_take_profit` |
| **20pp / lock@200% (after ARM)** | No | **`premium_trail`** from HWM; floor max(0, HWM−20pp) |
| **PX2 early premium trail** | No | **`premium_trail`** before 12:30 when premium HWM ≥ **+175%**, current P&L ≥ **+75%**, and pullback ≥ **5pp**; diagnostic `early_prem_trail=true` |
| **Near-ARM weak red exit guard** | No | At MFE ≥ **+20%** and &lt; **+30%**, red `no_progress` / `scratch` / `time_exit` are held until flat/green; does not lower ARM or start PX2 |
| **Post-ARM BE** | No | **Decision** at 0% premium; **fill** = live bid (may print red vs entry) |
| **0.50% `price_trail`** | **Yes** — only after und HWM ≥**0.5%** AND prem ≥**+60%** | Concurrent with 20pp (first latch) |
| **PX3 Protected Runner** | **Yes** — 0.4% trail after PX2 `premium_trail`, exceptional +100% winner handoffs, and dual-sided one-quote confirmation after qualified PX2 `mfe_take_profit` | **ON locally** (`PX3_ENABLED=true`, `PX3_EXCEPTIONAL_ENABLED=true`, `PX3_THIN_LEAD_EXCEPTIONAL_ENABLED=true`, `PX3_HIGH_HWM_EXCEPTIONAL_ENABLED=true`, `PX3_MFE_CONFIRM_ENABLED=true`). Does not replace PX2. Bank-tick HWM only — no session-peak look-ahead. — [0DTEProfitCapture.md §3.6](0DTEProfitCapture.md#36-px3-protected-runner--modulespx3_exitpy) |
| **12:30 `mfe_trail` −10pp** | No | Required on every name still open. Do not skip. |
| **Moon hold (H2)** | — | **Removed Aug 17** — +60% → 0.50% trail is the runner |
| **ARM_TRAIL / pre-ARM / never-armed BE/scratch / weak exits / data safety** | No | **Yes** — all-day MFE trail **config-off** · pre-ARM +20% HWM → +2% floor **ON** · near-ARM red fallback **ON** · always-bank TP30 **OFF** · ARM-tick thin TP30 **OFF** |
| **Trail / micro-lock / profit_floor** | **No** | Keys retained; exits **disabled** |
| Structure invalidation | **Removed** Jul 1, 2026 | Was priced at premium when active |
| HWM ratchet when quote degraded | Optional confirm only | Primary mark still premium |

Desk charts show **stock** price; automation protects **long premium**. After ARM, dumpers (und+prem stall, leftover ≤ $10) bank current green; extending moons ride 20pp / lock@200%. Do not start und trail from ARM on thin names (sells SPY/MSFT). If theta erodes premium to 0 after ARM, BE books **$0**, not red.

**Pareto (Aug 13+14+17 40 wins — list):** take leftover ≤ $10 (QQQ +$422, INTC ORB1 +$108) · take NFLX on 2s tape (+$226, not sparse +$74) · do **not** keep GDX live vs 12:30 · do **not** drop und trail · last-green **rejected** · never-armed PEP/SPCX/RGTI book **$0** not red.

**Mark pipeline (Jul 14):** `PremiumEngine.evaluate` → frozen `PremiumSnapshot` (STC bid) → `stealth.evaluate(position, premium, …)` → `ExitDecision` → `dispatch_exit_decision`. HWM/PnL fields apply only via synchronizer `apply_premium_state_update`. Logs: `OPTION_STEALTH_PREMIUM_PNL_AUDIT`, `OPTIONS_STEALTH_PREMIUM_STATE`, `OPTION_BE_ARMED`, `BROKER_POSITION_SYNC`.

**Cutover flags:**
```bash
OPTION_STEALTH_USE_EVALUATE_API=true      # monitor primary path (default)
OPTION_STEALTH_EVALUATE_SHADOW=false      # log-only legacy compare on metadata copy
```

**Production quote strictness:**
```bash
REQUIRE_LIVE_OPTION_DATA=true
OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA=true
OPTION_REQUIRE_LIVE_QUOTES=true
```

#### Unified 0DTE single-leg (§2b — Jun 18, profile routing Jun 26)

Canonical keys live in **`configs/Shared.env` §2b** (identical RESEARCH + FINAL).

**Profile routing (Jul 1, 2026):** `_resolve_exit_profile()` routes **0DTE singles** (ORB + Trendline lotto) through unified **`OPTION_*` / `OPTION_SINGLE_*`** keys in `Shared.env` §2b. Canonical live values are in the current policy block below.

**Policy (Sep 22, 2026 — bid-aware working BE; fills = liquidable bid; floors = decisions):** Primary enabled exits: **`price_stop` (−0.80% all 0DTE, only before ARM)** · **`premium_stop` (−60% when peak MFE &lt; 15%)** · **nonstarter 20m / −40% / MFE &lt; 10%** · EARLY_BE **20%/5m/−5%** (never-armed) **arms working BE** · pre-ARM profit protect after MFE ≥ **+20%** and &lt; **+30%** (`OPTION_PRE_ARM_PROFIT_PROTECT_*`: +2% decision floor after **30m** held, 10m since HWM, current ≤ **−5%**; **arm working BE**) · low-ARM BE after +10–20% MFE / **20m** held · CAP_weak **−10%** · near-ARM hold after wraps (`OPTION_NEAR_ARM_PROTECT_MFE_PCT=0.20`; Band B wrap-first) · **ARM +30%** then **stall-bank** / **`premium_trail` 20pp (lock@200%)** / **PX3** · post-ARM **`breakeven` at entry $0** · **`price_trail` 0.50%** after und HWM ≥0.5% AND prem ≥60% · after 12:30 **`mfe_trail` −10pp required** · `scratch` · `time_exit` · `no_progress` · `end_of_day_exit`. Working BE: wait **2m** / hard-stop **−25%** / target **0%** / cancel-if-peak-above-rule-max. **Config-off:** all-day MFE trail · always-bank TP30 · ARM-tick thin TP30. Cloud `00556-78z` still needs **`deploy now`** for this BE ship. — [0DTEProfitCapture.md](0DTEProfitCapture.md).

```bash
OPTION_MIN_HOLD_SECONDS=384
OPTION_TRAILING_TRIGGER_PCT=0.30
OPTION_SINGLE_NO_PROGRESS_MINUTES=60
OPTION_SINGLE_NO_PROGRESS_EARLY_MINUTES=30
OPTION_SINGLE_NO_PROGRESS_EARLY_MAX_MFE_PCT=0.05
OPTION_TIME_EXIT_MINUTES=150
OPTION_NO_PROGRESS_SKIP_IF_MFE_PCT=0.30
OPTION_NEAR_ARM_PROTECT_MFE_PCT=0.20
OPTION_PRE_ARM_PROFIT_PROTECT_ENABLED=true
OPTION_PRE_ARM_PROFIT_PROTECT_TRIGGER_MFE_PCT=0.20
OPTION_PRE_ARM_PROFIT_PROTECT_FLOOR_PNL_PCT=0.02
OPTION_PRE_ARM_PROFIT_PROTECT_MIN_CURRENT_PNL_PCT=-0.05
OPTION_PRE_ARM_PROFIT_PROTECT_MIN_HOLD_MINUTES=30
OPTION_PRE_ARM_PROFIT_PROTECT_MIN_SECONDS_SINCE_HWM=600
OPTION_PRE_ARM_PROFIT_PROTECT_SETTLE_AT_FLOOR=true
OPTION_LOW_ARM_PROFIT_PROTECT_ENABLED=true
OPTION_LOW_ARM_PROFIT_PROTECT_TRIGGER_MFE_PCT=0.10
OPTION_LOW_ARM_PROFIT_PROTECT_MAX_MFE_PCT=0.20
OPTION_LOW_ARM_PROFIT_PROTECT_FLOOR_PNL_PCT=0.00
OPTION_LOW_ARM_PROFIT_PROTECT_MIN_CURRENT_PNL_PCT=-0.05
OPTION_LOW_ARM_PROFIT_PROTECT_MIN_HOLD_MINUTES=20
OPTION_LOW_ARM_PROFIT_PROTECT_MIN_SECONDS_SINCE_HWM=600
OPTION_LOW_ARM_PROFIT_PROTECT_SETTLE_AT_FLOOR=true
OPTION_EARLY_BE_ENABLED=true
OPTION_EARLY_BE_MAX_MFE_PCT=0.20
OPTION_EARLY_BE_FLOOR_PNL_PCT=0.00
OPTION_EARLY_BE_MIN_CURRENT_PNL_PCT=-0.05
OPTION_EARLY_BE_MIN_HOLD_MINUTES=5
OPTION_EARLY_BE_MIN_SECONDS_SINCE_HWM=0
OPTION_EARLY_BE_SETTLE_AT_FLOOR=true
OPTION_WEAK_LOSS_CAP_ENABLED=true
OPTION_WEAK_LOSS_CAP_MAX_MFE_PCT=0.30
OPTION_WEAK_LOSS_CAP_FLOOR_PNL_PCT=-0.10
OPTION_WEAK_LOSS_CAP_SETTLE_AT_FLOOR=true
OPTION_PROTECT_BID_AWARE_BE_ENABLED=true
OPTION_PROTECT_BID_AWARE_BE_WAIT_MINUTES=2
OPTION_PROTECT_BID_AWARE_BE_HARD_STOP_PCT=-0.25
OPTION_PROTECT_BID_AWARE_BE_CANCEL_IF_PEAK_ABOVE_RULE_MAX=true
OPTION_PROTECT_BID_AWARE_BE_TARGET_PNL_PCT=0.00
OPTION_SINGLE_MFE_TRAIL_ENABLED=false
OPTION_SINGLE_MFE_TRAIL_TRIGGER=0.10
OPTION_SINGLE_MFE_TRAIL_EXIT=0.10
OPTION_SCRATCH_EARLY_MINUTES=30
OPTION_SCRATCH_EARLY_MAX_MFE_PCT=0.10
0DTE_ARM_TRAIL_PCT=0.30
0DTE_MFE_TAKE_PROFIT_ENABLED=false
0DTE_MFE_TAKE_PROFIT_IF_PRICE_THIN=false
PX2_ENABLED=true
PX2_TRAIL_PCT=0.20
PX2_LOCK_MOON_PCT=2.00
PX2_STALL_TICKS=8
PX2_BE_FLOOR=true
PX2_PRICE_TRAIL_NEED_PCT=0.005
PX2_PRICE_TRAIL_PREM_PCT=0.60
PX25_ENABLED=true
PX25_MIN_PREM_HWM_PCT=1.50
PX25_EXCLUDE_ULTRA_CLEAN_PNL_PCT=7.50
PX25_EXCLUDE_ULTRA_CLEAN_CAPTURE_RATIO=0.85
PX25_PRICE_TRAIL_PCT=0.01
PX25_MAX_EXTENSION_SECONDS=900
PX25_PREM_FLOOR_PCT=0.00
PX3_ENABLED=true
PX3_TREND_MFE_PCT=1.00
PX3_TREND_PRICE_HWM_PCT=0.004
PX3_PRICE_TRAIL_PCT=0.004
PX3_TREND_PREM_FLOOR_PCT=0.50
PX3_MOON_MFE_PCT=2.00
PX3_MOON_PATIENCE_SECONDS=300
PX3_MOON_PREM_FLOOR_PCT=0.75
PX3_ULTRA_CLEAN_PNL_PCT=7.50
PX3_ULTRA_CLEAN_CAPTURE_RATIO=0.85
PX3_CLEAN_TREND_PNL_PCT=2.50
PX3_CLEAN_TREND_CAPTURE_RATIO=0.90
PX3_EXCEPTIONAL_ENABLED=true
PX3_EXCEPTIONAL_MIN_CURRENT_PNL_PCT=1.00
PX3_EXCEPTIONAL_MIN_PREM_HWM_PCT=0.60
PX3_EXCEPTIONAL_MIN_PRICE_HWM_PCT=0.005
PX3_EXCEPTIONAL_PRICE_TRAIL_PCT=0.005
PX3_EXCEPTIONAL_PREM_FLOOR_PCT=0.75
PX3_THIN_LEAD_EXCEPTIONAL_ENABLED=true
PX3_THIN_LEAD_EXCEPTIONAL_MIN_CURRENT_PNL_PCT=1.00
PX3_THIN_LEAD_EXCEPTIONAL_MIN_PREM_HWM_PCT=0.60
PX3_THIN_LEAD_EXCEPTIONAL_MIN_PRICE_HWM_PCT=0.001
PX3_THIN_LEAD_EXCEPTIONAL_PRICE_TRAIL_PCT=0.004
PX3_THIN_LEAD_EXCEPTIONAL_PREM_FLOOR_PCT=0.25
PX3_HIGH_HWM_EXCEPTIONAL_ENABLED=true
PX3_HIGH_HWM_EXCEPTIONAL_MIN_CURRENT_PNL_PCT=1.00
PX3_HIGH_HWM_EXCEPTIONAL_MIN_PREM_HWM_PCT=1.25
PX3_HIGH_HWM_EXCEPTIONAL_MIN_PRICE_HWM_PCT=0.004
PX3_HIGH_HWM_EXCEPTIONAL_PRICE_TRAIL_PCT=0.004
PX3_HIGH_HWM_EXCEPTIONAL_PREM_FLOOR_PCT=0.50
PX3_MFE_CONFIRM_ENABLED=true
PX3_MFE_CONFIRM_MIN_CURRENT_PNL_PCT=0.20
PX3_MFE_CONFIRM_MIN_PREM_HWM_PCT=0.30
PX3_MFE_CONFIRM_MIN_PRICE_HWM_PCT=0.0025
PX3_MFE_CONFIRM_NORMAL_MIN_ENTRY_AGE_SECONDS=1200
PX3_MFE_CONFIRM_NORMAL_MAX_ENTRY_AGE_SECONDS=1800
PX3_MFE_CONFIRM_DELTA_PCT=0.15
PX3_MFE_CONFIRM_WINDOW_SECONDS=120
PX3_MFE_CONFIRM_THIN_RECOVERY_ENABLED=true
PX3_MFE_CONFIRM_THIN_RECOVERY_MIN_CURRENT_PNL_PCT=0.15
PX3_MFE_CONFIRM_THIN_RECOVERY_DEADZONE_MIN_PNL_PCT=0.20
PX3_MFE_CONFIRM_THIN_RECOVERY_DEADZONE_MAX_PNL_PCT=0.35
PX3_MFE_CONFIRM_THIN_RECOVERY_MIN_PRICE_HWM_PCT=0.0005
PX3_MFE_CONFIRM_THIN_RECOVERY_MAX_PRICE_HWM_PCT=0.0025
PX3_MFE_CONFIRM_THIN_RECOVERY_MAX_PREM_HWM_PCT=0.75
PX3_MFE_CONFIRM_THIN_RECOVERY_WINDOW_SECONDS=600
PX3_MFE_CONFIRM_THIN_RECOVERY_FLOOR_GIVEBACK_PCT=0.10
PX3_MFE_CONFIRM_THIN_RECOVERY_RUNNER_GIVEBACK_PCT=0.50
PX3_MFE_CONFIRM_STRONG_RECOVERY_MIN_CURRENT_PNL_PCT=0.29
PX3_MFE_CONFIRM_STRONG_UND_HWM_PCT=0.010
PX3_MFE_CONFIRM_RECOVERY_MAX_ENTRY_AGE_SECONDS=1800
PX3_MFE_CONFIRM_RECOVERY_WINDOW_SECONDS=900
PX3_MFE_CONFIRM_RECOVERY_FLOOR_GIVEBACK_PCT=0.20
PX3_MFE_CONFIRM_PRICE_TRAIL_PCT=0.004
PX3_MFE_CONFIRM_PREM_FLOOR_PCT=0.30
PX3_MFE_CONFIRM_RUNNER_GIVEBACK_PCT=0.50
OPTION_SINGLE_STOP_ENABLED=true
OPTION_SINGLE_STOP_PCT=0.008
OPTION_SINGLE_STOP_BEFORE_ARM_TRAIL=true
OPTION_SINGLE_PREMIUM_STOP_ENABLED=true
OPTION_SINGLE_PREMIUM_STOP_PCT=0.60
OPTION_SINGLE_PREMIUM_STOP_MAX_MFE_PCT=0.15
OPTION_SINGLE_PREMIUM_STOP_BEFORE_ARM=true
OPTION_SINGLE_PREMIUM_STOP_SETTLE_AT_STOP=true
OPTION_SINGLE_NONSTARTER_STOP_ENABLED=true
OPTION_SINGLE_NONSTARTER_STOP_PCT=0.40
OPTION_SINGLE_NONSTARTER_STOP_MAX_MFE_PCT=0.10
OPTION_SINGLE_NONSTARTER_STOP_MIN_HOLD_MINUTES=20
OPTION_SINGLE_NONSTARTER_STOP_BEFORE_ARM=true
OPTION_SINGLE_NONSTARTER_STOP_SETTLE_AT_STOP=true
OPTION_SINGLE_PRICE_RUNNER_ENABLED=true
OPTION_SINGLE_PRICE_RUNNER_ARM_PRICE_PCT=0.0005
OPTION_SINGLE_PRICE_RUNNER_CONFIRM_TICKS=3
OPTION_SINGLE_PRICE_RUNNER_TRAIL_PCT=0.002
SINGLE_RUNNER_WIDE_TRAIL_MFE_PCT=0.60
SINGLE_RUNNER_WIDE_TRAIL_PCT=0.005
OPTION_SINGLE_PRICE_RUNNER_TAKE_PROFIT_PCT=0
SINGLE_RUNNER_PREM_FLOOR_PCT=0
SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_MAX_PCT=0
SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_PCT=0
OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED=true
OPTION_PATH_V2_RUNNER_CAPTURE_ARM_MFE=3.50
OPTION_PATH_V2_RUNNER_CAPTURE_TIMED_MIN_CURRENT_RETURN=4.00
OPTION_PATH_V2_RUNNER_CAPTURE_TIMED_MIN_PEAK_RATIO=0.50
OPTION_PATH_V2_RUNNER_CAPTURE_TIMED_MIN_ARM_AGE_SECONDS=60
OPTION_PATH_V2_RUNNER_CAPTURE_FLOOR_MIN_AGE_SECONDS=600
OPTION_PATH_V2_RUNNER_CAPTURE_FLOOR_PEAK_RATIO=0.80
OPTION_BREAKEVEN_EXIT_ENABLED=true
OPTION_BREAKEVEN_EXIT_MINUTES=60
OPTION_BREAKEVEN_EXIT_MAX_PNL_PCT=0.02
OPTION_SCRATCH_BELOW_PNL_PCT=-0.0005
OPTION_SCRATCH_EXIT_MINUTES=62
# Moon hold (Aug 12 H2) removed Aug 17 — +60% MFE → 0.50% price_trail.
# Path refresh (moon-hold force caps — keep under E*TRADE 14k/hr)
# Live path chain-skew is discovery-async; owned eval is cache-only.
0DTE_PATH_CHAIN_SKEW_REFRESH_SEC=45
0DTE_PATH_CHAIN_SKEW_REFRESH_MAX_PER_TICK=2
0DTE_PATH_CHAIN_SKEW_REFRESH_MAX_PER_DISCOVERY=2
0DTE_PATH_CHAIN_SKEW_FORCE_MAX_PER_TICK=4
0DTE_PATH_SESSION_TECH_REFRESH_SEC=60
0DTE_PATH_SESSION_TECH_REFRESH_MAX_PER_TICK=4
0DTE_PATH_SESSION_TECH_FORCE_MAX_PER_TICK=4
0DTE_QG_CHAIN_CACHE_WHILE_OPEN_SEC=600
0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN=8
0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN=2
```

**ORB identity normalization (Jun 30):** easy0DTE `single_leg` rows promote to **`is_orb_0dte=true`** when normalized metadata has `strategy_type=orb_0dte` or `source_path=easy0dte`.

#### Generic `OPTION_STEALTH_*` (both paths)

```bash
OPTION_STEALTH_BREAKEVEN_TRIGGER_PCT=0.25
OPTION_STEALTH_BREAKEVEN_LOCK_PCT=0.08
OPTION_STEALTH_EARLY_BE_LOCK_PCT=0.04
OPTION_STEALTH_DELTA_ITM_THRESHOLD=0.60
OPTION_STEALTH_DELTA_ATM_THRESHOLD=0.40
OPTION_STEALTH_BE_TRIGGER_ITM_PCT=0.15
OPTION_STEALTH_BE_TRIGGER_ATM_PCT=0.20
OPTION_STEALTH_BE_TRIGGER_OTM_PCT=0.28
OPTION_STEALTH_MIN_SECONDS_BEFORE_BE=60
OPTION_STEALTH_REQUIRE_NEW_HWM_FOR_BE=true
OPTION_STEALTH_TRAILING_TRIGGER_PCT=0.35
OPTION_STEALTH_BASE_TRAILING_PCT=0.22
OPTION_STEALTH_MICRO_LOCK_ENABLED=false
OPTION_STEALTH_MICRO_LOCK_TRIGGER_PCT=0.08
OPTION_STEALTH_MICRO_LOCK_LEVEL_PCT=0.01
# disable_tp_ladder=True hardcoded (OPTION_DISABLE_TP_LADDER removed)
OPTION_MFE_TRAILING_STOP_ENABLED=false
OPTION_STEALTH_EXIT_PROFIT_FLOOR_ENABLED=false
OPTION_STEALTH_EXIT_PROFIT_TARGET_ENABLED=false
OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA=true
OPTION_STEALTH_STRUCTURE_INVALIDATION_ENABLED=true
```

#### ORB1 Breakout overlays (`strategy_type=orb_0dte`)

```bash
OPTION_STEALTH_ORB_BE_TRIGGER_MULT=0.75
OPTION_STEALTH_ORB_TRAILING_TRIGGER_MULT=0.85
OPTION_STEALTH_ORB_MICRO_LOCK_TRIGGER_PCT=0.06
OPTION_STEALTH_ORB_SINGLE_TRUSTED_DEGRADED_HWM_ENABLE=true
OPTION_STEALTH_ORB_NO_PROGRESS_SINGLE_RELAX_EXIT_GRADE=true
```

Monitor cadence: **`0DTE_POSITION_MONITOR_INTERVAL_SEC=2`** (shared ORB + Trendline + Breakout). Unified spine: `_monitor_loop` → `maybe_tick` → Live `BrokerPositionSynchronizer` → exact OCC batch (open priority) → **`monitor_0dte_options_positions()`** (pending fills + trail-armed first; **1200ms** eval budget) → PremiumEngine → ORB-like / Trendline `stealth.evaluate` + `exit_latch` dispatch. Fill enroll **flags** `_0dte_force_monitor_tick` + `request_wake`. 2s `_ul_refresh_loop` keeps the UL cache (local: no 4s cancel; serving `00624-8bn` still 4s-cancels). Persist is JSONL **5s** / daily JSON **45s**. Chain bridge only when exact incomplete (deferrable). Grep: **`UNIFIED_0DTE_MONITOR`**, **`BROKER_POSITION_SYNC`**, **`EXACT_CONTRACT_QUOTE`**, **`OPTION_POSITION_MONITOR_TICK`**, **`premium_source=exact`**, **`fill_enroll_flag_only`**, **`PATH_V2_RESTORE`**. Canonical: [PositionMonitoring.md](PositionMonitoring.md) · ladders: [0DTEProfitCapture.md §5](0DTEProfitCapture.md#5-orb-0dte-profile-summary).

#### Trendline 0DTE overlays (`strategy_type=trendline_0dte`) — spreads + May 27 desk tuning

**Single-leg lotto (default path):** exit profile resolved via **unified `OPTION_*` / `OPTION_SINGLE_*`** — same as ORB1 Breakout singles (Jul 1, 2026).

**Spreads / structure paths:** TLINE archetype overlays for spreads (**IONQ**, **TSLA**, **ASTS**, **RKLB**, etc.). Structure invalidation evaluate path **removed** Jul 1, 2026 — `allow_underlying_structure_exit=false` on all profiles.

```bash
# --- Trendline overlays (spreads / trail tiers; profit_lock removed Jul 1) ---
OPTION_STEALTH_TLINE_MICRO_LOCK_TRIGGER_PCT=0.035
OPTION_STEALTH_TLINE_MICRO_LOCK_LEVEL_PCT=0.01
TRENDLINE_TRAIL_ACTIVATE_PCT=0.28
TRENDLINE_IMPULSE_TRAIL_ACTIVATE_PCT=0.24
OPTION_STEALTH_TLINE_BASE_TRAILING_PCT=0.22
OPTION_STEALTH_TLINE_AGGRESSIVE_TRIGGER_PCT=0.45
OPTION_STEALTH_TLINE_AGGRESSIVE_TRAILING_PCT=0.18
OPTION_STEALTH_TLINE_EXPLOSIVE_TRIGGER_PCT=0.55
OPTION_STEALTH_TLINE_EXPLOSIVE_TRAILING_PCT=0.14
OPTION_STEALTH_TLINE_RUNNER_TRIGGER_PCT=0.85
OPTION_STEALTH_TLINE_RUNNER_TRAILING_PCT=0.10
OPTION_STEALTH_TLINE_IMPULSE_TRAILING_PCT=0.12
# --- BE / giveback / timeouts ---
OPTION_STEALTH_TLINE_BE_TRIGGER_MULT=0.50
OPTION_STEALTH_TLINE_BE_TRIGGER_LOW_DELTA_PCT=0.12
OPTION_STEALTH_TLINE_BE_GIVEBACK_EXIT_DRAWDOWN_PCT=0.05
OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES=6   # spreads only — singles use §2b 60 min
OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES=240        # spreads only — singles use §2b 150 min
OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT=0.08
OPTION_STEALTH_TLINE_MIN_SECONDS_BEFORE_BE=60
```

Monitor cadence: **`0DTE_POSITION_MONITOR_INTERVAL_SEC=2`** (same key as ORB). Ladder + chart mapping: [0DTEProfitCapture.md §13](0DTEProfitCapture.md#13-desk-calibration-may-27-trendline-charts).

#### Spread (ORB debit/credit)

```bash
OPTION_STEALTH_SPREAD_BE_TRIGGER_PCT=0.18
OPTION_STEALTH_SPREAD_BE_LOCK_PCT=0.04
OPTION_STEALTH_SPREAD_TRAILING_TRIGGER_PCT=0.28
OPTION_STEALTH_SPREAD_BASE_TRAILING_PCT=0.12
OPTION_STEALTH_SPREAD_TIME_EXIT_MINUTES=9
OPTION_STEALTH_SPREAD_NO_PROGRESS_EXIT_MINUTES=6
```

- **Normalized options metadata:** Executors attach **`metadata["normalized_options"]`**. `validate_normalized_options_for_stealth` before **`register_on_open`**; grep `OPTIONS_STEALTH | stage=normalized_metadata_used`.
- **Delta-aware BE:** \|delta\| → ITM/ATM/OTM → `OPTION_STEALTH_BE_TRIGGER_*_PCT`; Trendline scales by **`OPTION_STEALTH_TLINE_BE_TRIGGER_MULT`** (spreads / non-unified paths).
- **0DTE singles:** **`breakeven`** ≥**60m** **−0.05%…&lt;+2%** · **`scratch`** ≥**62m** **&lt;−0.05%** when never ARM_TRAIL-armed (`OPTION_BREAKEVEN_EXIT_*` · `OPTION_SCRATCH_*`). Primary winner path (Aug 17 PC-PX2): **ARM +30% → stall-bank / 20pp lock@200% / BE decision at 0% (fill = live bid) / 0.50% trail after und≥0.5% and prem≥60%** · leftover ≤ **$10** dumper class · required 12:30 `mfe_trail` −10pp · **moon hold Removed**. ARM-tick thin TP30 **off**. **MFE trail all-day config-off · Hold@0.55 removed.** Premium trail / micro-lock / profit_floor **disabled**.
- **BE giveback (spreads / legacy floor path):** `OPTION_STEALTH_TLINE_BE_GIVEBACK_EXIT_DRAWDOWN_PCT` — exit when floor breached and MFE giveback exceeds threshold (`TRENDLINE_BREAKEVEN_EXIT_GIVEBACK`).

**Rev 00364–00367:**

| Key / behavior | Purpose |
|----------------|---------|
| `OPTION_STEALTH_TLINE_BE_GIVEBACK_EXIT_DRAWDOWN_PCT=0.05` | Premium giveback exit (structure may still be valid) |
| `OPTION_BREAKEVEN_EXIT_*` **60m / −0.05%…&lt;+2%** · scratch **62m / &lt;−0.05%** | Unified 0DTE single-leg BE/scratch (ORB + Trendline) |
| `OPTION_SINGLE_MFE_TRAIL_*` **10% / 10pp** | Config-off retention trail (reason `mfe_giveback`) |
| `OPTION_STEALTH_TLINE_MICRO_LOCK_*` **0.035 / +1%** | Micro-lock (disabled on 0DTE singles Jul 1) |
| `TRENDLINE_TRAIL_ACTIVATE_PCT` **0.28** | Drift trail arms (premium MFE) |
| `OPTION_STEALTH_ORB_*` | ORB spread overlays / monitor helpers |
| `_ensure_*_options_stealth_engine()` | Monitor lazy init with full env (**00365**) |
| `TRENDLINE_*` (Trendline0DTE.env) | Post-draw entry — **core 9** + **Jun 4 desk** + **Phase 2** keys, separate from exit stealth |

### **Telemetry, priority optimizer & profit optimizer (`Shared.env` §6–§7)**

**Location:** `configs/Shared.env` — **§6a** priority optimizer snapshots · **§6b** telemetry / exit monitoring · **§7** profit optimizer. **Cloud policy:** [Cloud.md § Telemetry & GCS data plane](Cloud.md#telemetry--gcs-data-plane-may-2026) · **Data plane map:** [Data.md § Telemetry & retention bounds](Data.md#telemetry--retention-bounds).

| Key | Default | Purpose |
|-----|---------|---------|
| `ENABLE_EXECUTION_FEATURE_SNAPSHOT` | `true` | Master switch for 7:30 execution-window JSON |
| `EXECUTION_SNAPSHOT_KEEP_SESSIONS` | `2` | Session dates kept by active in-app prune; retrieve daily for local archive |
| `EXECUTION_SNAPSHOT_SLIM` | `true` | Omit duplicate `features_89_*` from GCS payload |
| `EXECUTION_SNAPSHOT_LOCAL` | `false` | Cloud Run: do not write snapshots to container disk |
| `EXIT_MONITORING_ENABLED` | `false` | **Off in production** — largest historical GCS writer when `true` |
| `EXIT_MONITORING_MAX_RECORDS_PER_SYMBOL` | `120` | Cap rows per symbol per day file (when enabled) |
| `EXIT_MONITORING_MAX_BLOB_BYTES` | `512000` | Max JSON blob size per symbol file (when enabled) |
| `EXIT_MONITORING_SAMPLE_EVERY` | `6` | Collect every Nth tick unless exit-factor flags fire |
| `TELEMETRY_JSONL_MAX_BYTES` | `8388608` (8 MiB) | `trendline_features.jsonl` trim threshold |
| `TELEMETRY_JSONL_TAIL_LINES` | `10000` | Lines kept after trim |
| `TELEMETRY_DISK_WARN_MB` | `64` | Startup warning if local telemetry exceeds MiB |
| `MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES` | `400` | Rolling cap on closed rows in `mock_options_history.json`; **`0`** = off |
| `PRIORITY_OPTIMIZER_CLEAR_RETRIEVED_ON_STARTUP` | `true` | Cloud: clear `priority_optimizer/retrieved_data` on boot |
| `ENABLE_TRENDLINE_BREAK_SNAPSHOT` | `true` | One row per symbol at `first_break_detected` → daily `_breaks.json` |
| `ENABLE_TRENDLINE_LEGACY_JSONL_DATASET` | `false` | Retired — `trendline_priority_optimizer_dataset/*_events.jsonl` no longer written live |
| `0DTE_PRIORITY_COLLECTOR_ENABLED` | `false` | Retired — use `orb_0dte_execution_window_input` unified snapshots |

**Modules:** `modules/execution_feature_snapshot.py`, `modules/prime_exit_monitoring_collector.py`, `modules/telemetry_jsonl_utils.py`, `modules/telemetry_startup.py`, `modules/execution_telemetry.py`, `easy0DTE/modules/mock_options_executor.py`, `easyTrendline/trendline_feature_logger.py`.

**Scripts:** `scripts/apply_gcs_data_lifecycle.sh`, `scripts/cleanup_priority_optimizer_data.sh`.

**Exit monitoring:** Set `EXIT_MONITORING_ENABLED=true` only for a **single tuning session**, then revert to `false`. Do not leave enabled on scale-to-zero Cloud Run long-term.

**Snapshot retention:** `EXECUTION_SNAPSHOT_KEEP_SESSIONS=2` in `Shared.env` §6. Pruned on **write** (`execution_feature_snapshot.py`) and at **startup** (`telemetry_startup.prune_local_execution_snapshots()`). Pull sessions locally after each trading day for durable research history.

**Ledger rolling retention:** `MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES` keeps the **most recent N closed trades** in `research_app/mock_options_history.json` only. Account balance, weekly/daily stats, and open positions are **not** pruned. Export older rows locally before they roll off if needed for research.

**ORB entry execution telemetry:** fills attach `metadata.execution_payload` (spread %, slippage vs mid, bid/ask/mid). Grep: `EXECUTION_FILL_SUMMARY` · `MOCK_OPTIONS_LEDGER | pruned`.

**GCS hygiene:** `scripts/cleanup_priority_optimizer_data.sh` dedupes execution snapshots by stage/day; lifecycle rules in `scripts/gcs_lifecycle_easy_etrade_data.json` — apply with `scripts/apply_gcs_data_lifecycle.sh`.

**Premium source priority (0DTE single-leg — ORB + Trendline):** When `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS=true`, fresh live quotes resolve to **`bid_executable`** (STC @ bid) for monitor/exit; demo entry sim uses **ask** via `option_executable_pricing`. Fallback chain: **bid → mid → last → cached → synthetic** (`resolve_option_price`). Profit/BE/trail use **`pnl_pct` from executable premium**; underlying is not the BE trigger.

**Degraded/outage safety:** BE arms on **live** or **trusted-degraded** premium marks when path rules allow. Forced exit on true no-data past `TRENDLINE_OPTION_FORCE_EXIT_NO_DATA_SECONDS` / `OPTION_STEALTH_NO_DATA_OUTAGE_SECONDS` when configured.

**Stop research keys (Jun 18 — spec only, not in `Shared.env` yet):** Same proposed keys as LIVE — see [LIVE Configs.md § stop research](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Configs.md) · [0DTEStopLossImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/0DTEStopLossImprovementGuide.md).

**Premium modeling:** The `delta_estimate` fallback uses underlying move, entry delta, and strike moneyness drift with jump clamps. Sensitivity control:

```bash
TRENDLINE_OPTION_SENSITIVITY=2.5
```

**0DTE open-position monitor** (ORB + Trendline + Breakout share one cadence — [PositionMonitoring.md](PositionMonitoring.md)):

```bash
0DTE_UNIFIED_POSITION_MONITOR_ENABLED=true
0DTE_POSITION_MONITOR_INTERVAL_SEC=2          # sole cadence knob (floor 0.5)
0DTE_EXIT_QUOTE_MODE=exact_contract_batch
0DTE_EXIT_QUOTE_TIMEOUT_SEC=1.5
0DTE_EXIT_QUOTE_MAX_AGE_SEC=8
0DTE_EXIT_QUOTE_BATCH_CONCURRENCY=4
0DTE_DISCOVERY_MAX_QUOTES_WHILE_OPEN=24      # same 24 rotate while open; never extras-first
0DTE_MONITOR_EVAL_BUDGET_MS=1200             # pending fills never skip
0DTE_UNDERLYING_FRESH_MAX_MS=4000            # evaluate cache: age ≤4s = fresh
0DTE_UNDERLYING_LAST_GOOD_MAX_MS=8000        # age ≤8s = last_good/stale; >8s always unavailable
0DTE_UNDERLYING_OWNED_REFRESH_MAX=25         # skip_cache live last; oldest first; 0 = unlimited
0DTE_PATH_REFRESH_SKIP_CYCLE_MS=8000         # skip session-tech / chain-skew only past last-good quote age
0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN=0          # no closed PO OCC on the owned 2s batch
0DTE_FORCE_CHAIN_REFRESH_BRIDGE=true         # bridge allowed but deferrable while open
0DTE_STRICT_EXIT_GRADE_ENABLED=true
0DTE_LONG_EXIT_PRICE_FIELD=bid
0DTE_MFE_HWM_PRICE_FIELD=bid
0DTE_GIVEBACK_PRICE_FIELD=bid
0DTE_EXIT_LATCH_ENABLED=true
REQUIRE_LIVE_OPTION_DATA=true
0DTE_ALLOW_SYNTHETIC_EXIT_QUOTES=false
0DTE_DEMO_SYNTHETIC_CHAIN=false
# Live ownership SLAs
0DTE_OWNERSHIP_GRACE_SEC=45
0DTE_OWNERSHIP_SYNC_TARGET_SEC=2
0DTE_OWNERSHIP_BLOCK_WARNING_SEC=3
0DTE_OWNERSHIP_BLOCK_CRITICAL_SEC=5
0DTE_OWNERSHIP_URGENT_SYNC_TIMEOUT_SEC=2
# Market quote pacing (Data.env)
ETRADE_BATCH_QUOTE_SIZE=25
ETRADE_MARKET_API_INTER_REQUEST_SEC=0.28
```

- **Where keys live:** `configs/Shared.env` + `ORB0DTE.env` mirrors.
- **Scheduler:** Dedicated `_monitor_loop` calls the same `Unified0DTEPositionMonitor.maybe_tick` and always pauses. An empty book clears `_0dte_force_monitor_tick`. Fill enroll **flags** that flag and `request_wake` only (no second full-book tick). Tick ids are `canonical_monitor_tick_id` (`u0dte_<ms>` only; Trendline/ORB fast loops mint the same way). Watchdog restarts the loop; it does not evaluate beside a live tick. Legacy ORB/TL fast monitors early-return when unified on.
- **Quotes:** Exact OCC is priority 0 on the shared pacer. The OCC date is the listed `selected_expiry` (same-day or nearest weekly), not “today” by policy. A slow exit quote does not cancel the 24-name scan or the ORB1, Trendline, and ORB2 watches. `open_quote_latency_high` still limits background chain work (stale-chain refresh, path-chain stamps, sampler cap, prewarm). Chain bridge still waits when owned exact quotes are incomplete. Quote batch concurrency **4**. Local source gives the exit monitor up to 0.8s, then the quote, then up to 0.5s, then the watches. Target tick is **2s** (`0DTE_POSITION_MONITOR_INTERVAL_SEC`); Trade Profile wide / 50-cap has not held that on this Market API budget — **3s** at `0DTE_MONITOR_WIDE_OPEN_COUNT=20`. Dead same-day OCC must not retry 1002 spellings.
- **Discovery:** Next **24** of `0dte_list` every **10s** (`discovery_interval_sec` floor 10). Cursor **always advances**. 118 names → every symbol at least once in **≤50s** when each scan finishes inside 10s. CONFIRM/READY/body-beyond do **not** steal slots. The same task evaluates those quotes and sends a break to Trade Profile. `0DTE_DISCOVERY_MAX_OPEN_QUOTE_LATENCY_MS=4000` paces option-chain work only, and only when the exit quote is stale. `already_running` does not advance the 10s timer. Do not set the interval under 10s.
- **Monitor / exit / flight recorder:** Tick is **2s**, or **3s** at `0DTE_MONITOR_WIDE_OPEN_COUNT=20` (`0DTE_MONITOR_WIDE_INTERVAL_SEC`). **`await run_fair_owned_eval_tick`** exit-checks every open name and yields between them. **`0DTE_MONITOR_EVAL_BUDGET_MS=1200`** (1800 at 20+) is logged when the tick runs long; it does not skip a name. Each PATH_V2 eval writes JSONL (`evaluated=true` bypasses the 2s sample throttle; `force_path_sample` still set). Compact rows carry `monitor_tick_id` and `prem_vel_10`. Closed-trade `exit_bid` is the frozen `entry_option_symbol` path bid. Underlying is **cache-only**; a 2s `_ul_refresh_loop` calls `refresh_underlying_spot_cache_async`. Age wins — ≤4000ms usable, ≤8000ms last_good/`stale`, **>8000ms always unavailable**. Evaluate must **not** call broker `get_quote`/`get_quotes` for the equity. JSONL persist is **`po-persist`** at **5s**; daily metrics JSON stays **45s** (not inside `POSITION_EVAL_MS`). Path chain-skew / session-tech **skipped** when last `cycle_ms` > `0DTE_PATH_REFRESH_SKIP_CYCLE_MS`.
- **Demo vs Live:** Same evaluate; Demo closes on `research_app/mock_options_history.json`; Live STC + `research_app/live_options_history.json` for closed rows.
- Grep: **`UNIFIED_0DTE_MONITOR`**, **`EXACT_CONTRACT_QUOTE`**, **`OPTION_POSITION_MONITOR_START`**, **`EXIT_LATCHED`**, **`BROKER_POSITION_SYNC`**, **`premium_source=exact`**, **`fill_enroll_flag_only`**, **`POSITION_EVAL_MS`**, **`UNDERLYING_CACHE`**, **`PO_PERSIST`**.

**Demo ledger ghost hygiene:** `MockOptionsExecutor._purge_closed_ids_from_active()` before GCS save; GCS active merge skips IDs in `closed_positions`; persistent no-quote ghosts force-ledger-close (`DEMO_0DTE_GHOST_PURGED`).

**Fallback** if `trendline_options_stealth` is not initialized (legacy path only): fallback behavior remains in code, but `TRENDLINE_EXIT_TP_PCT` / `TRENDLINE_EXIT_SL_PCT` were removed from repo env cleanup because they were not actively referenced in the current Python path audit.

**Alerts / heartbeat** (Telegram monitor alert cadence only):
```bash
TRENDLINE_MONITOR_ALERT_ENABLED=false
TRENDLINE_MONITOR_ALERT_INTERVAL_SEC=300
```

**Open-position stealth cadence:** ORB + Trendline + Breakout share **`0DTE_POSITION_MONITOR_INTERVAL_SEC`** (default **2s**). Telegram `TRENDLINE_MONITOR_ALERT_INTERVAL_SEC` is independent (alert throttling only).

**`TrendlineConfig`:** explicit env-loaded fields via `load_trendline_config_from_env` (post-draw TRENDLINE_* + Jun 4 desk + Phase 2 Priority Gate + Grade A sustain lane + liquid reversal lane + dual-side geometry Phase 1). Mission contest fields **removed** Aug 11. Legacy post-draw fields resolve via **`TrendlineConfig.__getattr__`** from `trendline_legacy_config_defaults.py` when `TRENDLINE_BREAK_MODE=false` (compare-only). See [0DTETrendline.md §13](0DTETrendline.md#13-configuration).

**Reference doc**: [0DTETrendline.md](0DTETrendline.md) — simple break mode, module map, 7:30 selector v3, **`OPTION_STEALTH_TLINE_*`**, unified monitor dispatcher.


---

## 🛡️ **Risk Management Configuration**

**Location**: `configs/Risk.env` — **Phase 2 trim (Jun 18):** §A global ceilings · §C ORB SO `STEALTH_*` · ADV lookback `LIQUIDITY_GUARD_ADV_LOOKBACK_DAYS` (0DTE Liquidity Guard). Slip Guard / `SLIP_GUARD_*` removed Aug 3, 2026. Removed earlier: VAR/stress/regime blocks and duplicate portfolio keys (those live in `ORBSO.env` §6).

### **Portfolio Risk Limits**

```bash
# Portfolio Risk
MAX_PORTFOLIO_RISK_PCT=80.0
MAX_SINGLE_POSITION_RISK_PCT=35.0
MAX_CORRELATED_POSITIONS=3

# Daily Risk Limits
# Research default: disabled so entries continue all day for review.
# Restore a positive value such as 2.0 before live-capital trading.
MAX_DAILY_LOSS_PCT=0.0
MAX_DAILY_TRADES=200
MAX_DAILY_VOLUME_PCT=10.0

# Weekly Risk Limits
MAX_WEEKLY_LOSS_PCT=5.0
MAX_WEEKLY_LOSS_AMOUNT=5000.0
MAX_WEEKLY_TRADES=200

# Monthly Risk Limits
MAX_MONTHLY_LOSS_PCT=10.0
MAX_MONTHLY_LOSS_AMOUNT=20000.0
MAX_MONTHLY_TRADES=500
```

### **Position Risk Management**

```bash
# Stop Loss Settings
STOP_LOSS_ENABLED=true
STOP_LOSS_ATR_MULTIPLIER=1.5
STOP_LOSS_PCT=3.0

# Trailing Stop Settings
TRAILING_STOP_ENABLED=true
TRAILING_STOP_ATR_MULTIPLIER=2.0

# Take Profit Settings
TAKE_PROFIT_ENABLED=true
TAKE_PROFIT_ATR_MULTIPLIER=3.0
TAKE_PROFIT_PCT=5.0
```

### **Path consume (Aug 16) — cooldown vs expire vs re-entry**

Owned in `configs/Breakout0DTE.env` (1h duration for ORB1/ORB2) and Trendline simple-mode expire.

```bash
# Shared 1h duration — ORB1 reject · ORB2 reject/fill
BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS=3600
# ORB2 only: after fill, require inside-ORB2 reclaim before same H/L can fire again
BREAKOUT_ALLOW_REENTRY_AFTER_PROFITABLE_EXIT=true
BREAKOUT_REENTRY_AFTER_PROFIT_MIN_PNL_PCT=0.0
TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC=10800
```

| Path | After reject | After fill |
|------|--------------|------------|
| **ORB1** | **1h** same-side cooldown; other H/L still eligible until **07:25** | **One trade** — symbol stays executed; **no** re-entry, **no** second ORB1 after exit |
| **ORB2 BO** | **1h** same-side + must print **inside** ORB2 before that H/L can break again | Same as reject (fill does **not** day-lock the symbol) |
| **TL0–TL4** | Hard QG / post-exec **expire that slot/side envelope** (`EXPIRED`); A1/A2 persist | Envelope stays **EXECUTED**; no same-envelope re-entry |

Opposite CALL/PUT stays eligible on every path. Modules: `path_exec_day_exclude.py` · `breakout_watch.py` · `orb1_0dte_watch.py` · `expire_simple_candidate_after_attempt`. Detail: [0DTETrendline.md](0DTETrendline.md) · [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · [0DTERisk §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit).

### **Stealth Trailing Stop System** (65+ Configurable Settings)

**Optimized Parameters** (Rev 00196):
```bash
# Breakeven Settings
STEALTH_BREAKEVEN_THRESHOLD=0.0075  # 0.75% (optimized from 0.5%)
STEALTH_MIN_BREAKEVEN_ACTIVATION_MINUTES=6.4  # Optimized from 3.5 minutes
STEALTH_BREAKEVEN_OFFSET=0.002  # 0.2% offset above breakeven

# Trailing Stop Settings
STEALTH_BASE_TRAILING=0.015  # 1.5% base trailing distance
STEALTH_MIN_TRAILING=0.010  # 1.0% minimum trailing
STEALTH_MAX_TRAILING=0.025  # 2.5% maximum trailing
STEALTH_MIN_PROFIT_FOR_TRAILING=0.007  # 0.7% profit activates trailing (optimized from 0.5%)
STEALTH_MIN_TRAILING_ACTIVATION_MINUTES=6.4  # Optimized from 3.5 minutes

# Explosive Move Settings
STEALTH_EXPLOSIVE_TRAILING=0.040  # 4.0% for explosive moves
STEALTH_MOMENTUM_GAIN_THRESHOLD=0.003  # +0.3% in 15 min = explosive
STEALTH_MOMENTUM_LOOKBACK_MINUTES=15.0
STEALTH_TRENDING_TAKE_PROFIT=0.12  # 12% trending moves

# ORB SO low-volume policy
STEALTH_LOW_LIQUIDITY_EXIT=0.5  # Legacy threshold; ignored for ORB SO unless explicitly enabled
STEALTH_SO_LOW_LIQUIDITY_EXIT_ENABLED=false  # Volume is an entry/rank + PP input, not a standalone SO exit
```

**Volatility-Based Trailing Tiers**:
```bash
STEALTH_TRAILING_VOL_EXTREME=0.025   # 2.5% trailing for >6% volatility
STEALTH_TRAILING_VOL_HIGH=0.020      # 2.0% trailing for 3-6% volatility
STEALTH_TRAILING_VOL_MODERATE=0.0175 # 1.75% trailing for 2-3% volatility
STEALTH_TRAILING_VOL_LOW=0.015       # 1.5% trailing for <2% volatility
STEALTH_VOL_THRESHOLD_EXTREME=6.0
STEALTH_VOL_THRESHOLD_HIGH=3.0
STEALTH_VOL_THRESHOLD_MODERATE=2.0
```

**Profit-Based Trailing Tiers**:
```bash
STEALTH_TRAILING_PROFIT_MAX=0.015    # 1.5% trailing for 12%+ profit
STEALTH_TRAILING_PROFIT_HIGH=0.020   # 2.0% trailing for 7-12% profit
STEALTH_TRAILING_PROFIT_MEDIUM=0.025 # 2.5% trailing for 3-7% profit
STEALTH_TRAILING_PROFIT_LOW=0.030    # 3.0% trailing for <3% profit
```

**Portfolio Health Check Thresholds**:
```bash
STEALTH_HEALTH_CHECK_WIN_RATE_THRESHOLD=35.0  # <35% win rate = red flag
STEALTH_HEALTH_CHECK_AVG_PNL_THRESHOLD=-0.005 # <-0.5% avg P&L = red flag
STEALTH_HEALTH_CHECK_MOMENTUM_THRESHOLD=40.0  # <40% momentum = red flag
STEALTH_HEALTH_CHECK_PEAK_THRESHOLD=0.008     # <0.8% avg peak = red flag
```

**Rapid Exit Thresholds**:
```bash
STEALTH_RAPID_EXIT_NO_MOMENTUM_MINUTES=15.0  # 15 minutes
STEALTH_RAPID_EXIT_NO_MOMENTUM_PEAK=0.003     # <0.3% peak
STEALTH_RAPID_EXIT_IMMEDIATE_START=5.0        # 5 minutes
STEALTH_RAPID_EXIT_IMMEDIATE_END=10.0         # 10 minutes
STEALTH_RAPID_EXIT_IMMEDIATE_PNL=-0.005       # <-0.5% P&L
```

**Entry Bar Protection**:
```bash
STEALTH_ENTRY_BAR_PROTECTION_EXTREME=0.080  # 8% protection for >6% volatility
STEALTH_ENTRY_BAR_PROTECTION_HIGH=0.050     # 5% protection for 3-6% volatility
STEALTH_ENTRY_BAR_PROTECTION_MODERATE=0.030  # 3% protection for 2-3% volatility
STEALTH_ENTRY_BAR_PROTECTION_LOW=0.020      # 2% protection for <2% volatility
STEALTH_ENTRY_BAR_PROTECTION_MINUTES=30.0   # Protection duration
```

### **Holiday Filtering**

**Location**: `modules/dynamic_holiday_calculator.py` — see [MarketCalendar.md](MarketCalendar.md). Optional static notes at `data/holidays_custom.json` (not the live skip list).

**Configuration**:
- Dynamic NYSE-style calendar for any year (weekend observed closes included)
- Prevents trading on exchange-closed holidays (`MARKET_CLOSED`)
- Prevents trading on market-open low-volume holidays (`LOW_VOLUME`)
- Prevents trading on the final weekday/session before a bank holiday or standalone low-volume day (`PRE_HOLIDAY`), including Friday before a Monday holiday. A listed Day Before … day does not add a second pre-holiday.
- Cloud Scheduler Good Morning is replaced by the Holiday / Pre-Holiday Telegram on those dates

**Settings**:
```bash
HOLIDAYS_ENABLED=true
HOLIDAYS_CUSTOM_PATH=data/holidays_custom.json
```

### **Red Day Filtering**

**Configuration:** `configs/ORBSO.env` — unified Portfolio Protection (`modules/portfolio_protection.py`, Rev **00369** + split-tape **00375**)

**Gate 3a triggers:**
- **Red Day Patterns 1–3:** RSI breadth + weak volume (legacy Rev 00176)
- **`protection_day`:** ≥**50%** below VWAP **and** median vol **&lt; 0.45**
- **`protection_day_split_tape`:** median VWAP dist **&lt; 2%** **and** median vol **&lt; 0.45** (VWAP coverage ≥ **80%**)

**On batch block:** ORB SO cleared · 0DTE CALL non-Tier-1 blocked · Tier-1 CALL + PUT allowed (PUT Convex floor **0.62** on Red Day)

**Settings** (legacy summary):
- **3-Pattern Detection**: Oversold, overbought, weak volume + RSI breadth
- **3-Tier Override System**: Primary (MACD+RS), Secondary (Solo MACD), Tertiary (VWAP Distance) — Pattern 2 only
- **Signal-Level Detection**: Two-layer protection (portfolio + signal level) — signal layer **disabled** by default
- **Direction-Aware** (0DTE): non-Tier-1 LONG rejected, Tier-1 LONG allowed, SHORT allowed on Red Days

---

## 💰 **Position Sizing Configuration**

**Canonical 0DTE desk source:** `configs/Account0DTE.env` — change slots / path caps / total together.  
**ORB SO ETF (separate):** `configs/ORBSO.env` + `configs/Risk.env`.  
**Detail:** [0DTERisk.md §1](0DTERisk.md#0dte-position-sizing--caps-one-place-to-edit) · [0DTERisk.md §14.3](0DTERisk.md#143-position-sizing--concurrency-canonical--edit-account0dteenv-first).

### **0DTE — slots, capital, open caps (grouped)**

```bash
# --- Account0DTE.env (canonical) ---
0DTE_DEMO_STARTING_BALANCE=100000    # RESEARCH 10/9 reseed. FINAL is 100000
0DTE_TRADING_CAPITAL_PCT=90          # → 10% sacred buffer
0DTE_MAX_POSITION_SIZE_PCT=35        # looser than the 1/N slot
0DTE_MAX_POSITION_SIZE_SUM=600       # Eric 10/9; 0 = no dollar sum cap
LIVE_MAX_CONTRACTS_PER_TRADE=0
0DTE_SLOT_COUNT=50                   # 50 = RESEARCH Demo, live 00599-lhd (FINAL Live Pilot = 7)
TOTAL_0DTE_MAX_POSITIONS=50
ORB_0DTE_MAX_POSITIONS=50
BREAKOUT_0DTE_MAX_POSITIONS=50
TRENDLINE_0DTE_MAX_POSITIONS=50
0DTE_MONITOR_MAX_SYMBOLS=50
0DTE_DEGRADED_WARN_RATIO=0.60
0DTE_DEGRADED_WARN_CONSECUTIVE=3
0DTE_DEGRADED_ENTRY_KILL_SWITCH_ENABLED=true
0DTE_DEGRADED_ENTRY_KILL_SWITCH_RATIO=0.60
0DTE_DEGRADED_ENTRY_KILL_SWITCH_CONSECUTIVE=3
# Do not set MAX_OPEN_POSITIONS, MAX_TOTAL_OPTION_POSITIONS,
# UNIFIED_0DTE_*_MAX_POSITIONS, TRENDLINE_SLOT_COUNT, or 0DTE_MAX_POSITIONS.
```

**Formula:** `slot_target = tradable_equity / 7`. Allocated capital is the minimum of that slot and cash after the buffer. No dollar sum cap. No contract-count cap.

**Signal load policy:** **ORB1** is **06:45–07:25 one-shot** (reject = 1h same-side; fill = one trade). **ORB2** reject/fill = 1h + inside-ORB2 re-entry (opposite edge open). TL soft maturation has a **300-second** entry window (`TRENDLINE_ENTRY_WINDOW_SEC=300`) then re-arms. All three paths use **Structure → Quality Gate → Confirm → PO Entry Snapshot → Enter Trade**. TL/BO use `0DTE_EXEC_SIGNAL_EXPIRE_MINUTES=1` for qualified signals waiting on capital/slots; TL TTL expiry **consumes that envelope**. TL/BO **CONFIRM/READY** are evaluated when they sit in this scan's 24-name rotate window (they do **not** pin the scan or skip WAIT_BREAK names). Trendline hard QG / post-exec rejects **expire that slot/side envelope** (sibling slots / opposite envelope stay eligible). **TL0 + ORB1** hard-stop at **07:25**. **07:25–07:30** is the ORB2 capture gap. **TL1–TL4** ARMED first-break clock **3h**. Discovery cutoff **12:30** · flatten **12:50** · EOD Telegram **12:55**.

**Dead / do not set:** `MAX_TRENDLINE_OPTION_SYMBOLS` · `TRENDLINE_MAX_POSITION_PCT` · `TARGET_TRENDLINE_ACTIVE` · `TRENDLINE_MAX_TRADES_PER_DAY` · `0DTE_LOTTO_SLEEVE_*`.

### **ORB SO ETF — capital & batch (not 0DTE slots)**

```bash
# ORBSO.env + Risk.env
SO_CAPITAL_PCT=90.0
ORR_CAPITAL_PCT=0.0
CASH_RESERVE_PCT=10.0               # SO path: SO + ORR + reserve = 100%
MAX_CONCURRENT_TRADES=15            # SO batch @ 7:30
MAX_CONCURRENT_POSITIONS=15         # keep aligned with SO intent
MAX_OPEN_POSITIONS=26               # Risk.env — all-path tracked ceiling
MAX_POSITION_SIZE_PCT=35.0          # Risk.env generic ceiling
```

Confidence / profit-scaling boosters for **ETF** stealth live in `Risk.env` / strategy presets — they do **not** size 0DTE option slots. See [SORisk.md](SORisk.md).

### **Position limits summary**

| Path | Cap key | Default | File |
|------|---------|--------:|------|
| ORB SO batch | `MAX_CONCURRENT_TRADES` | 15 | `ORBSO.env` |
| ORB1 Breakout funnel | `ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX` | 10 | `ORB0DTE.env` |
| ORB1 Breakout open | `ORB_0DTE_MAX_POSITIONS` | 10 | `Account0DTE.env` |
| Trendline open | `TRENDLINE_0DTE_MAX_POSITIONS` | 10 | `Account0DTE.env` |
| Total 0DTE open | `TOTAL_0DTE_MAX_POSITIONS` | 10 | `Account0DTE.env` |
| Monitor symbols | `0DTE_MONITOR_MAX_SYMBOLS` | 10 | `Account0DTE.env` |
| All paths tracked | `MAX_OPEN_POSITIONS` | 26 | `Risk.env` |
---

## 🔧 **Broker Configuration**

**Location**: `configs/Data.env` (+ `secretsprivate/` locally / Secret Manager in production)

### **Supported Brokers (Live product scope)**

| Broker | Status | Live cutover |
|--------|--------|--------------|
| **E*TRADE** | ✅ **Complete** — Demo + Live | ✅ Allowed |
| **Interactive Brokers** | 🚧 P2 planned — stub adapter | ❌ Blocked |
| **Charles Schwab** | 🚧 P2 planned — router token / provider+adapter pending | ❌ Blocked |
| **TastyTrade** | 🚧 P2 planned — stub adapter | ❌ Blocked |
| Robinhood / Alpaca / Tradier | Removed Jun 18 | Not in product roadmap |

**Current implementation:** E*TRADE only. IB, Schwab, and TastyTrade have account ID keys/stubs/placeholders and must fail closed; data plane and adapters are **not implemented**. Full matrix: [Brokers.md](Brokers.md).

```bash
# Canonical (Shared.env)
BROKER=etrade   # etrade | interactive_brokers | schwab | tastytrade

# Fallback (Data.env)
BROKER_TYPE=etrade

# Strategy-specific overrides (optional)
# ORB_BROKER_TYPE=etrade
# 0DTE_BROKER_TYPE=etrade
```

### **E*TRADE Configuration** ⭐ **Fully Developed**

**Trading Modes (E*TRADE only)**:
- **Demo Mode**: Real E*TRADE chains/quotes · mock execution · **`BrokerAdapter.submit_*` blocked** · GCS `research_app/*`
- **Live Mode**: Real chains · **`ETradeBrokerAdapter`** submit + fill reconcile · account **`ETRADE_0DTE_ACCOUNT_ID`** · ORB SO blocked via **`SO_LIVE_DISABLED`**

**Important**: E*TRADE does not have separate sandbox/production APIs. Both Demo and Live use the **production E*TRADE API**. Demo = Sim account + mock balances ($5k ORB SO default, $5k 0DTE, $5k Trendline). Live = live account, real money.

```bash
# Account IDs (see PrivateSecrets.md for actual values)
# These are your actual E*TRADE account IDs - store in PrivateSecrets.md
ETRADE_ORB_ACCOUNT_ID=your_orb_account_id_here      # ORB Strategy account
ETRADE_0DTE_ACCOUNT_ID=your_0dte_account_id_here    # Easy 0DTE account
ETRADE_DEFAULT_ACCOUNT_ID=your_default_account_id   # Default account (fallback)

# Account Names (optional, for reference only - not used by system)
ETRADE_ORB_ACCOUNT_NAME=Your Account Name
ETRADE_0DTE_ACCOUNT_NAME=Your Account Name

# API Settings
ETRADE_ENABLED=true
ETRADE_REAL_TIME_QUOTES=true
ETRADE_BATCH_QUOTES=true
ETRADE_DAILY_CALL_LIMIT=1180
ETRADE_RATE_LIMIT_CALLS_PER_MINUTE=100
```

**Account Selection Logic**:
1. Check strategy-specific account (e.g., `ETRADE_ORB_ACCOUNT_ID`)
2. If not set, check default account (e.g., `ETRADE_DEFAULT_ACCOUNT_ID`)
3. If not set, auto-select first available active account

**Account Configuration**:
- **ORB Strategy Account**: Separate account for ETF/stock trading (see PrivateSecrets.md)
- **Easy 0DTE Account**: Separate account for options trading (see PrivateSecrets.md)
- **Default Account**: Fallback account if strategy-specific not set (see PrivateSecrets.md)

**Broker Switching (when P2 adapters ship)**:
1. Set `BROKER=<broker>` in `Shared.env` and `BROKER_TYPE` in `Data.env`
2. Set broker-specific account IDs
3. Configure broker credentials in Secret Manager
4. Run broker certification + Live readiness probe for that broker
5. Deploy and verify `PRODUCTION_STARTUP_AUDIT | BROKER=<expected>`

**Today:** Keep `BROKER=etrade`. Do not switch to IB, Schwab, or TastyTrade for Live until P2 certification.

### **Data and Third-Party Source Configuration**

Broker config owns anything that can create, price, size, monitor, or reconcile a trade. Third-party data is explicitly opt-in by use case; it is never a general quote fallback.

| Data need | Source / config | Owner file | Rule |
|-----------|-----------------|------------|------|
| Broker truth | `BROKER`, `BROKER_TYPE`, `BROKER_DATA_ONLY=true` | `Shared.env` / `Data.env` | E*TRADE today; future selected broker |
| Same-day option chain request | `expiryYear` / `expiryMonth` / `expiryDay` + `noOfStrikes` | Code contract, docs here | ORB1 Breakout / TL / BO choose same-day first; else earliest listed (expire-date list + empty-day memo; no Sat/Sun 10031 walk) |
| Session tech history | `0DTE_SESSION_TECH_YF_1M_BACKFILL=true` | `ORB0DTE.env` | Yahoo 1m can fill RSI/MACD/VWAP depth only; volume stays broker quote-delta |
| TL1 A2 structure | `TRENDLINE_BUILD_YF_1M_BACKFILL=true` + `MAX_SYMBOLS` / `BATCH_SIZE` / `BATCH_SLEEP_SEC=0.5` | `Trendline0DTE.env` | Batched Yahoo RTH 1m [06:30–07:30] for A2 path only |
| TL2/TL3 hydrate | `TRENDLINE_WATCH_YF_1M_BACKFILL=true` + `MIN_BARS` / `MAX_SYMBOLS` / `BATCH_SIZE` / `BATCH_SLEEP_SEC` | `Trendline0DTE.env` | Hydrate before extension inject when `_bar_cache` is thin |
| Broker quote hydrate cap | `TRENDLINE_WATCH_HYDRATE_QUOTE_CAP=40` | `Trendline0DTE.env` | Broker quote pass before TL2/TL3 inject |
| Legacy daily Yahoo | `ETRADE_ALLOW_YAHOO_HISTORICAL=0` | `Data.env` / legacy code | Keep off unless a reviewed fallback requires it |

### **Interactive Brokers Configuration** (P2 — not Live-ready)

```bash
BROKER=interactive_brokers   # or BROKER_TYPE=ib
IB_ENABLED=false
IB_ENVIRONMENT=paper         # paper | live
# IB_ORB_ACCOUNT_ID=
# IB_0DTE_ACCOUNT_ID=
# IB_DEFAULT_ACCOUNT_ID=
```

**Current behavior:** Config recognized · data plane `NotImplementedError` · `StubBrokerAdapter` · Live probe `broker_not_etrade`.

### **Charles Schwab Configuration** (P2 — not Live-ready)

```bash
BROKER=schwab   # aliases: charles_schwab, charlesschwab
SCHWAB_ENABLED=false
SCHWAB_ENVIRONMENT=demo   # demo | live
# SCHWAB_ORB_ACCOUNT_ID=
# SCHWAB_0DTE_ACCOUNT_ID=
# SCHWAB_DEFAULT_ACCOUNT_ID=
```

**Current behavior:** Router token recognized; data provider, account mapping, OAuth2 credential flow, and `SchwabBrokerAdapter` are pending. Treat as fail-closed until broker certification passes.

### **TastyTrade Configuration** (P2 — not Live-ready)

```bash
BROKER=tastytrade
TASTYTRADE_ENABLED=false
TASTYTRADE_ENVIRONMENT=demo   # demo | live
# TASTYTRADE_ORB_ACCOUNT_ID=
# TASTYTRADE_0DTE_ACCOUNT_ID=
# TASTYTRADE_DEFAULT_ACCOUNT_ID=
```

**Current behavior:** Same fail-closed pattern as IB.

### **Legacy broker placeholders** (removed Jun 18)

Robinhood, Alpaca, and Tradier env stubs were **removed** from `Data.env` during Phase 2 trim. Live product roadmap: **E*TRADE · Interactive Brokers · TastyTrade · Schwab** — see [Brokers.md](Brokers.md).

---

## 📱 **Alert Configuration**

**Location**: `configs/Alerts.env` — **Telegram only (7 keys)** after Phase 2 trim (Jun 18). Email, webhook, and Slack keys removed.

### **Telegram Configuration**

```bash
ALERTS_ENABLED=true
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_ENABLED=true
TELEGRAM_MAX_MESSAGES_PER_MINUTE=20
ALERT_COOLDOWN_SECONDS=60
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here  # See PrivateSecrets.md
TELEGRAM_CHAT_ID=your_telegram_chat_id_here      # See PrivateSecrets.md
```

**Alert levels** (`ALERT_LEVEL_*`) are in **`configs/Shared.env`**, not `Alerts.env`. Deprecated alias: `TELEGRAM_ALERT_COOLDOWN_SECONDS` → canonical **`ALERT_COOLDOWN_SECONDS`** (see `config_manifest.yaml`).

**Setup**: See [Alerts.md](Alerts.md) for complete Telegram setup guide.

**Rev Jun 17 alert copy** (documented in Alerts.md): Good Morning multi-broker block · DEMO Trade Signal Collection (`Standard Orders` + `0DTE Options Ready`) · ORB1 Breakout execution title **`ORB1 Breakout Orders Filled`**.

### **Retired alert keys** *(removed from env Jun 18 — code defaults or Shared.env only)*

Per-type toggles (`ALERT_ON_*`), PnL thresholds, and timing keys that lived in legacy `Alerts.env` are no longer in the canonical seven-file bundle. Use `ALERT_LEVEL_*` in `Shared.env` and alert manager behavior in code.

---

## 🚀 **Deployment Configuration**

**Location**: `configs/Data.env` and `configs/Shared.env` (GCP/container keys and deployment-oriented toggles)

### **Deployment Mode**

**Trading Modes** (`MODE` — canonical; `modules/runtime_mode.py`):
- **Demo Mode** (`MODE=demo`): **Live broker market data** + **simulated ledger** (unified mock options book). No broker order submits.
- **Live Mode** (`MODE=live`): **Live broker market data** + **real broker account** orders on **`ETRADE_0DTE_ACCOUNT_ID`**.

**Important**: Both modes use the **production E*TRADE API** for quotes and chains. `MODE` controls ledger vs broker execution — not sandbox vs production API.

```bash
# Runtime mode (canonical — Shared.env §8)
MODE=demo
BROKER=etrade
DEPLOYMENT_MODE=demo  # legacy label
AUTOMATION_ENABLED=true
```

### **Strategy Enablement**

```bash
# Strategy Enablement (RESEARCH app — three paths)
ENABLE_ORB_STRATEGY=true   # ORB SO ETF (ORBSO.env)
ENABLE_0DTE_STRATEGY=true
ENABLE_TRENDLINE_STRATEGY=true
```

### **Google Cloud Configuration**

```bash
# GCP Project Settings (see CloudSecrets.md for actual values)
GCP_PROJECT_ID=your-project-id
GCP_REGION=us-central1
GCP_ZONE=us-central1-a
GCP_SERVICE_NAME=your-service-name  # See CloudSecrets.md for actual value
GCS_BUCKET_NAME=your-project-id-data
GCS_PERSISTENCE_ENABLED=true
```

Runtime feature flags (`ENABLE_*`, automation shell) live in **`configs/Shared.env`** (DEMO) or **`configs/System.env`** (LIVE). Container CPU/memory/instance counts are **deploy-script** settings, not `Data.env` keys (Phase 2 trim, Jun 18).

### **Feature Flags**

```bash
# Feature Flags
ENABLE_ALERT_MANAGER=true
ENABLE_PRIME_SYSTEM=true
ENABLE_RISK_MANAGEMENT=true
ENABLE_PERFORMANCE_TRACKING=true
ENABLE_AUTO_TRADING=true

# Deployment Safety
DEPLOYMENT_SAFETY_CHECKS=true
PRE_DEPLOYMENT_TESTS=true
POST_DEPLOYMENT_VALIDATION=true
ROLLBACK_ENABLED=true
EMERGENCY_STOP_ENABLED=true
```

---

## 🔐 **Secrets Management**

**Location**: `secretsprivate/` (local) or Google Secret Manager (production)

### **Two-Tier Secrets Management System**

**Production/Deployment**: Google Secret Manager (GCP)  
**Local Development**: `secretsprivate/` folder (gitignored)

### **⚠️ CRITICAL: Secret Manager Cost Optimization**

**Secret Manager charges $0.06 per version per month.** To prevent excessive costs:

1. **Automatic Cleanup**: ✅ **DEPLOYED** (February 9, 2026)
   - **Firebase OAuth App**: `oauth_backend.py` automatically deletes old secret versions when tokens are renewed via web app
   - **Cloud Run Services**: `secret_manager_oauth.py` also includes cleanup (for direct token storage)
   - Both systems keep only the latest version (1 per secret)

2. **Best Practice**: Each secret should have only **1 version** (the latest). Monitor version counts monthly:
   ```bash
   for SECRET in etrade-oauth-prod; do
     COUNT=$(gcloud secrets versions list $SECRET --format='value(name)' | wc -l)
     echo "$SECRET: $COUNT versions (should be 1)"
   done
   ```

3. **Manual Cleanup**: If old versions accumulate, run:
   ```bash
   cd "0. Strategies and Automations/1. The Easy ORB Strategy"
   ./scripts/cleanup_secrets_optimized.sh
   ```

**Current Cost**: ~$1.20/month (20 billable versions across all projects × $0.06)  
**Expected Cost**: ~$0.78/month (13 secrets in the trading project × 1 version × $0.06)  
**Without Cleanup**: Could exceed $200/month if thousands of versions accumulate

**Implementation**: 
- **Firebase OAuth**: The `write_secret()` function in `ETradeOAuth/login/oauth_backend.py` automatically calls `_cleanup_old_secret_versions()` after creating a new secret version
- **Cloud Run**: The `store_tokens()` method in `ETradeOAuth/login/secret_manager_oauth.py` automatically calls `_cleanup_old_versions()` after creating a new secret version
- Both ensure only the latest version is kept

**All sensitive credentials are stored securely and never committed to Git.**

### **Local Development Setup**

1. **Create secrets files**:
   ```bash
   cp secretsprivate/etrade.env.template secretsprivate/etrade.env
   cp secretsprivate/telegram.env.template secretsprivate/telegram.env
   ```

2. **Fill in credentials**:
   - Edit `secretsprivate/etrade.env` with your E*TRADE keys/secrets
   - Edit `secretsprivate/telegram.env` with your Telegram bot token/chat ID

3. **Automatic loading**: `modules/config_loader.py` automatically loads from `secretsprivate/` when `ENVIRONMENT=development`

### **Production Deployment**

**Location**: Google Secret Manager  
**Access**: Via service account with Secret Manager permissions

**Secret names** (values live in Secret Manager / [PrivateSecrets.md](PrivateSecrets.md) / `secretsprivate/`):
- `etrade-prod-consumer-key` (Production consumer key)
- `etrade-prod-consumer-secret` (Production consumer secret)
- `etrade-oauth-prod` (Production OAuth tokens - JSON format)
- `telegram-bot-token` (Telegram bot token)
- `telegram-chat-id` (Telegram chat ID)

**Note**: Both Demo and Live modes use the same production E*TRADE API credentials. The difference is which account is used (demo account vs live account). OAuth tokens are shared between demo and live modes since they use the same API.

**Automatic loading**: `modules/config_loader.py` automatically loads from Secret Manager when `ENVIRONMENT=production`

### **Security Best Practices**

- ✅ Store production secrets in Google Secret Manager
- ✅ Use `secretsprivate/` for local development only
- ✅ Keep template files (`.template`) in Git
- ✅ Never commit `secretsprivate/` folder to Git
- ✅ Never hardcode secrets in config files
- ✅ Rotate credentials regularly

---

## 🌍 **Environment Variables**

### **Runtime Environment Variables**

These can override configuration file settings:

```bash
# Trading Mode (canonical)
ENVIRONMENT=production  # development or production
STRATEGY_MODE=standard  # standard, advanced, quantum
MODE=demo             # demo | live
BROKER=etrade
SYSTEM_MODE=full_trading  # signal_only, scanner_only, full_trading, alert_only
CLOUD_MODE=true

# Strategy Enablement
ENABLE_ORB_STRATEGY=true
ENABLE_0DTE_STRATEGY=true

# Broker Configuration (BROKER canonical; BROKER_TYPE legacy fallback)
BROKER_TYPE=etrade
BROKER_DATA_ONLY=true

# Logging
LOG_LEVEL=INFO
```

### **Cloud Run Environment Variables**

Set via `gcloud run deploy`:

```bash
# Replace YOUR_SERVICE_NAME with your actual service name (see CloudSecrets.md)
gcloud run deploy YOUR_SERVICE_NAME \
    --set-env-vars="ENVIRONMENT=production,STRATEGY_MODE=standard,MODE=demo,BROKER=etrade,SYSTEM_MODE=full_trading,CLOUD_MODE=true,ENABLE_0DTE_STRATEGY=true,LOG_LEVEL=INFO"
```

---

## 📝 **Configuration Best Practices**

### **1. Use Configuration Files**

- ✅ Store all settings in `configs/*.env` files
- ✅ For secrets, use `secretsprivate/*.env.template` as starting points
- ✅ Never hardcode values in code

### **2. Secrets Management**

- ✅ Store secrets in Secret Manager (production) or `secretsprivate/` (local)
- ✅ Never commit secrets to Git
- ✅ Use template files for documentation

### **3. Version Control**

- ✅ Commit only safe template files (for example `secretsprivate/*.env.template`)
- ✅ Gitignore actual config files with secrets
- ✅ Document configuration changes

### **4. Testing**

- ✅ Test configuration changes in demo mode first
- ✅ Validate configuration before deploying
- ✅ Use deployment safety checks

### **5. Documentation**

- ✅ Document all configuration changes
- ✅ Keep configuration files organized
- ✅ Use clear naming conventions

---

## 🔄 **Configuration Validation**

### **Capital Allocation Validation**

**Rules**:
- `SO_CAPITAL_PCT + ORR_CAPITAL_PCT + CASH_RESERVE_PCT = 100%`
- `SO_CAPITAL_PCT + ORR_CAPITAL_PCT = TOTAL_CAPITAL_ALLOCATION_PCT`

**Validation**:
```bash
# System automatically validates on startup
# Errors logged if validation fails
```

### **Account Selection Validation**

**Rules**:
- Strategy-specific account IDs must be valid E*TRADE account IDs
- Default account ID must be valid if strategy-specific not set
- System validates account IDs on startup

### **Risk Limits Validation**

**Rules**:
- `MAX_POSITION_SIZE_PCT <= MAX_POSITION_RISK_PCT`
- `BASE_POSITION_SIZE_PCT <= MAX_POSITION_SIZE_PCT`
- Daily/weekly/monthly limits must be consistent

### **Runtime Config Visibility Logs** (Apr 30 cleanup pass)

`modules/config_loader.py` now emits passive (non-blocking) config hygiene logs:

- `CONFIG_MISSING_KEY | key=<name> | fallback_used=true`
  - emitted when `get_config_value(...)` falls back to default because a key is absent from merged env
  - also emitted via passive `os.getenv` hook for config-like keys not present in runtime `os.environ`
- `CONFIG_DUPLICATE_KEY | key=<name> | sources=[...]`
  - emitted when the same key appears in multiple env files during merge

These are visibility-only logs; they do not change strategy behavior or startup flow.

---

## 🛠️ **Troubleshooting**

### **Configuration Not Loading**

**Issue**: Settings not taking effect
- ✅ Check configuration file path
- ✅ Verify environment variable overrides
- ✅ Check Secret Manager permissions (production)
- ✅ Review logs for configuration errors

### **Capital Allocation Errors**

**Issue**: Capital allocation doesn't sum to 100%
- ✅ Verify `SO_CAPITAL_PCT + ORR_CAPITAL_PCT + CASH_RESERVE_PCT = 100%`
- ✅ Check `TOTAL_CAPITAL_ALLOCATION_PCT` matches strategy allocation
- ✅ Review `configs/ORBSO.env`, `configs/ORB0DTE.env`, `configs/Trendline0DTE.env`, `configs/Shared.env`, and `configs/Risk.env`

### **Account Selection Issues**

**Issue**: Wrong account being used
- ✅ Check merged broker config (`configs/Data.env`) and secrets layer for account IDs
- ✅ Verify account IDs are correct (see PrivateSecrets.md)
- ✅ Check account selection priority logic

### **Secrets Not Loading**

**Issue**: Secrets not found in production
- ✅ Verify Secret Manager secrets exist
- ✅ Check service account permissions
- ✅ Verify secret names match configuration
- ✅ Review Secret Manager access logs

---

## 📚 **Additional Resources**

- **Alert System**: See [Alerts.md](Alerts.md) for alert configuration
- **OAuth System**: See [OAuth.md](OAuth.md) for OAuth token management
- **Cloud Deployment**: See [Cloud.md](Cloud.md) for cloud configuration
- **Strategy Details**: See [ORBSO.md](ORBSO.md) (SO ETF) · [0DTEORB1Breakout.md](0DTEORB1Breakout.md) (ORB1 Breakout) · [0DTETrendline.md](0DTETrendline.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md)
- **Sensitive Information**: See [PrivateSecrets.md](PrivateSecrets.md) for deployment-specific values

---

---

## Appendix A — Configuration History

Deployment chronology retained for audit. For **current** baseline only, see [Production Defaults Snapshot](#production-defaults-snapshot) and [Current build status](#current-build-status).

### August 16, 2026 — local (not deployed)

- **TL first-break clock:** `TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC=10800` — ARMED **TL1–TL4** expire **3h** after `built_at` (was 7200 / 2h). **TL0** still hard-expires at **07:10** (not the 3h clock).
- **TL one attempt:** hard QG / post-exec reject **or fill** expires that slot/side envelope (`EXPIRED` / `EXECUTED`). A1/A2 persist. Opposite envelope and sibling slots stay live. Soft window / capital wait still re-arm. Hydrate restores EXPIRED/EXECUTED (`apply_persisted_envelope_state`).
- **ORB1 one-shot:** 06:45–07:10. Reject = 1h same-side (other H/L open until cutoff). Fill = one trade; no inside-range re-entry; no second ORB1 after exit.
- **ORB2 re-entry:** reject **or fill** = 1h same-side cooldown **and** price must print back inside ORB2 before that H/L can fire again. Fill does **not** day-lock the symbol.
- **Docs:** [0DTETrendline.md](0DTETrendline.md) · [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### August 13, 2026 — local (not deployed)

- **TL activate:** sole key `TRENDLINE_MIN_BREAK_DISTANCE_PCT=0.0008` (0.08% detect=emit). `TRENDLINE_MIN_CONTINUATION_PCT` / `TRENDLINE_EMIT_MIN_BREAK_DISTANCE_PCT` **deleted**.
- **TL activate:** detect=emit **0.08%** (`TRENDLINE_MIN_BREAK_DISTANCE_PCT=0.0008`); no separate continuation knife; Grade A sustain **OFF**; dual-lane PUT block **OFF**; expansion CALL mult **1.0**
- **Env rename:** operator knobs `TRENDLINE_SIMPLE_*` → `TRENDLINE_*` (loader **does not** accept `SIMPLE` aliases; log tokens may still say `TRENDLINE_SIMPLE_*`)
- **Trade-activate age (then):** `TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC=7200` — ARMED **TL1–TL3** expire **2h** after `built_at` (Aug 16 raised this to **10800** / **3h**)
- **TL2/TL3 Yahoo:** force Yahoo 1m before inject; refuse synth geometry; bypass 60s hydrate gate for pending inject symbols
- **TL0:** after ORB1 ≥**06:45** → Yahoo 1m → dual-side ORB1 box · keys `SYM:bull|bear:trendline0` · discovery **≤07:10** · `modules/trendline_tl0_build.py`
- **Dual-side TL1:** arm bull PUT + bear CALL when intact + relevant — `run_unified_geometry_contest` / `_collect_dual_side_watch_candidates`
- **Mission contest removed:** deleted `TRENDLINE_GEOMETRY_MISSION_*`, `TRENDLINE_BLOCK_MIXED_GEOMETRY_COMPRESSION`, structure-first / misalign helpers — not “off”, gone
- **Reject lifecycle (then):** hard quality / post-exec → **1h `{SYM}:{TLn}:{side}` cooldown + re-arm WAIT_BREAK** (Aug 16: expire that envelope instead)
- **Scheduler:** single-flight TL1 · arm-once @ 07:10 · Yahoo batch sleep **0.5s** · compact reject alerts

### June 10, 2026 — local (not deployed)

- **Mission structure-first geometry (P0–P4):** *(historical — removed Aug 11)* formerly `geometry_structure_first_*` / legacy mission penalty in `trendline_models.py`
- **Jun 10 desk (`00369-7cf`):** **5** Trendline geometry mismatches (**SPY/QQQ/RGTI/NBIS/QBTS** legacy **CALL** → desk **PUT**); local replay **10/10** — `scripts/replay_jun10_geometry_replay.py`

### June 9, 2026 — local (not deployed)

- **Cutoff-control line:** `TRENDLINE_CUTOFF_CONTROL_LINE_ENABLED` · `TRENDLINE_CUTOFF_CONTROL_NEAR_CUTOFF_MINUTES` — inside-ORB ramp anchor (ORB extreme → 7:30 control touch)
- **Jun 9 desk (`00368-g2k`):** Trendline **0** executes · **MSFT/META** PUT geometry correct but legacy anchor drift (`entry_distance_extended`) · **PANW/NFLX** Red Day gate drops — [Jun 9 review](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Trendline%20Improvements/TrendlineImprovementGuide.md#jun09-improvement-review-summary)

### June 8, 2026 — deployed (`00367-gsd` / BUILD `00383`)

- **Trendline:** `TRENDLINE_RED_DAY_GATE_*` · **10/10** slot cap · equity **deployed (%)** on alerts · PP Red Day Pattern 3 alignment
- **Jun 8 desk (`00366-fwc`):** Trendline **7** entries · **2** closed winners (**RUT**, **QQQ**) — [session](doc_elements/Sessions/2026/Jun%208%20Session/SESSION_SUMMARY_JUN08_2026.md)

### June 5, 2026 — local (not deployed)

- **F12** `0DTE_CONVEX_SHORT_BREAKDOWN_*` vol bypass + **F14** VWAP relief (mirror LONG continuation)
- **F13** `0DTE_SINGLE_LEG_OPENING_*` opening premium relax (symmetric CALL/PUT)
- **`TRENDLINE_DEFER_BUILD_UNTIL_ORB_BATCH`** (7:30 scheduling with ORB batch)
- **Live Mode architecture:** canonical `MODE`/`BROKER`, `BrokerAdapter`, production soak gates — [FINAL LiveModeReadiness.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/LiveModeReadiness.md)
- P0 code: **`0DTE_HARD_GATE_SYMBOL_SUMMARY`**, **`SO_EXEC_REJECT`**, demo balance hydration, **`ORB_CRITICAL_WINDOW`**, **`STARTUP_CONFIG_AUDIT`**
- **Jun 5 desk (`00364-2n5`):** ORB1 Breakout **1** fill (**MSTR** PUT **+503%**) · **9/9** PUT direction · **1/9** filled; Trendline **4** fills — [session](doc_elements/Sessions/2026/Jun%205%20Session/SESSION_SUMMARY_JUN05_2026.md) · [SOversus0DTEFulfillmentGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/SOversus0DTEFulfillmentGuide.md)

### June 4, 2026 — deployed (00381 / `00364-2n5`)

- ORB **`0DTE_ORB_LOTTO_FIRST=true`** (single-leg primary) + spread primary/fallback off; monitor health-ratio + ghost purge
- **`0DTE_DELTA_VIABILITY_*`** in `ORB0DTE.env`; opening **`no_progress`** defer keys in `Shared.env`
- Trendline: **mission geometry contest** (`TRENDLINE_GEOMETRY_MISSION_RESOLUTION_*`); **`TRENDLINE_DEFER_POST_BUILD_WATCH_UNTIL_ORB_BATCH`**
- **Jun 4 desk (`00363`):** ORB1 Breakout **4** fills ~**+$1,671**; Trendline **0** fills pre-deploy — [session](doc_elements/Sessions/2026/Jun%204%20Session/SESSION_SUMMARY_JUN04_2026.md)

### June 3, 2026 — deployed (00360)

- Trendline **`hold_mode`**; neutral volume gate; **ExecutionIntent** ledger serialization
- profit_optimizer UTC timezone fix; ORB1 Breakout ghost DEMO purge; alert UX improvements
- Shared.env: `ORB_RTH_INTRADAY_DISABLE`, `0DTE_CONVEX_ORB_MOMENTUM_VOL_FLOOR`, `PROFIT_OPTIMIZER_STALE_QUOTE_SECONDS`, `ALERT_LEVEL_*`
- **Jun 3 desk (`00359`):** ORB1 Breakout **4** fills ~**+$4,643**; Trendline **0** fills — [session](doc_elements/Sessions/2026/Jun%203%20Session/SESSION_SUMMARY_JUN03_2026.md)

### June 2, 2026 — deployed (00358)

- ORB1 Breakout monitor `quote_grade` fix; directional single-leg delta **0.42**; opening OI **100** / spread **10%**
- **`0DTE_HARD_GATE_EXEC_SPREAD_RELAX_MULT=1.25`**
- **Trendline Geometry Phase 1** — six `TRENDLINE_GEOMETRY_*` keys — [GEOMETRY_ELITE_SETUP_AUDIT.md](Improvement%20Guide/Trendline%20Improvements/GEOMETRY_ELITE_SETUP_AUDIT.md)
- [session](doc_elements/Sessions/2026/Jun%202%20Session/SESSION_SUMMARY_JUN02_2026.md)

### May 29, 2026 — deployed (00371) + local pending

- ORB monitor reliability; Trendline simple break mode; Selector v3; PO three-artifact capture; Yahoo **`RUT`/`SILVER`** fixes
- **Local pending 00372+:** Trendline sacred 10% buffer, 1/N tradable sizing, deferred-emit re-arm
- [May 29 session](doc_elements/Sessions/2026/May29%20Session/SESSION_SUMMARY_MAY29_2026.md)

### May 28, 2026 — deployed (`00352-wdj`)

- ORB opening-mode selector, Tier **E** convex preservation, liquidity classes, exitability score
- **`ORB_0DTE_BEST_EXECUTABLE_MISSED_CONTRACT`** — [May 28 session](doc_elements/Sessions/2026/May28%20Session/SESSION_SUMMARY_MAY28_2026.md)

### May 27, 2026 — deployed (`00351`)

- Trendline profit-capture calibration (trail **28%**, micro **+3.5%→+1%**, profit lock **+8%→+4%**) — **historical**; Jul 1, 2026 policy disables trail / profit-lock exits
- **00368** Hard Gate align · **00369** Portfolio Protection · **00375** split-tape Protection Day (local)
- [May 27 session](doc_elements/Sessions/2026/May27%20Session/SESSION_SUMMARY_MAY27_2026.md)

### Improvement / tuning docs (research)

[ORBSOImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/ORBSOImprovementGuide.md) · [ORB0DTEImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/ORB0DTEImprovementGuide.md) · [SOversus0DTEFulfillmentGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/SOversus0DTEFulfillmentGuide.md) · [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md) · [TrendlineImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Trendline%20Improvements/TrendlineImprovementGuide.md) · [ORB_Pipeline_Ranking_and_Execution.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/ORB_Pipeline_Ranking_and_Execution.md)

---

## 📝 **Revision History**

### **October 27, 2026 — PC V2 STEP 29 ATR loss cut + STEP 30 VWAP cross exit (local Research, not deployed)**

- **Keys:** 13 new `OPTION_PATH_V2_ATR_LOSS_CUT_*` / `OPTION_PATH_V2_VWAP_CROSS_*` in `configs/Shared.env` §2b after `OPTION_PATH_V2_SG_UL_HOLD_MAX_GIVEBACK`; both kill switches dataclass `false`, Shared.env `true`. Age gate is the shared 120s checkpoint (`OPTION_PATH_V2_MIN_HOLD_SECONDS`, STEP 16), used by STEP 29–30 only.
- **Files:** `modules/path_v2_exit.py` `569a516c…f5a8`, `configs/Shared.env` `ea15c978…b3bf` (10/29: now `37bd9d1f…8a74`; `0DTE_DISCOVERY_MAX_OPEN_QUOTE_LATENCY_MS` 900→4000, `0DTE_PATH_REFRESH_SKIP_CYCLE_MS` 1500→8000, plus `0DTE_MONITOR_WIDE_OPEN_COUNT=20` / `0DTE_MONITOR_WIDE_INTERVAL_SEC=3`; exit replay unaffected), catalog `5c518252…507c`, `execution_routing.py` `b48a88a2…6f83`, `prime_alert_manager.py` `4c18de66…8e98`, `prime_options_stealth_trailing_tp.py` `ec5f0c6f…5e68`; backups `*.bak1027_atrvwap`.
- **Replay 8/19–10/26:** both on Total +$579 / TP +$37; both off = 0 differences. See [0DTEProfitCapture.md](0DTEProfitCapture.md) for the full table and deploy checklist.

### **August 26, 2026 — PX3 MFE confirmation runner**

- **Canonical keys:** `PX3_MFE_CONFIRM_*` in `Shared.env` §2b. This is a PX3-family overlay after PX2 would exit `mfe_take_profit`; it does not use side-only rules and does not replace PX2 `premium_trail`, BE, 12:30 `mfe_trail`, or flatten.
- **Gate:** `PX3_MFE_CONFIRM_ENABLED=true` · current PnL ≥ **+20%** · premium HWM ≥ **+30%** · normal lane underlying HWM ≥ **+0.25%** only in the **20–30m** entry-age band; thin lane uses **+0.05% to <+0.25%** underlying HWM and excludes the **+20% to +35%** original-bank chop zone; strong lane uses **+1.00%** underlying HWM and original bank ≥ **+29%** within the first **30m**; late-strong can admit 30–60m NBIS-class banks with ≥ **+0.75%** underlying HWM. Confirmation must improve at least **+15pp** to become a runner.
- **Runner:** price trail **0.40%** · normal/strong/late-strong static premium floor **max(+30%, original bank)** with premium ratchet **HWM−50pp** after handoff; thin-recovery runner uses original-bank static floor with **HWM−50pp**. If confirmation fails, thin recovery banks at original−**10pp** and strong recovery banks at original−**20pp**, never below flat.
- **Replay:** Aug 17–Sep 4 closed-book replay: expected winners **20877.2pp** vs actual **20105.8pp** (**+771.4pp**), all closed-trade lift **+2491.7pp**, modeled winner-to-red flips **0**. Sep 8 CRWV/AMD live replay: CRWV **+42.2% → +98.3%**, AMD live baseline **+69.0% → +138.1%**, both via `px3_price_trail`. Side-only and broad keep-open variants are rejected.

### **August 26, 2026 — PX3 exceptional winner handoffs**

- **Canonical keys:** `PX3_EXCEPTIONAL_*`, `PX3_THIN_LEAD_EXCEPTIONAL_*`, and `PX3_HIGH_HWM_EXCEPTIONAL_*` in `Shared.env` §2b. These are PX3-only overlays after PX2 would exit `premium_trail`; they do not replace PX2 stall-bank, BE, 12:30 `mfe_trail`, or flatten.
- **Safe exceptional:** `PX3_EXCEPTIONAL_ENABLED=true` · current PnL ≥ **+100%** · premium HWM ≥ **+60%** · underlying HWM ≥ **+0.50%** · price trail **0.50%** · premium floor **+75%**.
- **Thin-lead exceptional:** `PX3_THIN_LEAD_EXCEPTIONAL_ENABLED=true` · current PnL ≥ **+100%** · premium HWM ≥ **+60%** · underlying HWM ≥ **+0.10%** · price trail **0.40%** · premium floor **+25%**.
- **High-HWM exceptional:** `PX3_HIGH_HWM_EXCEPTIONAL_ENABLED=true` · current PnL ≥ **+100%** · premium HWM ≥ **+125%** · underlying HWM ≥ **+0.40%** · price trail **0.40%** · premium floor **+50%**. Evaluates before the broader exceptional gates.
- **Replay:** Aug 17–25 trusted shadow strict gate: 4 historical rows improved / **0 worse**; high-HWM refinement adds **+93.0pp** on AAPL / IBIT over the prior PX3 exceptional baseline.

### **August 21, 2026 — PX2 / PX2.5 / PX3 Profit Capture codified**

- **Canonical keys:** `PX2_*`, `PX25_*`, and `PX3_TREND_PRICE_HWM_PCT`. Legacy `SINGLE_PX2_*` / `PX3_TREND_UND_HWM_PCT` remain code aliases only, not active config names.
- **PX2.5:** `modules/px25_exit.py`, existing-position exit extension only. No re-entry / no new trades. Enabled with `PX25_ENABLED=true`.
- **Code:** `modules/px2_exit.py` · `modules/px25_exit.py` · `modules/px3_exit.py` · engine intercept in `modules/prime_options_stealth_trailing_tp.py`.
- **Deploy:** local only until explicit `deploy now`.

### **August 21, 2026 — PX3 Protected Runner (local ON)**

- **Does not replace PX2.** Overlay after PX2 `premium_trail` only. Guards that say keep PX2 return the original `premium_trail` fill. Decision uses **open** premium HWM and realized pnl at the bank tick — not session-peak leftover.
- **Keys (`Shared.env` §2b):** `PX3_ENABLED=true` · `PX3_TREND_MFE_PCT=1.00` · `PX3_TREND_PRICE_HWM_PCT=0.004` · `PX3_PRICE_TRAIL_PCT=0.004` · `PX3_TREND_PREM_FLOOR_PCT=0.50` · `PX3_MOON_MFE_PCT=2.00` · `PX3_MOON_PATIENCE_SECONDS=300` · `PX3_MOON_PREM_FLOOR_PCT=0.75` · `PX3_ULTRA_CLEAN_PNL_PCT=7.50` · `PX3_ULTRA_CLEAN_CAPTURE_RATIO=0.85` · `PX3_CLEAN_TREND_PNL_PCT=2.50` · `PX3_CLEAN_TREND_CAPTURE_RATIO=0.90`.
- **Code:** `modules/px3_exit.py` · engine intercept in `prime_options_stealth_trailing_tp.py`.
- **Replay (current QG · 17–21 same-tape):** PX2 net **+$22,297** → PX3 **+$36,713** · win gross **+$24,644 → +$39,060** · **51/51** · 0 flips · 0 worse. Lifts: IBIT **+$13,430** · AAPL **+$871** · AMAT **+$115**. AMZN / SPY / MSFT TL1 / MSFT TL2+ stay PX2.
- **Docs:** [0DTEProfitCapture.md §3.6](0DTEProfitCapture.md#36-px3-protected-runner--modulespx3_exitpy) · this file §2b.
- **Deploy:** local only until explicit `deploy now`.

### **August 27, 2026 — price_stop −0.80% (local)**

- **`OPTION_SINGLE_STOP_PCT=0.008`** in `Shared.env` §2b (was 0.007). Reason **`price_stop`**. One Shared width for ORB1 / Trendline / ORB2 BO. Only before ARM.
- Aug 17–27 stop sweep: −0.80% saves META Aug 27 and 21 later-positive stopouts for **+784.9pp** opportunity; wider than −0.80% adds little until −1.0% while increasing loss drag.
- **Docs:** [0DTEProfitCapture.md §3.1 / §4.2](0DTEProfitCapture.md) · [0DTERisk.md](0DTERisk.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 26, 2026 — price_stop −0.7% (local)**

- **`OPTION_SINGLE_STOP_PCT=0.007`** in `Shared.env` §2b (was 0.005). Reason **`price_stop`**. One Shared width for ORB1 / Trendline / ORB2 BO. Only before ARM.
- MN26 17–26 PC-PX2 walk: −0.5% **+$25,516** · −0.7% **+$25,527** (GDX 19 stall-bank +$195) · −0.8% **+$25,496**.
- **Docs:** [0DTEProfitCapture.md §3.1 / §4.2](0DTEProfitCapture.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 21, 2026 — price_stop −0.5% (local)**

- **`OPTION_SINGLE_STOP_PCT=0.005`** in `Shared.env` §2b (was 0.018). Reason **`price_stop`**. One Shared width for ORB1 / Trendline / ORB2 BO. Only before ARM.
- **Docs:** [0DTEProfitCapture.md §3.1 / §4.2](0DTEProfitCapture.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **September 1, 2026 — Liquidity Guard ADV 2.0% / generic floor off (local)**

- **`LIVE_UNDERLYING_ADV_PARTICIPATION_PCT=0.02`** (was 0.005). Option notional ≤ ADV$ × 2.0%.
- **`LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD=0`** (was 200000000). No generic $200M substitute; missing ADV fail-closes after broker + named floors.
- Unchanged: vol/OI qty caps **off** · ask-depth **200%** · exit-book **off** · min bid/ask **1** · lookback **90** · yfinance **off** · Demo enforce **on**.
- **Docs:** [LiquidityGuard.md](LiquidityGuard.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 20, 2026 — Liquidity Guard v1.7 (local)**

- **Size governors:** `LIVE_MAX_CONTRACTS_PER_TRADE=0` · `LIVE_OPTION_VOLUME_PARTICIPATION_PCT=0` (off) · `LIVE_OPTION_OI_PARTICIPATION_PCT=0` (off) · `LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT=0` (off) · `LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT=2.0` · ADV **0.5%** · `LIVE_MIN_ASK_SIZE=1` · `LIVE_MIN_BID_SIZE=1`.
- **Policy:** volume, OI, and bid are **not** qty caps. Qty ≤ **200% of displayed ask** (temporary L1 ceiling; no L2 / no slicer). Bid/ask **5** are MQ floors only.
- **Docs:** [LiquidityGuard.md](LiquidityGuard.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 17, 2026 — Liquidity Guard v1.6 (local)**

- **Size governors:** `LIVE_MAX_CONTRACTS_PER_TRADE=0` · `LIVE_OPTION_VOLUME_PARTICIPATION_PCT=0` (off) · `LIVE_OPTION_OI_PARTICIPATION_PCT=0` (off) · `LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT=0` (off) · `LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT=1.0` · ADV **0.5%** · `LIVE_MIN_ASK_SIZE=1` · `LIVE_MIN_BID_SIZE=1`.
- **Policy:** volume, OI, and bid are **not** qty caps. Qty ≤ **100% of ask**. Bid/ask **5** are MQ floors only.
- **Docs:** [LiquidityGuard.md](LiquidityGuard.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 17, 2026 — Liquidity Guard v1.5 (local)**

- **Size governors:** `LIVE_OPTION_VOLUME_PARTICIPATION_PCT=0.05` · `LIVE_OPTION_VOLUME_MIN_CONTRACTS=5` · `LIVE_OPTION_OI_PARTICIPATION_PCT=0` · `LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT=1.0` · `LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT=0` · ADV **0.5%** unchanged · min bid/ask **5**.
- **Policy:** stop OI as a qty cap; stop 25% of displayed book as the whole-trade cap. Qty ≤ configured ask-depth participation. Bid is MQ evidence (`LIVE_MIN_BID_SIZE=1`) only.
- **Docs:** [LiquidityGuard.md](LiquidityGuard.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 17, 2026 — PC-PX2 Pareto leftover ≤ $10 (local)**

- **Expected next session:** stall-bank (und+prem 8 ticks; leftover ≤ **$10** = QQQ / INTC ORB1 14) · 20pp giveback · lock@200% same 20pp · post-ARM BE books **entry / $0** · 0.50% und trail only after und HWM ≥0.5% AND prem ≥60% · required 12:30 `mfe_trail` −10pp · flatten 12:55.
- **Keys (`Shared.env` §2b):** `PX2_ENABLED=true` · `PX2_TRAIL_PCT=0.20` · `PX2_LOCK_MOON_PCT=2.00` · `PX2_STALL_TICKS=8` · `PX2_BE_FLOOR=true` · `PX2_PRICE_TRAIL_NEED_PCT=0.005` · `PX2_PRICE_TRAIL_PREM_PCT=0.60` · `0DTE_MFE_TAKE_PROFIT_IF_PRICE_THIN=false` · `OPTION_SINGLE_MFE_TRAIL_ENABLED=false`.
- **Code:** `modules/px2_exit.py` · `PX2_DUMPER_LEFT_USD=10` · `px2_is_stall_bank_dumper` (scoring/docs; live latch is dual-stall + 20pp).
- **Book (40 wins Aug 13+14+17):** live thin TP **+$4,809 (21%)** · overlay **+$12,200 (54%)** · **expected / Pareto +$12,545 (56%)**. Take INTC leftover $9 and NFLX on 2s tape. Do not skip 12:30 (GDX). Do not drop und trail. Last-green rejected.
- **Rollback:** `PX2_ENABLED=false` · `0DTE_MFE_TAKE_PROFIT_IF_PRICE_THIN=true`.
- **Docs:** [0DTEProfitCapture.md §3.5](0DTEProfitCapture.md#35-pc-px2-after-arm--modulespx2_exitpy) · this file §2b.
- **Deploy:** local only until explicit `deploy now`.

### **August 17, 2026 — Moon hold removed (local)**

- **Removed:** `OPTION_MOON_HOLD_*` · `modules/moon_hold_defer.py` · H2 defer intercept. Catalog `moon_hold` historical-only.
- **Runner moons:** +60% MFE → **0.50%** `price_trail` (`SINGLE_RUNNER_WIDE_TRAIL_*`) unchanged.
- **Telegram:** `Moon: ⭐️` on exit when realized premium PnL **> +200%**.
- **Deploy:** local only until explicit `deploy now`.

### **August 14, 2026 — ORB/TL price_stop −1.8% (local)**

- **`OPTION_SINGLE_STOP_PCT=0.018`** in `Shared.env` §2b (was 0.006). CALL stop = entry_und × (1 − 0.018) · PUT = (1 + 0.018) · only before ARM.
- **BO unchanged:** `BREAKOUT_OPTION_SINGLE_STOP_PCT=0.008`.
- **Why:** Aug 13–14 width study — only ARM win blocked by −0.6% was RKLB (pre-ARM MAE 1.675%). −1.5% still clips; −1.8% lets ARM fire.
- **Docs:** [0DTEProfitCapture.md §3.1 / §4.2](0DTEProfitCapture.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 12, 2026 — Moon hold On (H2) (local)**

- **Keys (`Shared.env` §2b):** `OPTION_MOON_HOLD_ENABLED=true` · `DIR_MIN_PCT=0.65` · `VOLUME_RATIO_MIN=0.8` · `CANCEL_DIR_PCT=0.55` · `MAX_SECONDS=5400` · `PREM_GIVEBACK_PCT=0.15` · BB optional (H5 off).
- **Path force caps:** `0DTE_PATH_CHAIN_SKEW_FORCE_MAX_PER_TICK=4` · `0DTE_PATH_SESSION_TECH_FORCE_MAX_PER_TICK=4` · session tech max/tick **4** — moon-active / missing-dir force ≤**15s** under E\*TRADE **14k/hr**.
- **Behavior:** At deferrable base-exit fire, H2 defers close; later exit reason `moon_hold` (prem giveback / dir cancel / max sec). Invent `volume_ratio` → fail-closed. Hard stops / EOD never defer.
- **Module:** `modules/moon_hold_defer.py` · catalog `MOON_HOLD` · [0DTEProfitCapture.HISTORY.md](PCLegacy/0DTEProfitCapture.HISTORY.md).
- **Deploy:** local only until explicit `deploy now`.

### **August 11, 2026 — Single-flight TL1 · arm-once · compact alerts · Yahoo sleep 0.5 (local)**

- **TL1 architecture:** `_tl1_build_in_flight` + asyncio lock → **exactly one** `_run_deferred_trendline_build_if_pending` per morning; recover skips while in flight; pending **GCS-persisted** (`tl1_build_pending`); cleared only after successful build. After a kill, **run** unfinished TL1 — do not stamp complete.
- **Arm once:** `_arm_deferred_trendline_build_if_needed` stamps `_trendline_tl1_armed_pt_date` at **07:10**; heal recreates missing pending; **do not** clear pending before ORB2 completes.
- **Watch-mode defer:** post-build unlock uses `reason=watch_mode_post_tl1` (not legacy Signal Collection wording).
- **Yahoo:** `TRENDLINE_BUILD_YF_1M_BATCH_SLEEP_SEC=0.5` (and watch hydrate sleep) — confirm `hits=90/90` a few sessions before locking further.
- **Discovery budget:** watches use this scan's merged quotes. If that cache is empty they fetch the current 24 and still evaluate. A slow exit quote does not skip the break check. Those names are not re-quoted for 2 minutes, and Yahoo bars are not loaded before the compare.
- **Alerts:** reject `Reason:` = bare code (`ask_size_too_small` / `bid_size_too_small`); reject title `⚠️ SYMBOL SIDE` without trailing 🟢/🔴; drop duplicate `reject_reason=` and entire `📋 Symbol execution diagnostics:` on Filled/Rejected; bid-size maps to same MQ path as ask-size.
- **Docs:** [0DTETrendline.md](0DTETrendline.md) · [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 10, 2026 — Same-side 1h cooldown reject + post-exit (local)**

- **Shared duration:** `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS=3600` (`Breakout0DTE.env`) for **terminal reject** and **any filled exit**.
- **Scope:** ORB1 `symbol+side` · ORB2 BO `symbol+side` · TL1–TL3 `symbol+slot+side`; opposite side stays open.
- **BO profit reentry:** `BREAKOUT_ALLOW_REENTRY_AFTER_PROFITABLE_EXIT` still clears day-executed + ORB2 reclaim; **exited side** remains on the 1h cooldown.
- **Docs:** [0DTERisk §7.1](0DTERisk.md#71-same-side-1h-cooldown-reject--post-exit) · [PositionMonitoring §8.4](PositionMonitoring.md#84-same-side-1h-cooldown-after-exit) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 10, 2026 — Price-thin prem floor (local)**

- **Keys (`Shared.env` §2b):** `SINGLE_RUNNER_PREM_FLOOR_PCT=0` · `SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_MAX_PCT=0.004` · `SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_PCT=0.20`.
- **Behavior:** After ARM + runner armed, if und HWM fav from entry stays &lt;**0.40%**, prem floor rises to **+20%** (reason `breakeven`). Thick und keeps floor **0%** so `price_trail` can mature.
- **Docs:** [0DTEProfitCapture.md §3.1 / §4.2 / §8](0DTEProfitCapture.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 12, 2026 — Thin und TP30 + strong trail (local)**

- **Keys:** historical rollback uses `0DTE_MFE_TAKE_PROFIT_IF_PRICE_THIN=true` with PX2 off. Active deployment keeps `0DTE_MFE_TAKE_PROFIT_ENABLED=false` and `0DTE_MFE_TAKE_PROFIT_IF_PRICE_THIN=false`.
- **Behavior:** At premium ARM +30%: und fav &lt;0.5% → `mfe_take_profit`; und fav ≥0.5% → `price_trail` 0.20%.
- **Aligned:** `SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_MAX_PCT` **0.004 → 0.005**.
- **Docs:** [0DTEProfitCapture.md §3.1](0DTEProfitCapture.md) · this file §2b.
- **Deploy:** local only until explicit `deploy now`.

### **August 4, 2026 — TL structure Yahoo + 0DTE chain API (local)**

- **ORB2 box:** E*TRADE session H/L at ~7:30; A1 time pinned `orb_*_extreme_ts` @ 06:30 PT.
- **TL1 A2 / TL2–TL3 path:** batched Yahoo RTH 1m (`TRENDLINE_BUILD_YF_1M_*`, `TRENDLINE_WATCH_YF_1M_*`) — not per-symbol; broker `bars=1` fallback only.
- **Empty TL1:** `seed_bar_cache_from_intraday` · watch hydrate-before-inject · **no synthetic A2** on TL2–TL4 (skip inject until Yahoo 1m binds a real higher-low / lower-high).
- **Session tech:** invent-clear RSI/MACD · `0DTE_SESSION_TECH_YF_1M_BACKFILL` for RSI depth.
- **Data / brokers:** E*TRADE is production-complete; IB, Schwab, and TastyTrade remain fail-closed/planned; Yahoo is allow-listed only for session-tech history and TL structure.
- **Chains:** `expiryYear/Month/Day` + `noOfStrikes` so same-day 0DTE is actually requested; ORB1 Breakout / TL / BO choose same-day first, else the earliest listed expiry (expire-date list + empty-day memo).
- **Monitor:** sync underlying spot refresh must not call async `get_batch_quotes` (await path via `refresh_underlying_spot_cache_async`).
- **Docs:** [Data.md](Data.md) · [0DTETrendline.md](0DTETrendline.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 2, 2026 — TL soft window · candidate-only rejects · CONFIRM/READY must-run**

- **TL soft maturation:** `TRENDLINE_ENTRY_WINDOW_SEC=300` — Demo **`00455-lzz`**.
- **TL post-exec rejects:** contract/premium/spread/pc_oi → **current TL candidate only** (no symbol day-exclude).
- **BO rejects / capital TTL:** immediate same-side cooldown + ORB2 re-entry (no soft×3). Duration was **2h** on Aug 2; **1h** since Aug 10 (`BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS=3600`).
- **CONFIRM/READY must-run (historical):** extras every tick beyond the rotate window — **superseded Aug 31** (fair rotate only; status does not pin slots).
- **Docs:** [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · [0DTETrendline.md](0DTETrendline.md) · this file.

### **August 11, 2026 — BE/scratch bands + BO confirm 0.04% (local)**

- **Never-armed BE/scratch:** `breakeven` ≥**60m** · **−0.05% ≤ PnL &lt; +2%** · `scratch` ≥**62m** · PnL **&lt; −0.05%** (`OPTION_BREAKEVEN_EXIT_MAX_PNL_PCT=0.02` · `OPTION_SCRATCH_BELOW_PNL_PCT=-0.0005` · `OPTION_SCRATCH_EXIT_MINUTES=62`).
- **BO confirm floors:** sole **0.08%** (`BREAKOUT_BREAK_MIN=0.08`) — shared ORB1 watch + ORB2. Continuation / FT / chop **deleted** as admit.
- **Docs:** [0DTEProfitCapture.md §4.3A](0DTEProfitCapture.md) · [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · [0DTETrendline.md](0DTETrendline.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **August 9, 2026 — Profit Capture key rename + Hold prune (local)**

- **Canonical §2b keys:** `OPTION_TIME_EXIT_MINUTES` · `OPTION_SINGLE_STOP_BEFORE_ARM_TRAIL` · `OPTION_PRE_ARM_PROFIT_PROTECT_*` · `0DTE_ARM_TRAIL_PCT` · `PX2_*` · `PX25_*` · `PX3_*` · `OPTION_SINGLE_MFE_TRAIL_*` · `OPTION_SINGLE_PRICE_RUNNER_*` · rollback-only `0DTE_MFE_TAKE_PROFIT_IF_PRICE_THIN`.
- **Shipped stack:** `price_stop` −0.6%/−0.8% · ARM_TRAIL +30% · `price_trail` 0.20% · und hard TP OFF · prem floor 0% (+ price-thin +20%) · MFE trail OFF · Hold **removed** · TP ladder env **removed** (`disable_tp_ladder=True` hardcoded).
- **Docs:** [0DTEProfitCapture.md §3.1](0DTEProfitCapture.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **July 31, 2026 — Position Sizing + Liquidity Guard + und-runner (local)**

- **Position Sizing primary:** 90% tradable · 10 equal slots (~9% per open) — [0DTERisk.md §5–§6](0DTERisk.md).
- **Liquidity / ADV Guard secondary (historical):** `LIVE_MAX_CONTRACTS=0` · vol **2%** · OI **5%** · book depth **25%** · ADV **0.5%** · min bid/ask **10**. **Superseded Aug 17 v1.5** (vol **5%** · OI **off** · ask **100%**) then **v1.6** (vol **off**).
- **Exit stack (superseded Aug 9):** und-runner after +30% · `price_stop` −0.6% before ARM_TRAIL · **Hold / mfe trail OFF** — see Aug 9 changelog.
- **Docs:** [0DTERisk.md §11](0DTERisk.md#11-liquidity--adv-guard-execution-risk-layer) · [Brokers.md](Brokers.md) · this file.
- **Deploy:** local only until explicit `deploy now`.

### **July 28, 2026 — Full stack lock (local; Hold later OFF Jul 31)**

- **Hold@0.55 (historical Jul 28):** was ON; OFF Jul 31; **env family deleted Aug 9**.
- **Entry dir% hard:** `0DTE_DIR_VOLUME_*` · `TRENDLINE_DIR_VOLUME_*` · `BREAKOUT_DIR_VOLUME_*` (Layer C; **pc_oi off** Aug 7).
- **OTM ≤ 5.5%:** `0DTE_ENTRY_OTM_PCT_PREFER/MAX=0.055` · `TRENDLINE_*_MAX_OTM_PCT=0.055` · `BREAKOUT_*_MAX_OTM_PCT=0.055` (shared Layer D).
- **Discovery:** `0DTE_DISCOVERY_SYMBOLS_PER_TICK=24` · `0DTE_DISCOVERY_INTERVAL_SEC=10` · while-open dual-cap **removed** · **(Aug 31)** CONFIRM extras no longer pin slots — fair rotate only.
- **TL/BO reject hygiene:** TL soft maturation expires after 300s; TL hard quality **and** post-exec contract/premium/spread/liquidity/pc_oi rejects invalidate **only the current TL1/TL2/TL3 candidate** (siblings stay watchable — no symbol day-exclude). TL full-slot/no-slot re-arms until 1-minute TTL, then current TL signal skip/reject only. BO quality/pc_oi/OTM/structural and BO capital TTL expiry → same-side cooldown + `WAIT_BREAK` (was **2h** Jul 28; **1h** since Aug 10 via `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS=3600`); same side requires ORB2 re-entry and a fresh breakout; **Aug 10** also arms the same cooldown after any filled exit on ORB1 / BO / TL (`path_exec_day_exclude.py`).
- **Universe:** `0dte_list` +42 net new symbols (45 requested, 3 already present) → 132 symbols.
- **Docs:** [0DTE_Exit_Optimization.md](0DTE_Exit_Optimization.md) · [0DTEQualityGateFormalization.md](Improvement%20Guide/0DTEQualityGateFormalization.md) · path guides · this file.
- **Deploy:** local only until explicit `deploy now`.

### **July 16, 2026 — Convex session tech (local)** · **Aug 4 invent-clear**

- **`0DTE_CONVEX_SESSION_TECH=true`:** before Convex @ 7:30, overwrite VWAP/MACD/RSI from today RTH 1m bars (`apply_session_tech_for_convex` · `modules/session_tech_indicators.py`).
- **Why:** daily yfinance VWAP poisoned Jul 16 CALL rejects (e.g. NET/IBIT session <1% vs daily 10–30%).
- **Bar path (Aug 4):** broker quote minutes → day_seed → optional Yahoo RTH 1m backfill when closes &lt; 15 (`0DTE_SESSION_TECH_YF_1M_BACKFILL`); managed via `_yf_trendline_watch_intraday_bars` → `YahooBackfillManager` → `_yf_batch_intraday_rth_bars_blocking` (history only — volume stays quote-delta). RSI/MACD stamp **null** when missing (never invent 50/0).
- **Docs:** [0DTEORB1Breakout.md §8.2](0DTEORB1Breakout.md#82-hydrate--session-tech--chain-skew-steps-3–3b) · [Data.md](Data.md) · [0DTERisk.md](0DTERisk.md).

### **July 14, 2026 — Broker-truth ownership (local)**

- **Sole Live writer:** `modules/broker_position_synchronizer.py` — `synchronize` / `register_confirmed_fill` / `reconcile_confirmed_close` / `apply_premium_state_update`.
- **Premium Engine:** `modules/premium_engine.py` — frozen `PremiumSnapshot` before exit decision.
- **Exit cutover:** `OPTION_STEALTH_USE_EVALUATE_API=true` (default) · `OPTION_STEALTH_EVALUATE_SHADOW=false` · `modules/exit_decision_dispatch.py`.
- **Cert:** `tests/test_forced_mismatch_certification.py` — run on FINAL + RESEARCH.
- **Docs:** [0DTEProfitCapture.md](0DTEProfitCapture.md) · [LiveModeReadiness.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/LiveModeReadiness.md).

### **June 25, 2026 — Canonical execution model (local)**

- **Removed:** `0DTE_BROKER_EXECUTION_ENABLED`, `0DTE_DEMO_MOCK_FALLBACK_ON_BROKER_SUBMIT_FAIL`, `ENABLE_ORB_SO_STRATEGY` (wrong key — use `ENABLE_ORB_STRATEGY` in `ORBSO.env`).
- **`MODE=demo`:** live E*TRADE market data + simulated ledgers (ETF + unified 0DTE) — no broker order submit.
- **`MODE=live`:** broker API fill-confirmed opens/closes on the ORB quote account (`ETRADE_ORB_ACCOUNT_ID`) and the 0DTE options account (`ETRADE_0DTE_ACCOUNT_ID`). Values: [PrivateSecrets.md](PrivateSecrets.md).

### **September 17, 2026 — True-to-live fills vs protect floors (local)**

- Protect / premium-stop / BE **floors are decisions**, not fills. Demo STC = executable bid (`resolve_true_to_live_exit_fill`); Live = broker fill; net `$` includes **$0.65**/contract/side.
- Telegram headlines, PremiumEngine open dollars, mock close, and desk pulses use the same net. Stop **still fires** at −60% of premium.
- **Tests:** `tests/test_pre_arm_profit_protect.py` · `tests/test_max_pnl_telegram_units.py` · `tests/test_premium_engine.py`.
- **Docs:** [0DTEProfitCapture.md §3.11](0DTEProfitCapture.md#311-fills-and-net-pnl-true-to-live) · [PositionMonitoring.md](PositionMonitoring.md).
- Cloud Run **`00545-xvl`** still stamps floors until an explicit post-EOD `deploy now`.

### **June 27, 2026 — Trade economics hardening (local)**

- **`modules/options_trade_economics.py`** — fees in cost basis · net P&L · `breakeven_exit_premium` · **`TRADE_CLOSE_CERTIFICATION`** on every close.
- **Config:** `OPTIONS_OPEN_COMMISSION_PER_CONTRACT=0.65` · `OPTIONS_CLOSE_COMMISSION_PER_CONTRACT=0.65` in `Shared.env` §3e (RESEARCH=FINAL).
- **Wiring:** `MockOptionsExecutor` · `OptionsTradingExecutor` · `TrendlineOptionsExecutor` · `live_options_ledger` (`economic_summary` on closed rows).
- **Tests:** `tests/test_options_trade_economics.py`.

### **June 24, 2026 (b) — True-to-live executable pricing (deployed `00400`)**

- **`modules/option_executable_pricing.py`** — BTO @ **ask** · STC @ **bid** for long 0DTE single-leg CALL/PUT (ORB + Trendline).
- **Config:** `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS=true` · `OPTION_USE_BID_FOR_LONG_EXIT=true` in `Shared.env` §3e.
- **Wiring:** `MockOptionsExecutor` debits ask · monitor/stealth marks bid · legacy `OPTION_STEALTH_TLINE_*` aliases still honored.

### **June 26, 2026 — PP hardening + RESEARCH parity (local)**

- **GCS Step-5 marker:** `portfolio_protection_step5/{date}.json` under daily markers — cross-instance PP parity on Cloud Run.
- **Volume enrichment guard:** `PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT=80` in `ORBSO.env` — defer evaluate when &lt;80% pool rows have real `volume_ratio`.
- **No volume default:** `raw_volume_ratio()` — missing volume never inflated to 1.0 in median/avg metrics.
- **Modules (per app):** `portfolio_protection_step5.py` · `portfolio_protection_gcs.py` · `ensure_pp_ready_before_execution()` before SO + 0DTE (RESEARCH) or 0DTE only (FINAL).
- **RESEARCH parity back-port from FINAL:** daily PP in-memory reset on new PT day · `_signal_collection_validation_blocked` split from `_red_day_filter_blocked` · `_live_0dte_rank_pool()` in tier-policy Convex refresh · convex preview uses `should_apply_portfolio_protection_block()` · Gate **3c** `filter_dte0_signals_below_min_priority_score()` uses computed `floor` (not `None`) · `_ensure_portfolio_protection_gate_at_step5()` → `context=zero_exec_alert`.
- **Tests:** `tests/test_portfolio_protection_volume_gcs.py` (volume · GCS · Gate 3c floor · wiring greps).

### **June 26, 2026 — PP on execution queue + observe-only**

- **Portfolio Protection:** **`ensure_pp_ready_before_execution()`** on post-Convex **`_pending_dte0_signals`** (`path=0dte_execution_queue`). **`PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false`** both apps — observe-only default.

### **June 25, 2026 — PP parity unification (local)**

- **Portfolio Protection:** Retired **`live_0dte_slice`** and SO ∪ 0DTE pool. *(Superseded Jun 26: pool moved to execution queue.)*

### **June 24, 2026 (a) — DEMO/LIVE readiness parity**

- **Portfolio Protection:** Tier arming on 0DTE zero-fill path · **`0DTE_MAX_EXECUTION_CANDIDATES=10`** aligned with LIVE (was 15). *(Slice workaround superseded Jun 25.)*

### **June 23, 2026 — Production deploy (`00386-wdh`)**

- Pipeline **Convex → Hard Gate → rank → Gate 3c**; caps **10/10/20**; alert HTML hardening; Trendline watch guard; `max_position_sum_usd` sizing fix.

### **June 15, 2026 — Telemetry hygiene + ORB entry execution telemetry**

- **Ledger rolling retention:** `MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES=400` in `Shared.env` §6 — prunes closed-trade arrays only; balances/stats preserved (`mock_options_executor._save_mock_data`).
- **GCS lifecycle expansion:** namespaced `research_app/` / `final_app/` execution snapshots **40d**; Trendline `trendline_break_snapshots/` + `trendline_outcomes/` **40d** — `scripts/gcs_lifecycle_easy_etrade_data.json`.
- **ORB entry telemetry:** `metadata.execution_payload` on fill · `EXECUTION_FILL_SUMMARY` — `modules/execution_telemetry.py` (shared with LIVE sibling).

### **June 10, 2026 — Mission structure-first geometry (local)**

- **Trendline:** P0–P4 routing in `trendline_geometry_selection.py` — structure-first classification, legacy-builder mission lock, chase protection, cutoff-control extension to above-ORB ramps. Code defaults: `geometry_structure_first_enabled`, `geometry_structure_first_skip_enabled`, `geometry_legacy_mission_penalty_mse_mult=50`.
- **Validation:** Jun 10 replay **10/10** (`scripts/replay_jun10_geometry_replay.py`); Grade-A cohort preserved; Skip cohort **5/6** blocked at emit layer.
- **Desk:** prod **`00369-7cf`** — **5** wrong CALL geometry (**SPY/QQQ/RGTI/NBIS/QBTS**); fixed locally — see [0DTETrendline.md § Geometry Phase 1](0DTETrendline.md#geometry-phase-1-730-selection).

### **June 9, 2026 — Cutoff-control line (local)**

- **Trendline:** `TRENDLINE_CUTOFF_CONTROL_LINE_ENABLED` · `TRENDLINE_CUTOFF_CONTROL_NEAR_CUTOFF_MINUTES` in `Trendline0DTE.env` §3 — see [0DTETrendline.md §730 formula](0DTETrendline.md#730-trendline-build--design-formula) · [Trendline Strategy Mission](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Trendline%20Improvements/Trendline%20Strategy%20Mission.md).
- **Desk:** prod **`00368-g2k`** — **0** Trendline executes; MSFT/META anchor drift class — [Jun 9 review](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Trendline%20Improvements/TrendlineImprovementGuide.md#jun09-improvement-review-summary).

### **June 8, 2026 — Trendline Red Day gate + caps (deployed 00383)**

- **`TRENDLINE_RED_DAY_GATE_ENABLED=true`** · **10/10** Trendline slots · equity deployed % on alerts — [Jun 8 session](doc_elements/Sessions/2026/Jun%208%20Session/SESSION_SUMMARY_JUN08_2026.md).

### **June 25, 2026 — Unified 0DTE account cleanup (local)**

- **Removed modules:** `easyTrendline/trendline_account_manager.py`, `trendline_reporter.py` — ORB1 Breakout + Trendline use **one** ledger only (`mock_options_history.json` / live broker book).
- **New helper:** `easyTrendline/trendline_metadata_persist.py` — JSON-safe `option_stealth` / `ExecutionIntent` on GCS round-trip.
- **Canonical account API:** `modules/unified_0dte_account.py` (`sync_unified_account_balance`, `unified_account_equity`, `list_trendline_open_positions`, `compute_unified_trendline_capital_snapshot`).
- **Legacy GCS path retired:** `demo_account/trendline_mock_history.json` — not read or written by current code.
- **EOD:** RESEARCH gates SO ETF EOD on `ENABLE_ORB_STRATEGY` (off on Demo → `skipped:so_etf_retired`); unified **`🏦 END-OF-DAY 0DTE`** always. FINAL has no ORB SO — unified 0DTE EOD only.

### **June 22, 2026 — Single-leg profile consolidation + monitor mark fidelity (local)**

- **ORB1 Breakout:** One opening selector — `directional` band only · `strategy_type=single_leg` · `0DTE_SINGLE_LEG_OPENING_*` replaces `0DTE_LOTTO_BREAKDOWN_*` · Demo = `MockOptionsExecutor` only (no broker submit).
- **Monitor (DEMO + LIVE):** `_trendline_option_quote_from_monitor_chain()` chain-first · `update_positions_with_real_prices(shared_chain_cache)` · `_filter_orb_only_open_positions` · `0DTE_DEMO_SYNTHETIC_CHAIN=false`.
- **Removed from active env:** `0DTE_LOTTO_MIN_SO_PRIORITY_SCORE` · `0DTE_DEBIT_LONG_*` (commented — spread experiments only).

### **June 5, 2026 — PUT fulfillment mirror + opening premium relax (local)**

- **ORB1 Breakout:** `0DTE_CONVEX_SHORT_BREAKDOWN_*` (F12/F14) · `0DTE_SINGLE_LEG_OPENING_*` (F13) in `ORB0DTE.env` §3 — sync to Cloud Run on deploy.
- **Trendline:** `TRENDLINE_DEFER_BUILD_UNTIL_ORB_BATCH` in `Trendline0DTE.env` §6.
- **P0 code (no new required env):** `0DTE_HARD_GATE_SYMBOL_SUMMARY`, `SO_EXEC_REJECT`, `ORB_CRITICAL_WINDOW`, `STARTUP_CONFIG_AUDIT`.
- **Desk:** prod **`00364-2n5`** — **MSTR** +503%; PUT **9/9** direction · **1/9** fill — [session](doc_elements/Sessions/2026/Jun%205%20Session/SESSION_SUMMARY_JUN05_2026.md) · [SOversus0DTEFulfillmentGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/SOversus0DTEFulfillmentGuide.md).

### **June 4, 2026 — Mission geometry contest + ORB lotto-first (deployed 00381)**

- **Trendline:** `TRENDLINE_GEOMETRY_MISSION_RESOLUTION_*` — structure-first 7:30 contest; verification `verify_mission_predeploy_matrix.py`. See [Trendline Strategy Mission](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/Trendline%20Improvements/Trendline%20Strategy%20Mission.md).
- **ORB1 Breakout:** `0DTE_ORB_LOTTO_FIRST` · `0DTE_DELTA_VIABILITY_*` · monitor fixes · [Jun 4 session](doc_elements/Sessions/2026/Jun%204%20Session/SESSION_SUMMARY_JUN04_2026.md).

### **June 3, 2026 — Trendline execution + PO timezone + alert UX (00379 / 00360)**

- **Trendline:** `hold_mode` on `TrendlineConfig`; neutral volume gate; **ExecutionIntent** JSON-safe ledger (`modules/execution_intent.py`, `trendline_options_executor.py`, `trendline_metadata_persist.py`).
- **Profit optimizer:** UTC-aware `entry_time` parsing in `profit_optimizer/audit.py` — Jun 3 desk PO JSON not recoverable; first full dataset expected next session.
- **ORB1 Breakout:** ghost DEMO purge; kill-switch clear; compact trade IDs; exit alert layout; **`% Of Account`** on execution rows.
- **Shared.env:** `ORB_RTH_INTRADAY_DISABLE`, `0DTE_CONVEX_ORB_MOMENTUM_VOL_FLOOR`, `PROFIT_OPTIMIZER_STALE_QUOTE_SECONDS`, `ALERT_LEVEL_*`, `APP_VERSION=00379`. Session: [Jun 3](doc_elements/Sessions/2026/Jun%203%20Session/SESSION_SUMMARY_JUN03_2026.md).

### **June 2, 2026 — ORB1 Breakout profitability + Trendline Geometry Phase 1 (00358)**

- **ORB1 Breakout:** `0DTE_SINGLE_LEG_DIRECTIONAL_*`, opening-window OI/spread relax, `0DTE_HARD_GATE_EXEC_SPREAD_RELAX_MULT=1.25` in `ORB0DTE.env`; monitor `quote_grade` fix (code). Tests `test_orb_0dte_jun02_2026_profitability_recovery.py`.
- **Trendline:** six **`TRENDLINE_GEOMETRY_PHASE1_*`** / line-relevance / MSE-override keys in `Trendline0DTE.env` §3; module `trendline_geometry_selection.py`; grep `GEOMETRY_*_AUDIT`. Elite validation doc: [GEOMETRY_ELITE_SETUP_AUDIT.md](Improvement%20Guide/Trendline%20Improvements/GEOMETRY_ELITE_SETUP_AUDIT.md).

### **April 30, 2026 — Trendline docs + settings alignment**

- Easy Trendline: **selector-built** production path and snapshot **`source`** values; **`TRENDLINE_SELECTOR_*`** / **`use_selector_built`** log tokens replace deprecated **`TRENDLINE_PREBUILT_*`** names in code; **`TRENDLINE_POST_BREAK_SURVIVAL_BARS`**, per-bar **`TRENDLINE_DECISION_SNAPSHOT`**, **`TRENDLINE_MISSED_WIN_EARLY`**, flow/checkpoint logs documented in [0DTETrendline.md](0DTETrendline.md) and Easy Trendline section above.

### **May 13, 2026 — ORB1 Breakout + ORB SO ETF operational alignment**

- ORB1 Breakout: pre-queue / skip-stage logging, spot fallback, viability default **0.30** (`orb0dte_execution_defaults.py`), chain relax **0.76**, selector momentum + **`0DTE_LIQUIDITY_RELAX_EXTRA_SYMBOLS`**, lifecycle / EOD flatten telemetry — see [0DTEORB1Breakout.md](0DTEORB1Breakout.md).
- ORB SO ETF: startup stealth rehydrate from mock (Demo) or E*TRADE portfolio (Live); **`PrimeUnifiedTradeManager.close_position`** for broker-only closes; **`ExitReason.MANUAL`** for shutdown — see May 13 session summary.
- Watchlists: **RGC** and **CGON** removed from `core_list` / `0dte_list` (strategy + collector).

### **May 6, 2026 — ORB1 Breakout settings accuracy refresh**

- Updated ORB1 Breakout debit-spread delta defaults to match `configs/ORB0DTE.env` (`0DTE_DEBIT_SPREAD_TARGET_DELTA_MIN=0.30`, `0DTE_DEBIT_SPREAD_TARGET_DELTA_MAX=0.45`). **Jun 18:** those keys removed from env (no runtime readers); docs/examples updated accordingly.
- Added current reliability note alignment for ORB1 Breakout runtime behavior (Convex min-score helper read + Cloud Run-first watchlist path handling).

### **Latest Updates (January 22, 2026 - Rev 00259)**

**Rev 00259 (Jan 22 - Cloud Cleanup Automation)**:
- ✅ Comprehensive settings documentation
- ✅ Complete configuration file reference
- ✅ All settings organized by category
- ✅ Sensitive information moved to PrivateSecrets.md

### **Previous Updates**

**Rev 00247 (Jan 20 - Critical Bug Fixes)**:
- ✅ ETrade API Batch Limit Fix
- ✅ 0DTE Import Path Fix
- ✅ Deployment Configuration fixes

**Rev 00246 (Jan 19 - 0DTE Strategy Improvements)**:
- ✅ 0DTE Priority Score Formula v1.1
- ✅ Direction-Aware Red Day Filtering
- ✅ Expanded Delta Selection (0.15-0.35)

**Rev 00245 (Jan 19 - Broker Configuration System)**:
- ✅ Multi-Broker Architecture
- ✅ Account Mapping (ORB and 0DTE)
- ✅ Broker Config Manager

**Rev 00233 (Jan 8 - Secrets Management)**:
- ✅ Two-tier secrets management system
- ✅ All sensitive credentials moved to `secretsprivate/`
- ✅ Config files cleaned (no hardcoded secrets)

---

**Configuration Guide (Configs.md) — operator handbook + complete reference**

*Last Updated: September 8, 2026*  
*Operator sections: Production snapshot · Strategy quick ref · Live readiness · Startup validation*  
*Config taxonomy: [configs/README.md](../configs/README.md#configuration-taxonomy-by-concern)*  
*Live Mode: [FINAL LiveModeReadiness.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/LiveModeReadiness.md)*  
*Production @ Jun 24: **`00400-jun24-0dte-sizing-alert-fix`** · LIVE sibling **`00402-jun24-0dte-sizing-alert-fix`***

## LIVE PC stack update 2026-09-22 (bid-aware working BE)
# Configs.md — LIVE stack patch (apply into both apps' `docs/Configs.md`)

**Stamp:** 2026-09-22 PT · historical Research ≡ Final note (FINAL is retired) · local ship (no cloud deploy)

## LIVE Profit Capture stack (replace prior EARLY_BE / PRE_ARM hold lines)

| Knob | Value | Notes |
|------|-------|-------|
| `OPTION_EARLY_BE_ENABLED` | `true` | Band D aggressive FADE |
| `OPTION_EARLY_BE_MAX_MFE_PCT` | `0.20` | peak MFE must stay &lt;20% |
| `OPTION_EARLY_BE_MIN_HOLD_MINUTES` | `5` | unchanged |
| `OPTION_EARLY_BE_MIN_CURRENT_PNL_PCT` | **`-0.05`** | was `-0.02` |
| `OPTION_EARLY_BE_FLOOR_PNL_PCT` | `0.00` | decision floor only |
| `OPTION_PRE_ARM_PROFIT_PROTECT_MIN_HOLD_MINUTES` | **`30`** | was `20` |
| `OPTION_PRE_ARM_PROFIT_PROTECT_MIN_CURRENT_PNL_PCT` | `-0.05` | unchanged |
| `OPTION_PROTECT_BID_AWARE_BE_ENABLED` | **`true`** | NEW — working BE |
| `OPTION_PROTECT_BID_AWARE_BE_WAIT_MINUTES` | **`2`** | NEW |
| `OPTION_PROTECT_BID_AWARE_BE_HARD_STOP_PCT` | **`-0.25`** | NEW |
| `OPTION_PROTECT_BID_AWARE_BE_CANCEL_IF_PEAK_ABOVE_RULE_MAX` | **`true`** | NEW late-bloomer rescue |
| `OPTION_PROTECT_BID_AWARE_BE_TARGET_PNL_PCT` | **`0.00`** | reclaim to entry/BE |
| `OPTION_USE_BID_FOR_LONG_EXIT` | `true` | unchanged |
| `USE_MARKET_ORDERS` | `false` | unchanged |

Fills remain liquidable **bid**. Decision floors never stamp Demo/Live fills (`protect_floor_not_fill`).

CAP / PX2 stall / PREM_TRAIL unchanged this ship.


## Chain fetch manager (Oct 13 2026) — `configs/Shared.env`

| Key | Default | Meaning |
|---|---|---|
| `CHAIN_FETCH_MANAGER_ENABLED` | true | Shared chain bucket, dir% missing → defer (no cooldown), prewarm under the bucket, 5m resample. `false` restores the old behaviour. |
| `CHAIN_FETCH_BUDGET_PER_MIN` | 24 | Chain fetches/min (each = 2 real calls). |
| `CHAIN_FETCH_BURST` | 8 | Bucket capacity. |
| `CHAIN_FETCH_LOW_PRIORITY_RESERVE` | 6 | Tokens resample/prewarm leave for gate/position. |
| `CHAIN_FETCH_REAL_CALLS_PER_CHAIN` | 2 | Real calls per chain, used for headroom. |
| `CHAIN_FETCH_LOW_PRIORITY_SAFE_PER_MIN` | 100 | Live-minute real-call ceiling for P2/P3. |
| `CHAIN_FETCH_HIGH_PRIORITY_SAFE_PER_MIN` | 130 | Live-minute real-call ceiling for P0/P1. |
| `CHAIN_FETCH_SCAN_CEILING` | 24 | Per-scan live chain ceiling when the manager is on (`0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN` applies only when it is off). |
| `CHAIN_FETCH_PREWARM_FLAT_BYPASS` | true | No open positions: the prewarm uses only the headroom check. |
| `CHAIN_RESAMPLE_ENABLED` | true | Low-priority 5m resample. |
| `CHAIN_RESAMPLE_INTERVAL_SEC` | 285 | Resample cadence per symbol. |
| `CHAIN_RESAMPLE_TICK_SEC` | 15 | Sampler tick. |
| `CHAIN_RESAMPLE_MAX_PER_TICK` | 3 | Fetches per tick. |
| `CHAIN_RESAMPLE_CANDIDATE_TTL_SEC` | 900 | A gate candidate stays in the resample set this long. |
| `CHAIN_RESAMPLE_OPEN_POSITIONS` | true | Also resample held underlyings (P0). |
| `DIR_PCT_DEFER_MAX_WAIT_SEC` | 90 | Bounded dir% data wait, then `dir_pct_data_unavailable`: the break is retired, no cooldown. |
| `CHAIN_FETCH_ADAPTIVE_BUDGET` | true | chains/min = (FRACTION × HARD_CAP − last minute's non-chain real calls) / CALLS_PER_CHAIN, clamped to FLOOR..CEILING. `false` = fixed `CHAIN_FETCH_BUDGET_PER_MIN`. |
| `CHAIN_FETCH_HARD_CAP_PER_MIN` | 140 | Hard cap the adaptive budget targets. |
| `CHAIN_FETCH_ADAPTIVE_CAP_FRACTION` | 0.85 | Share of the hard cap the budget aims for. |
| `CHAIN_FETCH_ADAPTIVE_FLOOR_PER_MIN` | 6 | Minimum chains/min. The headroom checks still apply. |
| `CHAIN_FETCH_ADAPTIVE_CEILING_PER_MIN` | 40 | Maximum chains/min. |
| `CHAIN_CALLS_COUNT_TOWARD_HARD_CAP` | true | Live chains are recorded as real broker calls (`lane=chain`, × `CHAIN_FETCH_REAL_CALLS_PER_CHAIN`) in `real_broker_total`, so `OPTION_API_HARD_CAP` sees them. |
| `MAX_OPEN_POSITIONS_PRECHECK_ENABLED` | true | Cap-first gate check (TL, BO, ORB1). When the book is full: `max_open_positions_reached` with open_n/cap, no chain, no dir%, no cooldown, capacity retry. |
| `0DTE_QG_LIVE_CHAIN_TIGHT_AT_OPEN` | 7 | Legacy path (manager off) only: the open count at which the tight `..._WHILE_OPEN` per-scan cap starts (was hard-coded 7). |

**50-position book (10/13 maxpos, deployed 00599-lhd):** `configs/Account0DTE.env` sets `TOTAL_0DTE_MAX_POSITIONS`, `0DTE_SLOT_COUNT`, `ORB_0DTE_MAX_POSITIONS`, `BREAKOUT_0DTE_MAX_POSITIONS`, `TRENDLINE_0DTE_MAX_POSITIONS` and `0DTE_MONITOR_MAX_SYMBOLS` to 50. `configs/Shared.env` sets `MAX_TOTAL_OPTION_POSITIONS=50`. `0DTE_MAX_POSITION_SIZE_SUM=600` is unchanged and binds every entry. `0DTE_MONITOR_EVAL_BUDGET_MS_OPEN20=1800` (`Shared.env`) applies at 20+ opens, capped at 2200 ms. All of this is local, pending next deploy (live `00598` runs 7).

## Oct 13 2026 — config dedupe + TL slot fast path (deployed 00599-lhd)

**Live 00598 still runs the pre-change image** (caps 7, duplicates present) until the next deploy.

Dedupe: 53 redundant lines removed (net, across ORB0DTE/Shared/Account0DTE). Every one had the same name and value in its canonical file. The merged config from `load_configuration("standard","demo","development")` is byte-identical before and after (1338 keys, 0 diffs; only addition is the new FAST_PATH key). No removed key matches a `config_profiles.py` per-file prefix bundle.

| Keys | Canonical file | Removed from |
|---|---|---|
| 0DTE exit/ownership/monitor keys (`0DTE_EXIT_QUOTE_*`, `0DTE_OWNERSHIP_*`, `0DTE_*_PRICE_FIELD`, `0DTE_EOD_*`, `0DTE_POSITION_MONITOR_INTERVAL_SEC`, `0DTE_DEMO_SYNTHETIC_CHAIN`, `0DTE_ALLOW_SYNTHETIC_EXIT_QUOTES`, …), `REQUIRE_LIVE_OPTION_DATA`, `OPTION_SINGLE_EXIT_GRADE_BYPASS` | Shared.env | ORB0DTE.env |
| `LIVE_*` size/close/quote-refresh keys, `EXEC_*_FILL_RECONCILE_TIMEOUT_SEC` | Account0DTE.env (sizing owner; pinned by test_unified_0dte_portfolio_risk_manager) | Shared.env |
| `LIQUIDITY_GUARD_*` | Risk.env | Shared.env, Account0DTE.env |
| `GCS_SESSION_RETENTION_DAYS` | Data.env | Shared.env |
| `BREAKOUT_BREAK_MIN`, `QG_BREAK_MAX` | Breakout0DTE.env | ORB0DTE.env |
| `0DTE_SHALLOW_E14_ENABLED`, `0DTE_SHALLOW_CALL_MACD_MIN/PUT_MACD_MAX` (listed twice in the same file) | ORB0DTE.env (first copy kept) | ORB0DTE.env |

Left on purpose:
- `QUALITY_GATE_STACK_CUTOVER/SHADOW` stays in each path file, because each path owns it.
- Aliases with different names are noted but not removed: `0DTE_EXECUTOR_MAX_POSITION_SIZE_PCT`, `ORB_0DTE_DEGRADED_ENTRY_KILL_SWITCH_*`, `MAX_TOTAL_OPTION_POSITIONS`, `0DTE_OPTIONS_MIN_OPEN_INTEREST` / `0DTE_MIN_OPEN_INTEREST` / `0DTE_ENTRY_MIN_OPEN_INTEREST`, `TRENDLINE_OPTION_*`.

TL slot count: set explicitly through `0DTE_SLOT_COUNT=50` and `TRENDLINE_0DTE_MAX_POSITIONS=50` (Account0DTE). The retired alias `TRENDLINE_SLOT_COUNT` was **not** added, because adding it would create a new duplicate.

New key: `TRENDLINE_GLOBAL_DAILY_CAP_FAST_PATH=true` (Trendline0DTE.env). See 0DTERisk.md.

### Single Trade Profile admit keys (Oct 13 2026, local)

| Key | Default | Meaning |
| --- | --- | --- |
| `SINGLE_TP_ADMIT` | pinned `true` on Demo | The admit is this path only: Discovery → Must Execute (incl. Skip Signals, one pick) → single stamp → one Trade Profile. The old flag-off stack is not in the code. Demo deploy still requires the env value `true`. |
| `TRADE_PROFILE_VERSION` | `10.5` (code default if env unset) | **Live Demo pins `wide`.** `wide` = TP Wide (`easy0DTE/configs/trade_profile_wide.json`); `10.5` = unset-env / replay baseline; `10.16` = historical continuation; `10.17` / `10.18` = historical continuation profiles; `10.20` / `10.21` = historical comparison profiles; `10.22` = baseline and parent of 10.23 (flat source profile, no longer current); `10.23` = **current local flat successor** (`trade_profile_10.23.json`, current sha256 `a449ab0d…` = S0_maxnet 11/6, 8/19–11/6 542 / 436W/106L / net +$159,400.39, not the live admit; previous C0_maxws `46f4305d…`; earlier P2_AMZN `d480f7c1…` = 10.23 FINAL + P3 + Option B + Option 1 + ORB1 CALL Q2 + Max + A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 + A99 + N3 + N100 + P2_AMZN (P2_AMZN 11/5 t74u: 9 flat bounds on N100, 8/19–11/5 528 / 425W/103L / +$166,255.64 / −$6,977.55 / net +$159,278.09; red days 09/04 + 10/09; 11/5 TP 5W/0L +$2,477.70; see 0.TradeProfiles.md *10.23 CURRENT — S0_maxnet on C0_maxws (11/6)*); before P2_AMZN N100 `a3c4cc86…` (N100 11/4 t65: 16 flat bounds, max net under losses ≤ 100 / loss sum ≥ −$6,600; 8/19–11/4 520 / 420W/100L / +$157,175.79; red days 09/04 + 10/09; 11/4 TP 3W/0L +$2,814.90); before N100 N3 `0ef7a804…` (N3 11/3 t48u: 14 flat bounds, 8/24 green; 8/19–11/3 519 / 420W/99L / +$154,050.14; red days 10/09 only; 11/3 TP 4W/2L +$45.30; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 + A99 + N3 (11/3)*); before N3 A99 `90f98504…` (A99 11/3 pt40u: 77 flat bounds, losses 112 → 99; engine `32a80291` / `5e447ecd`; 8/19–11/3 523 / 424W/99L / +$154,117.94; 11/3 TP 4W/2L +$45.30; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 + A99 (11/3)*); before A99 T1_E2 `b0f1489c…` (T1_E2 11/3 pt30u: 41 flat bounds, loss-sum cap −$6,468.16; engine `32a80291` / `5e447ecd`; 8/19–11/3 536 / 424W/112L / +$154,273.89; 11/3 TP 5W/2L +$134.70; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap + T1_E2 (11/3)*); before T1_E2 S1_Q2cap `5a8a0dd9…` (S1_Q2cap 11/3 pt27u: 51 flat bounds, loss-sum cap −$6,468.27; engine `32a80291` / `5e447ecd`; 8/19–11/2 507 / 406W/101L / +$153,986.03; 11/2 TP 7W/3L +$2,768.10; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 + S1_Q2cap (11/3)*); before S1_Q2cap P4_rel9 `f8c41a59…` (P4_rel9 11/3 pt21u: 24 flat ORB1 CALL / TL2+ PUT / Breakout PUT bounds; engine `32a80291` / `5e447ecd`; 8/19–11/2 502 / 401W/101L / +$152,485.29; 11/2 TP 7W/3L +$2,251.20; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 + P4_rel9 (11/3)*); before P4_rel9 A_g7 `0f296670…` (A_g7 11/3 pt15u: 5 flat TL0 CALL / TL1 PUT bounds; engine `32a80291` / `5e447ecd`; 8/19–11/2 500 / 401W/99L / +$151,770.53; 11/2 TP 7W/3L +$2,251.20; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 + A_g7 (11/3)*); before A_g7 SV_lean1 `ff0255c7…` (SV_lean1 11/3 pt13u: 47 flat bounds for the 20% Save exit winners; engine `eb812a6f` / `b0065e9d`; 8/19–11/2 499 / 400W/99L / +$151,727.43; 11/2 TP 7W/3L +$2,251.20; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A + SV_lean1 (11/3)*); before SV_lean1 R2_A `495b3bcc…` (R2_A 11/2 pt8u: 7 flat TL0 CALL / TL0 PUT bounds; 8/19–11/2 492 / 392W/100L / +$150,509.87; 11/2 TP 6W/4L +$2,171.70; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL + F105 + R2_A (11/2)*); before R2_A F105 `29805ada…` (F105 11/2 Eric: 7 flat bounds; 8/19–11/2 492 / 391W/101L / +$150,038.05); before F105 X_ALL `2c0f0578…` (X_ALL 10/30 pt129u: 11 flat TL1 PUT / TL2+ PUT bounds; 8/19–10/30 488 / 387W/101L / +$148,636.26; 10/30 TP 12W/0L +$3,595.60; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 + X_ALL (10/30)*); before X_ALL W2 `8ed30395…` (W2 10/30 pt125u: 18 loosened flat bounds; 8/19–10/30 488 / 388W/100L / +$148,304.06; 10/30 TP 11W/0L +$3,560.60; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 + W2 (10/30)*); before W2 H1 `cf4f671b…` (H1 10/30 pt121u: 13 flat TL1 PUT / TL2+ PUT bounds; 8/19–10/30 475 / 375W/100L / +$147,185.74; 10/30 TP 9W/0L +$3,155.00; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 + H1 (10/30)*); before H1 L2 `787353df…` (L2 10/30 pt118u: 3 flat Breakout PUT bounds; 8/19–10/30 478 / 382W/96L / +$145,846.93; 10/30 TP 7W/0L +$2,406.90; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean + L2 (10/30)*); before L2 Lean `f71eca7d…` (Lean 10/30 pt114u: 7 flat bounds in TL1 PUT / TL1 CALL / ORB1 PUT / TL2+ PUT / TL0 PUT; 8/19–10/30 477 / 381W/96L / +$144,607.83; 10/30 TP 6W/0L +$1,159.90; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 + Lean (10/30)*); before Lean A + P1 + N2 `52468772…` (N2 10/29 t111u: 3 flat bounds in ORB1 PUT / TL2+ CALL, 8/19–10/29 net +$143,562.43; audit fix of `139eea4e…`; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 + N2 (10/29)*); before N2 A + P1 `0fb839ea…` (P1 dir%-backup 10/29 t109u: TL0 PUT `bollinger_width` max → 0.05, admit set identical to A; see 0.TradeProfiles.md *10.23 PREVIOUS — A + P1 (10/29)*); before P1 A `e0b1e4b3…` (A 10/29 t107u, 24 bounds in TL2+ CALL / Breakout CALL / ORB1 PUT; 8/19–10/29 471 / 374W/97L / +$149,856.92 / −$6,387.83 / +$143,469.09; 10/29 TP 3W/0L +$318.30; see 0.TradeProfiles.md *10.23 PREVIOUS — A (10/29)*); before A Max `cec10e5d…` (10/29 t104u, 5 bounds in TL0 PUT / TL1 CALL / TL2+ PUT; 8/19–10/29 on PC V2 `569a516c`/`37bd9d1f` 475 / 377W/98L / +$149,692.02 / −$6,434.43 / +$143,257.59; 10/29 TP 2W/0L +$280.20; see 0.TradeProfiles.md *10.23 PREVIOUS — Max (10/29)*); before Max ORB1 CALL Q2 `82e8f1d9…` (10/28 t93u, 7 epsilon-centered bounds in ORB1 CALL; 8/19–10/28 476 / 378W/98L / +$149,488.92 / −$6,434.43 / +$143,054.49; 10/28 TP 5W/4L −$65.90; see 0.TradeProfiles.md *10.23 PREVIOUS — Option 1 + ORB1 CALL Q2 (10/28)*); before Q2 Option 1 `8ddf23b0…` (10/28 t83u, 8 bounds in TL0 CALL / TL0 PUT / TL2+ CALL / Breakout CALL / Breakout PUT; 8/19–10/28 on PC V2 `569a516c`/`ea15c978` 479 / 379W/100L / +$149,310.32 / −$6,505.63 / +$142,804.69, Total Results 6,474 / 1,430W/5,044L / −$291,457.81; 10/28 TP 5W/4L −$65.90; see 0.TradeProfiles.md *10.23 PREVIOUS — Option 1 (10/28)*); before Option 1 B `f024dfe9…` (10/27 t77u, 12 bounds in ORB1 PUT / TL0 CALL / TL0 PUT / TL1 PUT / TL2+ PUT / Breakout CALL / Breakout PUT; 8/19–10/27 on Profit Capture V2 current (path_v2_exit `569a516c`, Shared.env `ea15c978`) 470 / 375W/95L / +$149,209.22 / −$6,355.83 / +$142,853.39, Total Results 6,262 / 1,389W/4,873L / −$276,000.81; 10/27 TP 2W/1L −$50.70; see 0.TradeProfiles.md *10.23 PREVIOUS — B (10/27)*); before B P3 `a4dd0222…` (8/19–10/26 468 / 373W/95L / +$143,376.19 on engine `ee93d7d4`/`931d8208`); before P3 `e0d7bf3b…`, earlier flattened `aef9dfbb…` final flattened file (pre-flatten `fea583ea…` after package H + 8/20 A_cons and the metadata-text refresh), aliases `10.23flat` / `tp1023`, override `TRADE_PROFILE_1023_JSON`) started with exact parity to the 10.22 closed 8/19–10/22 baseline **370 / 308W/62L / +$134,774.63**; package H (155 key changes vs P1) gave **8/19–10/23 475 / 373W/102L / +$141,424.79 (STEP 9 (ex-7A))**, 10/23 18W/0L +$3,358.70; the 8/20 A_cons update (4 Breakout CALL changes, A_cons-stage sha `1c0aa3b1…`, metadata-refresh sha `fea583ea…`, flattened sha `aef9dfbb…`) gave **462 / 368W/94L / +$141,654.99 (STEP 9 (ex-7A))**, 10/23 16W/0L +$3,123.00. Local only, not deployed. (10/23 was interim then; the final t54u reconcile gives 462 / 368W/94L / +$141,521.99.) Deploy pins `wide` (protected in `scripts/deploy_demo_service.sh`). Fixed 10/23: `evaluate_trade_profile` (`trade_profile.py` line 910, used by the legacy `SINGLE_TP_ADMIT=false` path and the shadow call) now lists 10.23, so the decision version is `10.23` (it used to fall through to the 10.2 evaluator); `tests/test_trade_profile_10_23.py` covers it. No `configs/*.env` file sets `TRADE_PROFILE_VERSION`; the Demo deploy keeps `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`. |
| `TRADE_PROFILE_105_JSON` / `TRADE_PROFILE_1016_JSON` / `TRADE_PROFILE_1017_JSON` / `TRADE_PROFILE_1018_JSON` / `TRADE_PROFILE_1020_JSON` / `TRADE_PROFILE_1021_JSON` / `TRADE_PROFILE_1022_JSON` / `TRADE_PROFILE_1023_JSON` | – | Optional local file overrides for the flat profiles. A bad selected file fails closed at profile load. |
| `TRADE_PROFILE_SHADOW_VERSION` | off | Evaluate the other version on the same stamp; recorded in `po_decision_freeze.single_tp.shadow`, never blocks. |
| `TRADE_PROFILE_WIDE_JSON` | – | Override path for the Wide JSON (fail closed: a bad file rejects every signal `tp_reject:config_error:missing`). |
| `0DTE_MAX_SPREAD_PCT` | `8.0` | SINGLE_TP_ADMIT only. Informational base; NOT a cross-path override (path keys / path defaults win). Unit % points of mid ((ask−bid)/mid×100). Replaces `0DTE_PICKER_MAX_SPREAD_PCT` (removed 10/13). |
| `0DTE_MAX_SPREAD_PCT_ORB1` / `_BREAKOUT` / `_TRENDLINE` | `8.0` / `30.0` / `30.0` | SINGLE_TP_ADMIT only. Per-path cap; enforced in the shared picker and re-checked at submit (executor pick loop, ORB1 opening selector, `option_entry_liquidity_validator`, `validate_single_leg_before_execute`). Values ≤ 1 are read as fractions. |
| `0DTE_SPREAD_CHEAP_MAX_USD` | `0.01` | SINGLE_TP_ADMIT only. A quote with ask−bid ≤ this passes whatever the %, so one-tick cheap contracts (< ~$0.25) can enter. |
| Kept, read by the picker | – | `0DTE_MIN/MAX_ENTRY_PREMIUM` (0.03/2.50), `0DTE_ENTRY_DELTA_MIN/MAX` (0.18/0.42) + `TRENDLINE_DELTA_TOLERANCE`, `0DTE_ENTRY_MIN_VOLUME/OPEN_INTEREST` (50/100), `*_RESEARCH_MAX_OTM_PCT` / `*_PREFER_MAX_OTM_PCT` (0.055, Breakout 0.035), `0DTE_MAX_POSITION_SIZE_SUM` ($600). |
| Still read (outside TP) | – | `GATE_call_ss`, `GATE_put_ss`, `QG_CALL_SS_*`, `QG_PUT_SS_*`. |
| Unused when the flag is on (delete after two clean sessions) | – | `QUALITY_GATE_ADMIT`, other `GATE_ORB_*/GATE_TL_*/GATE_BO_*`, `QG_BREAK_MAX`, `QG_BO_BREAK_MAX`, dir% floor keys, `GATE_TL_Vol_Ratio_MIN`, `0DTE_HARD_GATE_*_VOL_FLOOR`, `BREAKOUT_MIN_VOLUME_RATIO`, `BREAKOUT_CHEAP_LOTTO_*`. Their levels now live in `trade_profile_wide.json`. |
