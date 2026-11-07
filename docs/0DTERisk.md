# Easy 0DTE — Risk & Position Sizing

**Serving now (Oct 30 2026):** RESEARCH Demo `easy-etrade-strategy-00619-xr6` · `MODE=demo` · `SINGLE_TP_ADMIT=true` · `TRADE_PROFILE_VERSION=wide` · sim ledger **$100,000** · 50 slots · `$600` sum. FINAL Live is **retired**. Slot, buffer, and the liquidity guard still run before the order. The strike they size is the Trade Profile pick: [0.OrderExecution.md](0.OrderExecution.md). Calendar: [MarketCalendar.md](MarketCalendar.md) — local skip gate matches Training Calendar (`Day Before …` does not double). **Local until `deploy now` (Nov 2):** Wide volume floor **0.012** all cells; empty two-sided chain hydrates live; pick hint keeps bid/ask size so Liquidity Guard is not `ask_size_missing` on a valid book.

> **Oct 13 2026 — Single Trade Profile admit path (**DEPLOYED** 10/13 3:08 PM PT in `easy-etrade-strategy-00599-lhd`: `SINGLE_TP_ADMIT=true`, `TRADE_PROFILE_VERSION=wide`, no `TRADE_PROFILE_SHADOW_VERSION`; the 10.5 JSON is loaded but not the admit; code default `false`).** RESEARCH only. With the flag off (rollback) the app runs exactly as before (legacy wide_collect / GATE_* stack). With the flag on, every signal runs:
>
> 1. **Discovery Scan** (unchanged; 10 s, 24-symbol batches).
> 2. **Must Execute** — feasibility only, never quality. In order: signal geometry (`orb_break`/`orb_range`/`regime`, `tl_break`, `bo_confirm`; ORB `PS`/`top_n` rank only) → cooldown / day-exclude → book-full precheck (`max_open_positions_reached`, no API, no cooldown) → **one** chain fetch via `chain_fetch_manager` → data readiness (`data_wait:<key>` holds the break; after `DIR_PCT_DEFER_MAX_WAIT_SEC` (90 s) it retires as `dir_pct_data_unavailable` / `data_unavailable:<key>`, no cooldown) → **Call / Put Skip Signals** (`GATE_call_ss` / `GATE_put_ss`, same evaluators and `QG_*_SS_*` levels, kept OUTSIDE the Trade Profile; CALL SS may early-out pre-chain on TL/BO and is not re-run; PUT SS runs at chain time) → `skip_signal:<call|put>` → **one shared contract pick** (premium 0.03–2.50, \|delta\| 0.18–0.42 (+tolerance), OTM ≤ 0.055 (Breakout 0.035), contract vol ≥ 50 / OI ≥ 100 when present, one spread rule: spread % of mid ≤ `0DTE_MAX_SPREAD_PCT_ORB1` 8 / `_BREAKOUT` 30 / `_TRENDLINE` 30 (Breakout has its own key) or ask−bid ≤ `0DTE_SPREAD_CHEAP_MAX_USD` ($0.01), re-checked at submit) → `contract_unavailable:<delta_out_of_band|otm|premium_low|premium_high|volume|oi|spread|no_quote>`.
> 3. **Single stamp** (`central_tech/decision_stamp.py`): one immutable dict = the TP decision keys read once from the hydrated row + `call_share`/`put_share` (computed once) + `equity_volume_ratio` (Wide extension) + the **picked** contract's `delta`/`otm_pct`. `decision_digest = sha256(canonical keys)`. The same dict feeds the TP, the decision record, the handoff snapshot freeze (`po_decision_freeze.decision_digest`) and the TP_DECISION log line; the TP returns `evaluated_digest` and a mismatch rejects `data_unavailable:stamp_parity`.
> 4. **Trade Profile** — exactly one evaluation, `TRADE_PROFILE_VERSION=wide|10.5` (`easy0DTE/configs/trade_profile_wide.json` or `trade_profile_10.5.json`); optional `TRADE_PROFILE_SHADOW_VERSION` evaluates the other version on the same stamp (record only, never blocks).
> 5. **Accept / reject** — `tp_admit:<cell>` or `tp_reject:<key>:<min|max|missing|not_and>`.
> 6. **Sizing / caps** (unchanged) — $600 per position (floor(600/(premium×100)), min 1; a $0.05 contract = 120), max open 50 everywhere incl. TL slots, TL slot persistence/recovery, `capacity:*`.
>
> **Removed from admit when the flag is on:** the GATE_* quality stack (dir%, Vol_Ratio, break_max, pc_oi, path_ss, entry_part, red_day, observe stamps), Layer D contract gates, the post-TP contract-band check, cheap-lotto. dir%, Vol_Ratio and break_max now live in TP Wide lines. PO snapshot writers (entry, handoff, 20p, exit), discovery, the position monitor and the flight recorder are unchanged.
>
> **Live missing-value policy = the loaded profile JSON (Eric 10/13; no code-side table).** `single_tp_admit` applies no pre-policy. **10.5:** `trade_profile_10.5.json` via `trade_profile._evaluate_10_5` — missing = pass, except each cell's `missing_reject_keys` (ORB1 CALL: PVE 3m, distance_from_vwap_atr, rsi; ORB1 PUT: PVE 3m, rsi; TL0 CALL: distance_from_vwap_atr, rsi; TL0 PUT: PVE 3m, distance_from_vwap_atr, rsi); `zero_is_missing` (dir_pct_change_5m, spy_change_pct) counts as missing; derived share lines pass on missing input; 10.5 never data_waits. **Wide:** `trade_profile_wide.json` `semantics.missing_default` = pass, per-cell `missing_reject_keys` = [equity_volume_ratio] (legacy Vol_Ratio), `missing_defer_keys` = the share key → `data_wait` (legacy dir% defer); break_distance_pct missing passes (legacy break_max skip). Verified: single-TP flag-on 10.5 8/19–10/13 = 216 / 167W/49L / +$96,369.82, row-for-row identical to the confirmed book; wide parity 257/257.
>
> **TP Wide** (`trade_profile_wide.json`, built by `build_tp_wide.py`): per cell `call_share`/`put_share` > 0.10 (strict, same side); `break_distance_pct` ≤ 0.025 (ORB1, Breakout); **`equity_volume_ratio` ≥ 0.012 inclusive on all ten cells** (Nov 2 2026; was 0.12 on ORB1/Breakout). No spread line (picker only), no Skip-Signal lines (outside TP). Picker `no_quote` is bid or ask ≤ 0 on the cached chain (no two-sided book). Premium min remains **$0.03**. Empty book: live hydrate, then `data_wait:chain_quotes` if the cache is not a just-live fetch.
>
> **Rollback:** traffic to `easy-etrade-strategy-00598-tr4`, or `SINGLE_TP_ADMIT=false` (legacy stack is still in the image). Replays (Oct 13, JSON missing policy): single-TP flag-on 10.5 8/19–10/13 = **216 / 167W/49L / +$96,369.82**, row-for-row identical to the confirmed book (`docs/0.ProfitabilityThresholds/10.13/`); Total 4,395 / −$158,470.91. Wide parity on 10/13 decision records: 257/257.


> **2026-10-13, first pinned in `easy-etrade-strategy-00599-lhd` (still the live admit/caps on `00611-9zz`).** Combined book, slot count and every path cap = **50**; `0DTE_MONITOR_MAX_SYMBOLS=50`. `0DTE_MAX_POSITION_SIZE_SUM=600` (since 10/9) binds every entry: 1/50 of 90% of the **$100,000** demo seed is $1,800. Book full is checked **first** (no API) in the TL / BO / ORB1 gate flows: reason `max_open_positions_reached` (open_n / cap), no chain pull, no dir%, no cooldown, capacity retry (§8.3). dir% missing data defers (90 s bounded wait, then the break is retired with no cooldown). Admit since 10/13 is `SINGLE_TP_ADMIT=true` / `TRADE_PROFILE_VERSION=wide`. (`QUALITY_GATE_ADMIT=wide_collect` + Trade Profile 10.5 observe-only was **10/9–10/13 only**.) FINAL Live Pilot (cap 7) is retired.

> **2026-09-28 (history; superseded by the 10/13 note above and the 10/9 $100k / $600 reseed).** **7** open positions. Sacred **10%** buffer, **90%** tradable, equal **1/7** slots. `0DTE_MAX_POSITION_SIZE_SUM=0` (no dollar sum cap). `LIVE_MAX_CONTRACTS_PER_TRADE=0`. Path caps and the combined book are **7**. Research demo seed **$5,000**.
>
> Decision data is classified before a gate compares a number (`easy0DTE/modules/decision_required.py`). **VALID** compares the number, and zero is valid. **NOT_READY** means the clock has not elapsed or the chain fetch was yielded; the same break is held and tried again. A Trendline data-readiness hold does not consume the 60-second capital reservation. **DATA_ERROR** means a required finite number was missing or non-finite; the break is rejected. **NON_APPLICABLE** is the other side, a disabled gate, or a research-only field.
>
> **Since 10/13 (first `00599-lhd`, still `00611-9zz`) the admit is the single Trade Profile TP Wide (`SINGLE_TP_ADMIT=true`; top note); the text below describes 10/9–10/13 and the flag-off rollback.** 10/9–10/13 the admit was the wide-collect `GATE_*` stack (`QUALITY_GATE_ADMIT=wide_collect`); the Trade Profile in [0.TradeProfiles.md](0.TradeProfiles.md) computes and records observe-only. Call SS and Put SS are on the stack, default off in code, set **hard** in `Shared.env` (`GATE_call_ss=hard` · `GATE_put_ss=hard`). Path SS is not on the stack. CALL 170,000 and PUT 11,000 are not the admit. Profit Capture stays PATH_V2.
>
> Snapshot and desk lists store keys in usefulness order: `call_chain_volume`, `put_chain_volume`, `representative_call_delta`, `representative_put_delta`, `price_volume_efficiency_3m`, `break_distance_pct`, then the rest of the replay rank. That order is for reading a row. Gate evaluation order is unchanged.

**Last Updated:** November 2, 2026 (local until `deploy now`: calendar enable/disable = `should_skip_trading` — previous **session** before a bank holiday or standalone low-volume day; listed Day Before … does not add a second skip, so 2026-11-24 and 2026-12-22 trade; Wide `equity_volume_ratio` ≥ **0.012** on ORB1 / Trendline / ORB2; Liquidity Guard `ask_size_missing` fail-closed if size is `None` — pick hint now keeps book sizes; empty two-sided chain hydrates like a stale PO stamp) · October 30, 2026 (calendar skip is previous **session**, not calendar day — Friday before a Monday holiday; [MarketCalendar.md](MarketCalendar.md)) · September 28, 2026 (7 open positions · 90% / 10% buffer · dollar sum cap off)  
**Subject:** 0DTE risk — Portfolio Protection · Red Day · **Position Sizing** (90% capital · equal slots) · **Liquidity / ADV Guard** · **Same-side 1h cooldown** (reject + post-exit) · **Trade Profile admit**. Covers **ORB1**, **Trendline (TL0–TL2+)**, and **ORB2 Breakout** (one slot pool).  
**Liquidity / ADV Guard SoT:** [LiquidityGuard.md](LiquidityGuard.md) — **0DTE only** (ORB · Trendline · Breakout). Slip Guard / SO ADV capping **retired**.  
**Not this file:** Path pipelines → [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTETrendline.md](0DTETrendline.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · post-fill exits → [0DTEProfitCapture.md](0DTEProfitCapture.md) · monitor/exit spine → [PositionMonitoring.md](PositionMonitoring.md) · ORB SO ETF sizing → [SORisk.md](SORisk.md) · ORB SO strategy → [ORBSO.md](ORBSO.md) · env catalog → [Configs.md](Configs.md).

### Position Sizing vs Liquidity / ADV Guard (desk lock)

| Layer | Role | Binds when |
|-------|------|------------|
| **Position Sizing Policy** (§5–§6) | How big **should** this trade be? Sacred **10%** buffer · trade **90%** of equity · **50** equal slots (live `easy-etrade-strategy-00611-9zz`; FINAL Live Pilot 7 retired) · **$600** dollar sum cap | Always — primary size for ORB / TL / BO. Each new open is min(1/N of tradable equity, $600); at $100k the $600 cap binds |
| **Liquidity / ADV Guard** (§11 · [LiquidityGuard.md](LiquidityGuard.md)) | How big **can** fill safely vs this contract’s market? Ask-depth qty cap **off** · ADV 2.0% · no contract-count cap | Only when slot-sized qty would exceed market caps (`winning_governor` ≠ `slot_request`) |

On a $5,000 book the buffer is $500 and each new open is about $643 (tradable $4,500 / 7). The 35% ceiling is $1,750 and does not bind.

### PP observe-only (current)

| Flag | RESEARCH | FINAL |
|------|----------|-------|
| `PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false` | `ORBSO.env` (+ deploy pin) | `Risk.env` |

Under observe-only: Gates **3a/3b** still evaluate + alert; **do not block fills**; **`should_arm_0dte_tier_policy_for_pp` returns false** (no Red Day tier arm from SO symbol gates).  
If PP volume coverage is insufficient: **defer** 0DTE execute and **keep the queue** for retry (do not seal `_dte0_batch_executed_pt_date`).

**Slots:** `0DTE_SLOT_COUNT` / `TOTAL_0DTE_MAX_POSITIONS` in `Account0DTE.env` — ORB + Trendline + Breakout.

---

### Documentation guide map

| Guide | Scope |
|-------|--------|
| **This file** | **0DTE risk & sizing** — Portfolio Protection · **Unified Account Risk (P4)** · Red Day · allocation · caps · Live entry clamps · **same-side 1h cooldown (reject + exit)** · **Skip Signals** |
| [LiquidityGuard.md](LiquidityGuard.md) | **SoT** — 0DTE Liquidity / ADV Guard only (SO Slip Guard retired) · ADV data plane · `underlying_adv_missing` runbook |
| [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md) | All-path PP operator desk · session replay · gate research · BAD DAY SO emergency |
| [0DTEORB1Breakout.md](0DTEORB1Breakout.md) | ORB1 Breakout pipeline — Convex, Hard Gate, execution @ 7:30 |
| [0DTEPriorityScore.md](0DTEPriorityScore.md) | **PS v2.1** formula · PRank · Gate 3c · future TL/BO PS |
| [0DTEQualityGates.md](0DTEQualityGates.md) | **QG** — live `GATE_*` wide collect · suggested QG 31 (not shipped) · cert **30 / 30** |
| [0DTESkipSignal.md](SkipSignal/0DTESkipSignal.md) | **Skip Signals** — absolute CALL/PUT + strategy/side 0-MFE (`SSORB1CALL` · `SSTL1PUT` · …) |
| [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md) | 0-MFE mine board per strategy × side |
| [0DTETrendline.md](0DTETrendline.md) | Trendline path · §11.6 shared gates |
| [0DTEProfitCapture.md](0DTEProfitCapture.md) | Post-fill premium stealth · ownership chain · Demo vs Live close path |
| [0DTEProfitCaptureImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/0DTEProfitCaptureImprovementGuide.md) | Profit capture research — `mfe_giveback` retention · session peak vs exit |
| [0DTEStopLossImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/0DTEStopLossImprovementGuide.md) | Stop loss tuning — MAE / drawdown from entry · conditional tail stops |
| [NoProgressTimeoutImprovementGuide.md](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/NoProgressTimeoutImprovementGuide.md) | No-progress timeout research — Jun 30 TSLA · Lane A/B spec |
| [ORB0DTEPositionSizingAudit.md](ORB0DTEPositionSizingAudit.md) | Engineering audit — flow tables · Live cap insertion point |
| [LiveModeReadiness.md](../LiveModeReadiness.md) | Live broker fill gates · ownership · forced-mismatch · cutover |
| [SORisk.md](SORisk.md) | ORB SO (ETF) sizing — DEMO sibling · credential matrix |
| [ORBSO.md](ORBSO.md) | ORB SO strategy path (Demo) |
| [Alerts.md](Alerts.md) | Telegram alert methods for PP / 0DTE exits |
| [MarketCalendar.md](MarketCalendar.md) | **SoT** — trading **enabled** unless `should_skip_trading` is `MARKET_CLOSED`, `LOW_VOLUME`, or `PRE_HOLIDAY`. Previous **session** only; no double Day Before |

---

## Table of contents

1. [Risk stack — layered model](#1-risk-stack--layered-model)
2. [Canonical pipelines](#2-canonical-pipelines)
   - [2.2 Quality Gate — profitable-trade settings](#22-quality-gate-profitable-trade-settings)
   - [2.2b Skip Signals](#22b-skip-signals)
3. [Portfolio Protection & Red Day](#3-portfolio-protection--red-day)
4. [Priority ranking (ORB1 Breakout)](#4-priority-ranking-orb-0dte)
5. [ORB1 Breakout position sizing](#5-orb-0dte-position-sizing)
6. [Trendline account management & sizing](#6-trendline-account-management--sizing)
7. [Daily limits & exposure caps](#7-daily-limits--exposure-caps)
   - [7.1 Same-side 1h cooldown (reject + post-exit)](#71-same-side-1h-cooldown-reject--post-exit)
8. [Unified Account Risk — account-scoped P4 hooks](#8-unified-account-risk--account-scoped-p4-hooks)
9. [Single-leg vs spread sizing](#9-single-leg-vs-spread-sizing)
10. [Maximum theoretical loss per position](#10-maximum-theoretical-loss-per-position)
11. [Liquidity / ADV Guard (Execution Risk Layer)](#11-liquidity--adv-guard-execution-risk-layer)
12. [Live vs Demo](#12-live-vs-demo)
13. [Live scaling phases](#13-live-scaling-phases)
14. [Configuration reference](#14-configuration-reference)
15. [Code & files index](#15-code--files-index)
16. [Operational verification](#16-operational-verification)
17. [Changelog](#17-changelog)

---

## 1. Risk stack — layered model

0DTE risk runs in **strict order** from signal to broker. Each layer answers a different question.

| # | Layer | Question | When | ORB1 Breakout | Trendline 0DTE |
|---|-------|----------|------|----------|----------------|
| 1 | **Eligibility** | Should this symbol trade at all? | Pre-rank | Convex · Hard Gate | Quality score · break gates · **Phase 2 follow-through** |
| **1b** | **Skip Signal** | Does PO state look like a dump (absolute CALL/PUT **or** this strategy/side)? | Same step as Quality Gate, before execution | `call_ss` · `put_ss` · `path_ss` | Same Shared keys; TL keyed by slot (TL0 / TL1 / TL2+) |
| 2 | **Portfolio Protection** | Is the **pool** stressed @ 7:30? | 7:30 pre-fill | Inherits `_red_day_filter_blocked` · **PS floor** (selection) | Inherits flag · intraday tier gate |
| 3 | **Priority / rank** | Who gets a **slot**? | Pre-sizing | **PS v2.1** → PS ≥ **0.30** → top-**10** | — |
| 4 | **Portfolio sizing** | How many **dollars** per slot? | Pre-strike | Sacred buffer · equal 1/N (shared) | Same allocator |
| 5 | **Strike / liquidity** | Which contract? | Pre-qty | Path selector (ORB vol **75**, δ band) → **shared execution floor** | Trendline pick + **shared execution floor** |
| 6 | **Quantity (Position Sizing)** | How many **contracts** from the slot? | Pre-risk | `slot_affordable(budget, ask×100)` — **primary** | Same formula |
| **6b** | **Unified Account Risk (P4)** | Account halt / caps / Red Day bridge? | Pre-broker @ `execute_lotto_sleeve` | **`daily_loss_halt`** · position caps · tier gate | Same manager |
| 7 | **Liquidity / ADV Guard** | Slot qty still market-executable? | Pre-broker | Shared validator + Execution Risk Layer — **clamp only if oversize** | Same choke point |
| **7b** | **Same-side 1h cooldown** | May we re-enter this **strategy / symbol / side**? | After **terminal exec reject** **or** any **filled exit** | Block same side **3600s** · opposite CALL/PUT stays open · TL keyed by **slot** (TL1≠TL2) | `path_exec_day_exclude` · `breakout_watch` · `orb1_0dte_watch` |
| 8 | **Profit capture** | When to exit? | Post-fill | **`asset_stop`** (**−0.5%** all 0DTE, before ARM) · **und runner** after +30% (`und_trail` / `und_take_profit` / prem BE) · BE/scratch · weak exits · data safety · **`eod_close`** · `mfe_giveback`/Hold config-off | Same engine; one Shared stop |

**Scope labels (do not conflate):**

| Label | When | Module | Blocks execution? |
|-------|------|--------|:-----------------:|
| **Portfolio Protection (PP)** | ~7:30 pre-fill | `portfolio_protection.py` · Gates **3a/3b only** | RESEARCH: observe-only by default · LIVE: tier flag + Convex |
| **Unified Account Risk (UAR / P4)** | Every 0DTE open | `unified_0dte_portfolio_risk_manager.py` | **Yes** when wired (`UNIFIED_0DTE_RISK_ENABLED=true`) |
| **Liquidity / ADV Guard** | Same open path, after UAR | `dte0_execution_risk_manager.py` | Clamps slot qty only when oversize vs vol/OI/depth/ADV · `LIVE_MAX_CONTRACTS=0` |
| **Skip Signal** | Same step as Quality Gate, before execution | `quality_gate_stack.py` (`call_ss` · `put_ss`) | **Yes** — a VALID absolute CALL/PUT pocket rejects. Path SS is off. A required **DATA_ERROR** rejects. **NOT_READY** holds the break |
| **Same-side 1h cooldown** | After reject **or** after filled exit | `path_exec_day_exclude.py` · `breakout_watch.py` · `orb1_0dte_watch.py` | **Yes** — blocks same strategy/symbol/side for `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS` (default **3600**); opposite side eligible |

`daily_loss_halt` is **UAR / P4**, **not** Portfolio Protection. PP sets `_red_day_filter_blocked` @ 7:30; UAR reads that flag **and** enforces account-scoped halts at broker submit.

**Jun 18 note:** Portfolio Protection blocked bad ORB1 Breakout entries, but DEMO Trendline **IWM** still hit **~−75%** post-fill — a **layer 8** gap (moderate δ **0.133**). Not fixable via PP or sizing keys — see [Stop loss guide §13](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/0DTEStopLossImprovementGuide.md#13-loser-exit-failure-modes--jun-18-iwm-case-study).

**Jun 30 note (layer 8 — weak exits):** ORB single-leg **`no_progress`** is **60 min** (`OPTION_SINGLE_NO_PROGRESS_MINUTES`). Mis-routed easy0DTE `single_leg` rows can fall back to generic keys — see [0DTEProfitCapture.md §15.6](0DTEProfitCapture.md#156-jun-30--no-progress-timeout--profile-routing-tsla-430c) · [NoProgressTimeoutImprovementGuide](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/NoProgressTimeoutImprovementGuide.md). This is **post-fill profit capture**, not a sizing/PP change.

**Jul 31 / Aug 6 / Aug 21 / Aug 27 note (layer 8 — price_stop + und runner):** 0DTE singles enable **`price_stop`** under/over **entry underlying** (CALL under / PUT over), **only before** premium **+30%** arms. **ORB + Trendline + Breakout:** **−0.80%** (`OPTION_SINGLE_STOP_PCT=0.008` in `Shared.env`). No BO path override. After +30%: **und runner** shared across paths (confirm und fav ≥**0.05%** × **3** ticks → trail **0.20%** und HWM · und TP **+1%** · prem BE floor). Detail: [0DTEProfitCapture.md §3 / §4.2](0DTEProfitCapture.md).

**Do not tune `OPTION_STEALTH_*` when changing sizing or Portfolio Protection keys.**

### Scope vs ORB SO

| Path | In this guide |
|------|----------------|
| **ORB1 Breakout** | Full |
| **Trendline 0DTE** | Full |
| **ORB SO (ETF)** | Referenced only where it **sets** `_red_day_filter_blocked` @ 7:30 |

**Combined options book (one shared slot pool):** `TOTAL_0DTE_MAX_POSITIONS=50` in `configs/Account0DTE.env` (live `easy-etrade-strategy-00611-9zz`, first pinned 10/13 in `00599-lhd`; FINAL Live Pilot 7 retired). It equals `0DTE_SLOT_COUNT`. ORB1, ORB2, and Trendline each also cap at **50**. Pending reservations count toward the path. ORB2 rejects with `breakout_cap_reached`. Do not set `MAX_OPEN_POSITIONS` or `UNIFIED_0DTE_*_MAX_POSITIONS`. `MAX_TOTAL_OPTION_POSITIONS` is a fallback alias of TOTAL that `Shared.env` still sets (**50**); keep it equal to TOTAL.

### 0DTE position sizing & caps (one place to edit)

Canonical source: **`configs/Account0DTE.env`**. Combined book is **one** count gate (`TOTAL_0DTE_MAX_POSITIONS=50`, live `easy-etrade-strategy-00611-9zz`, first pinned 10/13 in `00599-lhd`; FINAL Live Pilot 7 retired). `0DTE_SLOT_COUNT` must equal TOTAL so 1/N sizing matches available capital. Each path cap is also **50**.

| Cap | Value | Canonical key | File | Notes |
|-----|------:|---------------|------|-------|
| Equal-slot **sizing** divisor | **50** | `0DTE_SLOT_COUNT` | `Account0DTE.env` | Must equal TOTAL. If they differ, TOTAL wins. |
| Tradable capital % (→ 10% sacred buffer) | **90** | `0DTE_TRADING_CAPITAL_PCT` | `Account0DTE.env` | |
| Per-position pct ceiling | **35** | `0DTE_MAX_POSITION_SIZE_PCT` | `Account0DTE.env` | Looser than 1/N of the 90% pool; with the $600 sum cap the sum cap sets the size |
| Absolute USD / new open | **$600** | `0DTE_MAX_POSITION_SIZE_SUM` | `Account0DTE.env` | Eric 10/9 (live). `0` disables the dollar sum cap |
| Contracts / new open | **off** | `LIVE_MAX_CONTRACTS_PER_TRADE` | `Account0DTE.env` | `0` is no contract-count cap |
| **ORB1 Breakout** open positions | **50** | `ORB_0DTE_MAX_POSITIONS` | `Account0DTE.env` | Rolling watch |
| **ORB2 Breakout** open positions | **50** | `BREAKOUT_0DTE_MAX_POSITIONS` | `Account0DTE.env` | `breakout_cap_reached` |
| **Trendline 0DTE** open positions | **50** | `TRENDLINE_0DTE_MAX_POSITIONS` | `Account0DTE.env` | Path count gate |
| **Total 0DTE** (ORB1 + ORB2 + Trendline) | **50** | `TOTAL_0DTE_MAX_POSITIONS` | `Account0DTE.env` | One combined-book variable |
| Monitor symbol cap (post-fill) | **50** | `0DTE_MONITOR_MAX_SYMBOLS` | `Account0DTE.env` | Match combined book |

The **50** values are live in `easy-etrade-strategy-00611-9zz` (first pinned 10/13 in `00599-lhd`); FINAL Live Pilot = 7 is retired. Demo seed `0DTE_DEMO_STARTING_BALANCE=100000` (10/9 reseed).

Resolvers: `modules/unified_0dte_account.py` — `unified_0dte_slot_count()`, `orb_0dte_max_positions()`, `breakout_0dte_max_positions()`, `trendline_0dte_max_positions()`, `total_0dte_max_positions()`.

**Quote health / entry kill switch (ORB + Trendline):** `0DTE_DEGRADED_*` in `Account0DTE.env` (legacy `ORB_OPTIONS_MONITOR_DEGRADED_*` / `ORB_0DTE_DEGRADED_ENTRY_KILL_SWITCH_*`).  
**No-data force exit (all 0DTE singles):** `OPTION_MAX_DEGRADED_SECONDS=90` — not TL 180/300.

**`MAX_OPEN_POSITIONS` is retired.** It was a Prime/SO duplicate of `TOTAL_0DTE_MAX_POSITIONS`. Not in `Risk.env`. Prime leftover readers fall through to TOTAL.

**Liquidity / ADV Guard:** `LIQUIDITY_GUARD_ADV_LOOKBACK_DAYS` in `configs/Risk.env` · participation / vol / OI / depth in `Shared.env` / `Account0DTE.env` — `adv_data_manager.py` · [LiquidityGuard.md](LiquidityGuard.md). SO Slip Guard retired.

---

## 2. Canonical pipelines

### 2.1 ORB1 Breakout @ 7:30 PT — Signal Collection → Execution

**Full desk reference:** [0DTEORB1Breakout.md §8](0DTEORB1Breakout.md#8-orb-0dte--signal-pipeline) · Matrix baseline: [0DTEORB1Breakout.md § Current stack](0DTEORB1Breakout.md#current-stack-matrix-baseline).

| Step | Name | Module | Key settings |
|:----:|------|--------|--------------|
| 1–2 | ORB Capture + **Signal Collection** | `prime_orb_strategy_manager.py` | `SO_ENTRY_TIME=07:15` · `SO_CUTOFF_TIME=07:30` · `SO_BULLISH/INVERSE_THRESHOLD=0.001` · `CALL/PUT_RECHECK_*=1.000` · validation candle 7:00–7:15 |
| 3 | Hydrate + SO stamp + **session tech** | `orb_shared_730_pipeline.py` | `stamp_so_priority_for_0dte()` · `_hydrate_dte_signals_for_convex()` · `apply_session_tech_for_convex()` (`0DTE_CONVEX_SESSION_TECH`) |
| 3b | Chain skew enrich | `convex_chain_skew.py` | Live chain → `put_call_oi_ratio` (**observe**) |
| 4 | **Quality Gate E16** | `quality_gate_stack.py` · shim `convex_eligibility_filter.py` | dir% hard · pc observe · orb **≤8%** · vol **0.12** · MACD eps **99** + mild deadband ON · E10/E11/E7 · E14 off |
| 5 | **Hard Gate** | `prime_0dte_strategy_manager.py` | `0DTE_HARD_GATE_*` vol floors **0.012** (Nov 2, matches Wide; historical E16 **0.12**) · ET window 10:30–10:40 — **before** rank |
| 6 | **Priority Score (PS v2.1)** | `orb_priority_score.calculate_0dte_priority_score_v2` | `0DTE_PRIORITY_USE_PS_V2=true` — **[0DTEPriorityScore.md](0DTEPriorityScore.md)** · [0DTEORB PS](0DTEORB1Breakout.md#priority-score-ps-v21) |
| **7** | **PS floor + cap ★** | `dte0_execution_filters.py` | PS **≥ 0.30** · cap **10** · **always enforces** (not PP) |
| 8 | **PP 3a/3b** | `portfolio_protection_step5.py` | **Observe only** · `PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false` |
| 9 | **Sizing → Execution** | `prime_trading_system.py` | Rank mults · lotto-first · δ 0.18–0.42 · Execution Risk Layer |

**Active route:** `0DTE_ORB_LOTTO_FIRST=true` · spreads disabled (`0DTE_ORB_SPREAD_*=false`).

**Matrix baseline (Aug 6 E16 lock):** dir% hard · pc_oi observe · orb **0.25–8%** · vol **0.12** · MACD eps observe + mild soft-neg deadband · E10/E11/E7/chase/PUT-wide · **PS v2.1** · Gate 3c PS floor. E14 shallow is telemetry/off.

### 2.2 Quality Gate — live admit (after signal, before execute) {#22-quality-gate-profitable-trade-settings}

**Canonical three-path QG guide:** **[0DTEQualityGates.md](0DTEQualityGates.md)**. Technical keys / persist: **[Data.md](Data.md#5-chain-skew--dir--pc_oi)**. Spine: **[ProcessFlow.md](ProcessFlow.md)**.

The Quality Gate is the pre-execute **enter or reject** for every options path. After a trade signal and before the entry snapshot, the stack is `trade_profile`, then `call_ss`, then `put_ss`. The profile accepts or rejects. Call SS and Put SS default off. Catalog: [0.TradeProfiles.md](0.TradeProfiles.md).

**Live admit:**

| Check | What it does |
|------|----------------|
| **Trade Profile** | **Since 10/13 (`00611-9zz`, first pinned `00599-lhd`, `SINGLE_TP_ADMIT=true`) the one admit: TP Wide.** Flag off: hard when `QUALITY_GATE_ADMIT=trade_profile`; 10/9–10/13 (`wide_collect`) it was **observe-only**: computed, stamped and recorded, not the admit. |
| **Wide collect** | The admit since 10/9: dir% > 0.10 (same-side share, hard; missing data defers, 90 s, then retire with no cooldown) · volume floor · break_max 2.50 · contract band. |
| **Call SS / Put SS** | On the stack. Default off in code; **hard** in `Shared.env` since 10/9. |
| **Path SS** | Not on the stack. |
| **Chain-share dir% / put/call OI ratio** | Not on the stack. Those ideas live inside the profile keys above. |

The confirm hydrates this signal's bars, chain, and quote, then runs that stack. A pass continues to the entry snapshot and the order. A profile reject does not. The order path still last-looks the contract spread.

The table below is the older E16 collect. It is not the live admit.

**Foundation / historical expected levels (not live `GATE_*`):**

| Layer | Metric | ORB1 Breakout (**E16**) | Trendline 0DTE | Breakout 0DTE |
|------|--------|----------|----------------|----------------------------------------|
| **A — Geometry** | Direction | CALL above ORB high; PUT below ORB low | CALL through descending cap; PUT through ascending floor | CALL above ORB2 high; PUT below ORB2 low |
| **A — Break** | Minimum break | ORB break confirmed at 7:30; ORB range **0.25%–8.0%** | Close-beyond **0.08%** (`TRENDLINE_MIN_BREAK_DISTANCE_PCT=0.0008`; continuation **deleted**) | First print **0.08%** (`BREAKOUT_BREAK_MIN=0.08`; continuation **deleted**) |
| **A — Confirmation** | FT / continuation / chop | Path-specific continuation; no TL/BO 2-bar rule | **None** — geometry side + min break only | **None** — first print beyond box → QG; reclaim still invalidates |
| **B — Score** | Admit score | Convex **≥0.75** | Quality **≥55** | Quality **≥55** (score alone does **not** pass) |
| **B — Volume** | Equity `volume_ratio` | **Live Wide (Nov 2): ≥0.012 all paths.** Historical E16/Hard Gate table: ORB1 **≥0.12** | **≥0.012** hard (all TL slots; unchanged) | Historical A7 **≥0.40**. Live Wide Breakout cell is **≥0.012** |
| **C — Dir%** | Same-side chain volume share | Strictly **>0.54** (**hard**) | Strictly **>0.54** (**hard**) | Strictly **≥0.55** (**hard**) |
| **D — Contract** | Delta / OTM / liquidity | Delta **0.18–0.42** · OTM **≤5.5%** · premium **≥$0.03** · contract vol **≥50** · OI **≥100** · spread max **8%** | Same contract core; OTM **≤5.5%** | Same core + cheap-lotto · **OTM ≤3.5%** |
| **Rank / cap** | Pool selection | **PS v2.1** · PS **≥0.30** · cap **10** | Slot/cap only | Slot/cap + same-side cooldown |
| **B — VWAP** | Side / extension | Extension reject **±15%**; v2.5 side logic | **observe** (`GATE_TL_VWAP` only) | **observe** (`GATE_BO_VWAP`) |
| **B — RSI** | Side band / stamp | **`RSI_stamp` observe** (complete data; no hard band yet) | **`GATE_TL_RSI=observe`** CALL **≤80** · PUT **≥20** | Momentum floor **off** + **`RSI_stamp` observe** |
| **B — MACD** | Session histogram | E16: eps **99** (deep soft-neg observe) + mild deadband ON · **`MACD` observe** stamp | **`MACD` observe** (stamp until level locked) | **`MACD` observe** (`BREAKOUT_MACD_HOLD_GATE_ENABLED=false`) |
| **C — Chain skew** | `pc_oi` | **off** (`GATE_ORB_pc_oi=off`) | **not on TL stack** | **off** (`GATE_BO_pc_oi=off`) |
| **C — Bollinger (5)** | `bollinger_upper` · `middle` · `lower` · `width` · `position` (%B) | **observe** (stamp; levels TBD) | **observe** | **observe** |
| **C — Options tape** | `call_volume` · `put_volume` | **observe** (stamp; levels TBD) | **observe** | **observe** |

**ORB exceptions currently locked:** E10 protection-day CALL block; E11 protection CALL carve-out (break `>0.45%`, below VWAP, RSI `<54`, `pc_oi<1`, ORB range `≥0.65%`); E7 index CALL grind skip; compound chase skip (`volume_ratio<0.30` + `|VWAP|>6%` + adverse pc_oi/index); PUT wide-break skip `>2%`.

**Research stamps (Layer C after dir%):** RSI · MACD · 5× Bollinger · call/put volume stay **observe** until multi-session books define hard floors. Missing → soft-skip (never invent). Promote only from win/lose PO desks.

**Breakout:** A7∩A10-S hard first — vol **≥0.40** · break_max **≤2.50%** · BB mid · soft-break · \|δ\| **≤0.36** · IV **≤0.50** · ATR **≤0.15** · score **≥55** · dir% **≥0.55** · OTM **≤3.5%**. Then observe/off: VWAP · RSI_band · pc_oi · MACD stamps. Entry window 300s. Detail: [0DTEQualityGates.md §6](0DTEQualityGates.md#6-breakout-quality-gate) · `configs/Breakout0DTE.env`.

**Missing data policy:** never invent RSI `50`, MACD `0`, volume `1.0`, or Bollinger/`call_volume`/`put_volume`. Missing RSI/MACD/BB/tape soft-skips the corresponding stamp gate; missing VWAP/volume fails closed for TL/BO quality where those gates are hard.

### 2.2b Skip Signals {#22b-skip-signals}

**2026-09-24 PATH_V2 (local, not deployed):** Entry plus skip, after the chain already used for dir%, replay **+$2,703** on 9/9–9/23 versus **−$18,137** before the two new pockets. `GATE_entry_part=hard` · `GATE_call_ss=hard` · `GATE_put_ss=hard` · **`GATE_path_ss=off`.** Keys: [Configs.md](Configs.md#absolute-call--put-skip-signals--live-keys).

**Prior (same day, superseded):** CALL-SS-PC921-C / PUT-SS-PC921-C (PC918-safe + STRUCTURAL-4 / ABS-NOCLOCK-5). Keep as FWD control — do not re-enable omitted keys.

Skip Signals are an extra **pre-exec reject** on the Quality Gate stack. They run in the **same Layer C step as QG**, **after admit hydrate and the chain fetch**, **before trade execution**. Wide collect floors stay (dir% **>0.10** · vol). Suggested QG 31 stays a research stamp. PRUNED-8/8 hydrate: RSI, chain volume/OI, 1m/5m volume, relative volume, spread, ATM ask, theta, SPY change, dir% Δ5m, call Δ5m, break distance (price fraction), |VWAP|. See [Data.md](Data.md#5-chain-skew--dir--pc_oi).

**Reject if either is true:**

1. The **absolute CALL or PUT Skip Signal** matches this side, **or**
2. The **strategy/side Skip Signal** for this path and side matches (`SSORB1CALL`, `SSTL1PUT`, `TL2+SSCALL`, …) — **currently off**.

A missing required stamp is **DATA_ERROR** and does not admit. **NOT_READY** holds the break. Unknown Trendline slot does not by itself skip.

| Gate | ID | Side / path | Skip if (LIVE) | Env |
|------|----|-------------|----------------|-----|
| **Quality / Entry** | **ENTRY-2026-09-26** | CALL and PUT | CALL `call_chain_volume` **≥ 170000**. PUT `put_chain_volume` **≥ 11000**. Missing or non-finite rejects. Zero is compared to the floor. A yielded chain holds the break. Moon cannot clear a missing chain | `GATE_entry_part=hard` · `QG_CALL_ENTRY_CHAIN_MIN` · `QG_PUT_ENTRY_CHAIN_MIN` |
| **Absolute CALL** | **CALL-SS-2026-09-27** | All CALL | `break_distance_pct≤0.00086` | `GATE_call_ss=hard` · `QG_CALL_SS_BREAK_DIST_MAX=0.00086` |
| **Absolute PUT** | **R-PUT-2026-09-27** | All PUT | `call_chain_volume≥18800 ∧ break_distance_orb_ratio≤0.287` | `GATE_put_ss=hard` · `QG_PUT_SS_CALL_CHAIN_MIN=18800` · `QG_PUT_SS_BREAK_ORB_RATIO_MAX=0.287` |
| **Path / side** | **SSORB1CALL** · **SSTL1PUT** · … | That path × side | 9.21 knives stay in `_PATH_SS_KNIVES` | **`GATE_path_ss=off`** |

**Historical Absolute formulas (not live):** CALL-SS-1724 = `(sva ≤ 0.20 AND IV > 0.60) OR (dir% ≤ 0.40 AND IV > 0.50) OR (dir% ≤ 0.40 AND |vwap%| ≥ 0.80) OR (RSI ≥ 70 AND sva ≤ 0.20)`. PUT-SS-1724 = `(dir% ≤ 0.20 AND RSI ≤ 45) OR (RSI ≥ 70 AND vol < 5) OR (RSI ≤ 40 AND vol < 0.20) OR (IV > 0.70 AND vol < 0.20)`. PC918 / PC921-C pocket lists: [0DTESkipSignal.md §5.5–5.6](SkipSignal/0DTESkipSignal.md).

Stack order: dir% → `entry_part` → `call_ss` → `put_ss` → `path_ss`. Canonical write-up: [0DTESkipSignal.md §0](SkipSignal/0DTESkipSignal.md#0-live-skip-signal-stack). Mine board: [0MFESkipSignal.md](SkipSignal/0MFESkipSignal.md). Live keys: [Configs.md](Configs.md#absolute-call--put-skip-signals--live-keys). Admit hydrate + `watch_fsm` persist: [Data.md](Data.md).

### 2.3 Priority Score (PS v2.1) {#priority-score-next-improvement}

**Canonical formula & quality-term maps:** **[0DTEPriorityScore.md](0DTEPriorityScore.md)**.

**Operational order (watch default):** Structure → Quality Gate (hydrate + live `GATE_*` enter/skip) → PO Entry Snapshot (executed only) → Enter Trade → UAR / Liquidity Guard. **Batch rollback only:** Signal Collection → Quality Gate → Hard Gate → **PS v2.1 rank** → Gate 3c (floor + cap) → Execution.

| Role | Spec |
|------|------|
| **Purpose** | Order the 10-slot queue toward early-continuation winners; drop junk below floor |
| **Default** | `0DTE_PRIORITY_USE_PS_V2=true` · `calculate_0dte_priority_score_v2()` |
| **Formula** | dir% **26%** · RSI **24%** · MACD **20%** · VWAP **12%** · vol **10%** · ORB **8%** + conf nudge + penalties/floors |
| **Gate 3c floor** | **`0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION=0.30`** — drop PS &lt; 0.30 · then cap **10** |
| **Floor lock** | Aug 6 PS v2.1 desk — skips SBUX/RNG/ASTS/KTOS/RKLB/CSCO junk; keeps lowest wins (≥0.348). Do **not** raise past **0.34** (CRWV edge) |
| **Full write-up** | [0DTEORB1Breakout.md — Priority Score](0DTEORB1Breakout.md#priority-score-ps-v21) · [§4](#4-priority-ranking-orb-0dte) |

```text
base = 0.26·dir_q + 0.24·rsi_q + 0.20·macd_q + 0.12·vwap_q + 0.10·vol_q + 0.08·orb_q
priority_score = clamp01(base × conf_nudge − penalties + floors)
Gate 3c: reject priority_score < 0.30 → keep top 10
```

**Residual research:** high-PS fades (SLV/DAL) still leak past **0.30** — QG/deadband, not a higher floor. Tools: `priority_optimizer/tools/replay_ps_v2_multiday.py` · `scripts/star_stack_matrix_sweep.py`.

### 2.4 Trendline 0DTE (intraday)

| Step | Name | Risk relevance |
|:----:|------|----------------|
| 1–2 | ORB Capture · Trendline Build | Geometry @ 7:30 — no trade (Phase 1 mission contest + **7:30 bar merge preservation**) |
| 3 | Priority Gate | **`apply_priority_emit_gates()`** (geometry side + `TRENDLINE_MIN_BREAK_DISTANCE_PCT` only). Continuation / FT / chop **deleted**. Quality score ≥ **55** is scaffolding/logs. |
| **4** | **Portfolio Protection (Red Day)** | Tier 1 CALL + all PUT when `_red_day_filter_blocked` |
| 5 | Sizing / caps | Sacred buffer · 1/N · combined book |
| 6 | Execution | 7:30–**12:30 PT** · Execution Risk Layer · `execute_lotto_sleeve()` |
| 7–8 | Monitor · Profit Capture / EOD | Premium stealth |

```text
ORB1 Breakout:   … → Convex → Hard Gate → Rank → PS floor + cap → Sizing → qty
            → Red Day tier gate (pre-execute) → Execution Risk Layer → broker → stealth

Trendline:  … → intraday break → quality score → Phase 2 emit gates → Red Day tier gate (when blocked) → slot/buffer/caps
            → Execution Risk Layer → broker → stealth
```

Detail: [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTETrendline.md §11](0DTETrendline.md#11-account-management)

---

## 3. Portfolio Protection & Red Day

Portfolio Protection runs via **`ensure_pp_ready_before_execution()`** immediately **before** 0DTE execution: Gates **3a/3b** on post-Convex **`_pending_dte0_signals`** (`_build_0dte_execution_protection_pool_enriched()` · `path=0dte_execution_queue`). **FINAL and RESEARCH apps use the same pool and evaluation**. **Observe-only** (`PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false` — RESEARCH `ORBSO.env` / FINAL `Risk.env`): evaluates + **OBSERVE ONLY** alert; does not block fills or arm tier policy.

### 3.1 Quick reference — what changes on Red Day

Single flag **`_red_day_filter_blocked`** is set when Gate **3a** fires (Patterns 1–3 or Protection Day A/B).

| When blocked | ORB SO | ORB1 Breakout | Trendline 0DTE |
|--------------|--------|----------|----------------|
| **SO batch @ 3a** | ❌ **0 fills** | Flag only — batch **not** cleared | Flag only |
| **PS floor + cap ★** | SO trim (zero-PS) | ✅ 0DTE PS floor **≥ 0.30** ★ + cap **10** (selection — **not** PP) | ❌ |
| **Convex / Hard Gate** | N/A | ✅ Convex v2.5 + Hard Gate volume/spread | ❌ |
| **Red Day tier execution gate** | N/A | ✅ pre-execute | ✅ intraday emit |
| **BAD DAY emergency** | ✅ post-fill SO | ❌ | ❌ |

**Tier policy (0DTE + Trendline when blocked):** Tier 1 **CALL** ✅ · Tier 2+ **CALL** ❌ · all **PUT** ✅.

**Tier 1 symbols (`data/watchlist/0dte_list.csv`, column `tier=1`):** SPY, QQQ, MAGS, IWM, GLD, SLV, IBIT. IWM replaces cash-index RUT. SPX and RUT are not tier 1. A RUT CALL on a red day is blocked. Type labels are descriptive; gating uses **`tier`** only.

### 3.2 Step naming

| Term | Meaning |
|------|---------|
| **ORB1 Breakout Step 7** | Zero-PS + cap **`ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX`** (**10**) in `prime_0dte_strategy_manager` — runs **after** Hard Gate + rank |
| **Red Day tier execution gate** | Pre-execute CALL/PUT filter — ORB @ 7:30 execute · Trendline on each emit |
| Legacy **`STEP 5 TRADE EXECUTION`** | Options execute block in logs — **not** pipeline Step 5 (Priority Ranking) |

### 3.3 Gate layers (3a–3f) — 0DTE applicability

Evaluated on the **0DTE execution queue** @ Step 5 via `portfolio_protection_step5.py` (`ensure_pp_ready_before_execution`). **PP is Gates 3a/3b only.** Priority-score floor + cap runs in `prime_0dte_strategy_manager` / `dte0_execution_filters` before the execution queue is finalized — do **not** call that step “PP Gate 3c”.

**Cross-instance guard (Jun 26):** PP result is persisted to GCS (`portfolio_protection_step5/{date}.json`) and hydrated before 0DTE execution (`ensure_pp_ready_before_execution`). Mandatory audit: `PORTFOLIO_PROTECTION | audit`.

| Layer | Name (operator) | ORB1 Breakout | Trendline |
|:-----:|-----------------|:--------:|:---------:|
| **3a** | Unified batch — **Portfolio Protection** | Sets `_red_day_filter_blocked` | Inherits flag |
| **3b** | Composite risk — **Risk Gate** | Inherits flag | Inherits flag |
| — | **PS floor + cap** (selection) | **Symbol skip + cap 10** (symmetric CALL+PUT; survivors of Hard Gate) | — |
| **3d** | Signal Red Day — **Red Day Filter** | — (SO only) | — |
| **3e** | Winner profile filter | — (SO only) | — |
| **3f** | Adaptive expense filter | — (SO only) | — |
| — | **Red Day tier execution gate** | **Pre-execute** | **Pre-execute** |

### 3.4 Gate 3a — Red Day Patterns 1–3

Evaluated first in `modules/portfolio_protection.py` → `evaluate_portfolio_protection()`.

| Step | Trigger ID | Fire when |
|:----:|------------|-----------|
| 1 | `red_day_pattern_1` | ≥**70%** RSI &lt;40 **and** ≥**80%** weak volume |
| 2 | `red_day_pattern_2` | ≥**80%** RSI &gt;80 **and** ≥**80%** weak volume |
| 3 | `red_day_pattern_3` | ≥**80%** weak volume **and** ≥**50%** low or high RSI breadth |

**Example (Jun 8, 2026):** Pattern **3** fired (100% weak volume) → `_red_day_filter_blocked=True` → ORB SO **0 fills** · ORB1 Breakout **GOOGL PUT** allowed · Trendline prod **7** ungated entries vs local gate **2** (**RUT**, **QQQ**).

### 3.5a Volume enrichment guard (Jun 26)

Before `evaluate_portfolio_protection()`:

1. Build pool via `_build_0dte_protection_pool_enriched()`
2. Second-pass `force_enrich_protection_pool()` (E*TRADE backfill + lightweight red-day enrich)
3. **`protection_pool_enrichment_sufficient()`** — require ≥**80%** of rows with real `volume_ratio` (`PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT`)
4. If insufficient → **defer** Gates **3a/3b** (`reason=volume_coverage_insufficient`) — **no evaluate on sparse data**

**Volume metrics:** `raw_volume_ratio()` returns `None` when missing — **never** defaults to **1.0**. Median `volume_ratio` and weak-volume breadth use only populated values. This prevents false pass when enrichment fails (Jun 26 LIVE divergence).

### 3.5 Gate 3a — Protection Day (dual trigger)

Evaluated **after** Patterns 1–3. **Either Trigger A or B** blocks.

#### Trigger A — below-VWAP breadth (Rev 00369 — shipped)

| Condition | Default (`configs/Risk.env` §B) |
|-----------|-------------------------------|
| Below-VWAP breadth | `PORTFOLIO_PROTECTION_VWAP_BELOW_PCT` = **50** (% of pool with `vwap_distance_pct` &lt; 0) |
| Weak median volume | `PORTFOLIO_PROTECTION_MEDIAN_VOLUME_MAX` = **0.45** |

**Trigger id:** `protection_day`

#### Trigger B — split-tape / chop (Rev 00375 — local)

| Condition | Default (`configs/Risk.env` §B) |
|-----------|-------------------------------|
| Median VWAP distance | `PORTFOLIO_PROTECTION_MEDIAN_VWAP_MIN_PCT` = **2.0** — block when median `vwap_distance_pct` **&lt; 2%** |
| Weak median volume | same `PORTFOLIO_PROTECTION_MEDIAN_VOLUME_MAX` = **0.45** |
| VWAP coverage | `PORTFOLIO_PROTECTION_VWAP_COVERAGE_MIN_PCT` = **80** |
| Master switch | `ENABLE_PORTFOLIO_PROTECTION_SPLIT_TAPE=true` |

**Trigger id:** `protection_day_split_tape`

**Master switches:** `ENABLE_PORTFOLIO_PROTECTION=true` · `ENABLE_PORTFOLIO_PROTECTION_DAY=true`

**Operator log strings:**

```text
Portfolio Protection — Protection Day: {pct}% of pool below VWAP and median volume_ratio {med} < {max}
Portfolio Protection — Protection Day Split Tape: median VWAP distance {med_vwap}% < {min}% and median volume_ratio {med} < {max}
```

**Session replay (ORB SO desk — informs 0DTE flag):**

| Date | Type | % below VWAP | Med vol | Gate | SO P&amp;L |
|------|------|-------------:|--------:|:----:|----------:|
| May 18 | BAD DAY | **70.6%** | **0.273** | BLOCK (A) | −$14.62 |
| May 27 | BAD DAY | **66.7%** | **0.310** | BLOCK (A) | −$2.70 |
| May 26 | Chop | 48.0% | **0.315** | BLOCK (B) | −$4.59 |
| Jun 1 | Momentum | 33.3% | 0.412 | pass | +$64.02 |

Full replay: [PortfolioProtectionGuide.md § Gate replay](PortfolioProtectionGuide.md#gate-replay-key-sessions)

### 3.6 Gate 3b — Composite risk

Runs only when 3a passed. **`_fetch_spy_vix_for_portfolio_protection()`** (Rev Jun 12) → SPY momentum + VIX + weighted technicals → `composite_risk_score`.

| Recommendation | Effect |
|----------------|--------|
| **`SKIP_EXECUTION`** (score **≥ 0.75**) | Same as 3a for SO; sets `_red_day_filter_blocked` for 0DTE paths |
| **`REDUCE_POSITION_SIZE`** (Jun 12) | Sets **`_execution_position_size_multiplier`** (typically **0.5**) · scales **`0DTE_MAX_POSITION_SIZE_PCT`** at execution — does **not** block batch |

**Grep:** `Portfolio Protection Gate — composite risk` · `EXECUTION_MARKET_CONTEXT` · `enhanced_red_day_skip_execution` · `0DTE_EXEC_SIZE_MULT` · `MEDIUM RISK - Reducing 0DTE position sizes`

### 3.7 PS floor + cap (ORB1 Breakout) — not Portfolio Protection

Skip symbols below the PS floor before execution. **E14 shallow ★** is **Convex layer 4** — not here. **Always enforces** regardless of PP observe-only.

| Config | Default | Function |
|--------|---------|----------|
| `0DTE_SKIP_ZERO_PRIORITY_SCORE` | `true` | — |
| `0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION` | **`0.30`** | `dte0_execution_filters.filter_dte0_signals_below_min_priority_score()` |
| `0DTE_SHALLOW_E14_ENABLED` | **`false`** | E14 admit OFF (telemetry only) — `e14_shallow_gates_pass()` |

**E14 shallow (telemetry/off):** When enabled, CALL reject when `vwap_distance_pct < 0` and `macd_histogram < 2.0`; PUT reject when `vwap > 0` and `macd > −2.0`. **Aug 5 lock:** leave **`false`** — conflicts with Jun 26 E11 below-VWAP CALL winners. Session fields use RTH 1m when `0DTE_CONVEX_SESSION_TECH=true`. Grep: `0DTE_SESSION_TECH`.

Module: `prime_0dte_strategy_manager.py` → `_apply_dte0_priority_execution_filters()` — PS floor → **re-rank** → cap **`ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX`** (**10**).

**Greps:** `0DTE_PS_FLOOR` · `0DTE_PIPELINE | stage=dte0_execution_filters`

### 3.8 Red Day tier execution gate

Runs **after** sizing/qty intent, **before** broker submit.

#### ORB1 Breakout

`prime_trading_system.py` trims pending queue before `_execute_0dte_options_trades()`:

- **Tier 1 symbol:** keep CALL + PUT  
- **Non-Tier-1:** keep **PUT/SHORT only** · drop Tier 2+ CALL  

**Grep:** `RED_DAY_GATE | action=tier1_override_keep` · `Red Day 0DTE LONG/CALL gate: dropped`

#### Trendline 0DTE (Jun 8 local)

`_run_trendline_watch_cycle` → `passes_red_day_0dte_style_gate()` in `modules/portfolio_protection.py`:

| Side | Tier 1 | Tier 2+ |
|------|--------|---------|
| **CALL** | ✅ allowed | ❌ blocked · silent rearm |
| **PUT** | ✅ allowed | ✅ allowed |

**Config (`configs/Trendline0DTE.env` §6):**

```bash
TRENDLINE_RED_DAY_GATE_ENABLED=true
TRENDLINE_RED_DAY_ALLOW_TIER1_CALL=true
TRENDLINE_RED_DAY_ALLOW_PUT=true
```

**Greps:** `TRENDLINE_RED_DAY_GATE | action=drop` · `red_day_tier2_call_blocked`

### 3.9 Convex Red Day collateral (ORB1 Breakout)

When `portfolio_red_day=True` after 3a block:

- **Red Day refresh:** `0DTE_CONVEX_RED_DAY_REFRESH` re-runs the 7:30 listen pipeline when Gate 3a confirms after first Convex pass.
- **pc_oi mode (default):** Convex v2.5 uses side-aware `put_call_oi_ratio` + ORB range/break + trend regime + symmetric side gates + score **≥ 0.75** — no Red Day score floor; tier execution gate still applies at pre-execute
- Optional Hard Gate refresh: `_refresh_0dte_pipeline_for_portfolio_red_day()`

**Grep:** `0DTE_CONVEX_RED_DAY_PUT` · `0DTE_CONVEX_PC_OI`

### 3.10 On any Gate 3a/3b block — system actions

- **ORB SO:** `so_signals_ranked = []` → no SO fills  
- **All paths:** `_red_day_filter_blocked = True`  
- **ORB1 Breakout:** tier gate + Convex PUT floor  
- **Trendline:** tier gate on each emit  
- Telegram: `send_portfolio_protection_alert` ([Alerts.md](Alerts.md) §4)
- GCS: `portfolio_protection_step5/{date}.json` daily marker (+ legacy `blocked_portfolio_protection` when persisted)  
- Optional: force-close open SO positions  

**Data fail-safe:** Block suppressed when `avg_rsi == 0`, `avg_volume == 0`, or **`pct_volume_coverage` &lt; 80%**. Evaluate is **deferred** (not falsely passed) when enrichment coverage is below `PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT`.

### 3.11 Execution policy summary

| Path | CALL (LONG) | PUT (SHORT) | Other |
|------|-------------|-------------|-------|
| **ORB1 Breakout** | Tier 2+ ❌ · Tier 1 ✅ | All tiers ✅ | Convex PUT floor **0.62** |
| **Trendline** | Tier 2+ ❌ · Tier 1 ✅ | All tiers ✅ | No Convex / zero-PS |
| **ORB SO** | Entire batch ❌ | N/A | BAD DAY emergency post-fill |

**Not wired for 0DTE:** SO-only **BAD DAY emergency** (~15 min health check) — 0DTE uses premium stealth + EOD flatten. See [PortfolioProtectionGuide.md § BAD DAY](PortfolioProtectionGuide.md#operator-alert-bad-day-emergency).

### 3.12 Portfolio Protection config (SO-owned, 0DTE inherits)

| Key | Default | File | Role |
|-----|---------|------|------|
| `ENABLE_PORTFOLIO_PROTECTION` | true | `Risk.env` §B | Master switch |
| `ENABLE_PORTFOLIO_PROTECTION_DAY` | true | `Risk.env` §B | Protection Day triggers |
| `PORTFOLIO_PROTECTION_VWAP_BELOW_PCT` | 50 | `Risk.env` §B | Trigger A breadth |
| `PORTFOLIO_PROTECTION_MEDIAN_VOLUME_MAX` | 0.45 | `Risk.env` §B | Trigger A + B med vol |
| `ENABLE_PORTFOLIO_PROTECTION_SPLIT_TAPE` | true | `Risk.env` §B | Trigger B master |
| `PORTFOLIO_PROTECTION_MEDIAN_VWAP_MIN_PCT` | 2.0 | `Risk.env` §B | Trigger B med VWAP floor |
| `PORTFOLIO_PROTECTION_VWAP_COVERAGE_MIN_PCT` | 80 | `Risk.env` §B | Trigger B VWAP coverage |
| `PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT` | 80 | `Risk.env` §B | Min % pool with real `volume_ratio` before evaluate |
| `0DTE_SKIP_ZERO_PRIORITY_SCORE` | true | `ORB0DTE.env` | PS floor |
| `0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION` | **0.30** | `ORB0DTE.env` | PS floor (★ stack live) |
| `0DTE_SHALLOW_E14_ENABLED` | **false** | `ORB0DTE.env` | E14 admit OFF (telemetry); do not re-enable without matrix sign-off |
| `0DTE_CONVEX_SESSION_TECH` | true | `ORB0DTE.env` | Session RTH 1m VWAP/MACD for Convex L3/L4 + E14 |
| `0DTE_CONVEX_ORB_RANGE_MIN` | 0.25 | `ORB0DTE.env` | Convex ORB width floor |
| `0DTE_CONVEX_REGIME_ORB_MULT` | **1.0** | `ORB0DTE.env` | Trend regime ORB multiplier (symmetric) |
| `0DTE_CONVEX_MIN_SCORE` | 0.75 | `ORB0DTE.env` | Convex eligibility score floor |
| `CALL_RECHECK_BREAKOUT_MULTIPLIER` | 1.000 | `ORBPipeline.env` / `ORBSO.env` | 7:30 CALL + SO LONG recheck vs ORB high |
| `PUT_RECHECK_BREAKDOWN_MULTIPLIER` | 1.000 | `0dte.env` | 7:30 PUT recheck vs ORB low |
| `TRENDLINE_RED_DAY_GATE_*` | see §3.8 | `Trendline0DTE.env` | Trendline tier gate |

### 3.13 Portfolio Protection greps

```bash
grep "PORTFOLIO PROTECTION CHECK"
grep "PORTFOLIO_PROTECTION | audit"
grep volume_coverage_insufficient
grep protection_day
grep protection_day_split_tape
grep PORTFOLIO_PROTECTION_ZERO_PS
grep "0DTE_PIPELINE stage=portfolio_protection_zero_ps_cap"
grep RED_DAY_GATE
grep 0DTE_CONVEX_RED_DAY_PUT
grep TRENDLINE_RED_DAY_GATE
grep red_day_tier2_call_blocked
grep action=block_0dte_execution
```

**Tests:** `tests/test_live_portfolio_protection_gates.py` · `tests/test_0dte_zero_ps_gate.py` · `tests/test_0dte_shallow_e14_gate.py` · `tests/test_trendline_red_day_gate.py`

---

## 4. Priority ranking (ORB1 Breakout)

Ranking assigns **`priority_rank`** so the top-K names receive equal 1/N slots. Rank **multipliers are off by default** (`0DTE_RANK_SIZING_ENABLED=false`). Runs **after Hard Gate**, before Gate **3c** (`listen_to_orb_signals` → `_apply_hard_gate_before_rank()` → `_rank_signals_by_priority()` → PS floor + cap).

**Full pipeline:** [§2.1 Signal Collection → Execution](#21-orb-0dte--730-pt--signal-collection--execution) · [0DTEORB §8](0DTEORB1Breakout.md#8-orb-0dte--signal-pipeline) · [PS section](0DTEORB1Breakout.md#priority-score-ps-v21).

**Next research:** Improve PS so all matrix **wins ≥ 0.30** and junk **losses < 0.30** — [§2.3](#priority-score-next-improvement). Do not raise live floor past **0.34** without matrix sign-off.

### Production — PS v2.1 (CALL + PUT)

| Item | Detail |
|------|--------|
| Default | `0DTE_PRIORITY_USE_PS_V2=true` (`ORB0DTE.env`) |
| Module | `orb_priority_score.calculate_0dte_priority_score_v2()` via `_rank_signals_by_priority()` |
| Formula | dir% **26%** · RSI **24%** · MACD **20%** · VWAP **12%** · vol **10%** · ORB **8%** + conf nudge + penalties/floors |
| Floor (Gate 3c) | Drop **PS &lt; 0.30** · then cap **10** — `0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION=0.30` |
| Log | `0DTE_PS_V2` · `0DTE_PRIORITY_PS_V2` · `0DTE_PS_FLOOR` |

```text
base = 0.26·dir_q + 0.24·rsi_q + 0.20·macd_q
     + 0.12·vwap_q + 0.10·vol_q + 0.08·orb_q
base *= 0.94 + 0.06·clamp01((conf − 0.70) / 0.20)
# penalties/floors (SBUX mid-RSI · wide+hot · Aug 5 soft-neg · GLD/IBIT floor · CRCL hot+vol · PUT fade)
priority_score = clamp01(base)
# Gate 3c: drop PS < 0.30 → re-rank → keep top 10
```

### Fallbacks (off by default when PS_V2 on)

| Flag | Ranker |
|------|--------|
| `0DTE_PRIORITY_USE_PS_V2=false` · `0DTE_PRIORITY_USE_SO_RANK=true` | SO-mirror CALL / reversed SO PUT |
| both off | Legacy Rev **00352** blend |

Detail: [0DTEORB1Breakout.md — Priority Score](0DTEORB1Breakout.md#priority-score-ps-v21) · [§11 fallbacks](0DTEORB1Breakout.md#11-priority-ranking-at-730)

---

## 5. ORB1 Breakout position sizing

**Module:** `easy0DTE/modules/prime_0dte_strategy_manager.py` → `calculate_position_sizing()`  
**Allocator:** `easyTrendline/trendline_capital_allocation.py` → `compute_0dte_capital_snapshot()` (shared with Trendline)  
**Quantity:** `modules/prime_trading_system.py` → `_execute_0dte_options_trades()`

### 5.1 Two-layer model

1. **Dollars first** — equal 1/N of tradable equity (same sacred-buffer math as Trendline)  
2. **Contracts second** — `quantity = max(1, floor(capital_allocated / (ask × 100)))` for single-leg (`contract_executable_entry_premium` / `_orb_executable_entry_premium_per_share`); debit spreads use **`long ask − short bid`** per spread from chain manager

No env **`MAX_CONTRACTS`** — cheaper premium → more contracts within dollar slot (Jul 27 prefers near-ATM / higher δ among passers, so qty is usually lower than legacy lowest-δ picks).

### 5.2 Balance source

| Mode | Source | Persistence |
|------|--------|-------------|
| Demo | `mock_options_executor.account_balance` (default **$5,000**, GCS-persisted) | `demo_account/mock_options_history.json` — rolling **400** closed trades (`MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES`) |
| Live | `cash_available_for_investment` → `account_value` → fallback **$5,000** | Broker + LIVE sibling `live_account/live_options_history.json` |

**Live account:** `ETRADE_0DTE_ACCOUNT_ID=<see PrivateSecrets.md>` (ORB + Trendline shared).

Demo ledger retention prunes **closed trades only**; balance, weekly/daily stats, and open positions are preserved. ORB fills attach `metadata.execution_payload` for execution-quality forensics — grep `EXECUTION_FILL_SUMMARY`.

### 5.3 Allocation algorithm (equal slots — default)

Rank still selects **which** names get slots (top-K ≤ `ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX`). Dollars are **equal** across selected names unless optional rank sizing is re-enabled.

```text
equity_basis        = cash_balance + open_entry_costs
buffer_reserve      = max(session_floor, equity × (100 − 0DTE_TRADING_CAPITAL_PCT)%)   # default 10%
tradable_equity     = equity − buffer
available_cash      = max(0, cash − buffer)
slot_capital        = tradable_equity / 0DTE_SLOT_COUNT                                 # N=50 (live 00611-9zz; first pinned 10/13 in 00599-lhd; FINAL Live Pilot 7 retired)
max_single_position = min(balance × 35%, 0DTE_MAX_POSITION_SIZE_SUM=600)   # $600 binds at $100k
capital_allocated   = min(slot_capital, available_cash, max_single_position)
quantity            = max(1, floor(capital_allocated / (mid × 100)))
```

**Grep:** `0DTE_SLOT_SIZING`

**Example ($5,000 empty book):** buffer **$500** · tradable **$4,500** · slot **$643**. No dollar sum cap. Seven full slots use the 90% pool.

### 5.4 Optional rank multipliers (off by default)

Set `0DTE_RANK_SIZING_ENABLED=true` to restore greedy rank overlays, then normalize into available cash.

| Rank | Mult | Config |
|------|------|--------|
| 1 | 3.0× | `0DTE_RANK_MULT_1` |
| 2 | 2.5× | `0DTE_RANK_MULT_2` |
| 3 | 2.0× | `0DTE_RANK_MULT_3` |
| 4–5 | 1.71× | `0DTE_RANK_MULT_4_5` |
| 6–10 | 1.5× | `0DTE_RANK_MULT_6_10` |
| 11–15 | 1.2× | `0DTE_RANK_MULT_11_15` |
| 16+ | 1.0× | `0DTE_RANK_MULT_16_PLUS` |

### 5.5 Worst-case exposure

**50 slots (local; live 7) · `0DTE_MAX_POSITION_SIZE_SUM=600` · `LIVE_MAX_CONTRACTS_PER_TRADE=0`:** each new open is min(1/N of tradable equity, $600). On the $100,000 book 1/50 is $1,800 (1/7 is $12,857), so **$600** sets the size. Worst case with the book full: 50 × $600 = **$30,000** premium deployed. The 35% ceiling does not set the size.

### 5.6 Entry δ (sizing interaction)

| Policy | Config |
|--------|--------|
| δ band **0.18–0.42** | `0DTE_ENTRY_DELTA_*` (Jul 27) |
| Prefer **near-ATM / higher δ** | `0DTE_ENTRY_PREFER_LOWEST_DELTA=false` |
| Premium floor **$0.03** | `0DTE_MIN_ENTRY_PREMIUM` |
| OTM prefer / hard | ≤**12%** / ≤**15%** (`0DTE_ENTRY_OTM_PCT_*`) — TL + BO |

Liquidity gates reject strikes; they do **not** resize quantity. Shared pick/walk: `unified_contract_select.py` (ORB + TL + BO).

**Shared execution safety (Jul 2026; premium Jul 27):** `easy0DTE/modules/option_entry_liquidity_validator.py` is the **final** handoff gate for all 0DTE paths — after path-specific selectors, before broker submit. Spread caps remain path-owned (**ORB 8%** · **Trendline 30%** · Breakout per path).

| Key | Default | Role |
|-----|---------|------|
| `0DTE_ENTRY_MIN_VOLUME` | **50** | Min contract volume @ execution |
| `0DTE_ENTRY_MIN_OPEN_INTEREST` | **100** | Min OI @ execution |
| `0DTE_MIN_ENTRY_PREMIUM` | **$0.03** | Shared min per-share premium |
| `0DTE_MAX_ENTRY_PREMIUM` | **$2.50** | Shared max per-share premium |
| `0DTE_LIVE_MAX_QUOTE_AGE_SECONDS` | **10** | Pre-execution quote staleness (LIVE + true-to-live Demo) |

**Selector vs execution (ORB):** `0DTE_SINGLE_LEG_MIN_VOLUME=75` is **stricter** than the shared floor. Volume **50–74** may pass the shared floor but still **fail ORB selection** — acceptable. Trendline uses the shared floor at pick + pre-execution.

**Canonical reject codes (Telegram / ledger):** `option_low_volume` · `option_low_open_interest` · `option_spread_too_wide` · `option_premium_too_low` · `option_quote_stale` · `option_too_expensive_for_budget` · `option_live_chain_required`. Cold start: `0DTE_EXECUTION_POLICY_AUDIT | entry_min_volume=… | …`.

### 5.7 Gate 3b position-size multiplier (Rev Jun 12)

When Gate **3b** returns **`REDUCE_POSITION_SIZE`**, runtime sets **`_execution_position_size_multiplier`** from `risk_assessment.position_size_multiplier`. **`_execute_0dte_options_trades()`** multiplies **`0DTE_MAX_POSITION_SIZE_PCT`** before `calculate_position_sizing()`.

**Grep:** `0DTE_EXEC_SIZE_MULT | enhanced_red_day_mult=`

**Jun 11 desk:** Gate **3b** fired **`REDUCE_POSITION_SIZE`** (50%) on grind morning — composite risk did **not** skip execution; sole queue name **WULF** failed on chain/premium selector instead.

---

## 6. Trendline account management & sizing

**Modules:** `easyTrendline/trendline_capital_allocation.py` (`compute_0dte_capital_snapshot`) · `modules/unified_0dte_account.py` (`compute_unified_0dte_capital_snapshot`) · `trendline_options_executor.py` · unified ledger via `MockOptionsExecutor` / `configs/Account0DTE.env`

Same **sacred buffer + equal 1/N** math as ORB (§5). Trendline consumes remaining slots after the 7:30 ORB batch within the combined open cap.

### 6.1 Sacred buffer + 1/N slots

| Concept | Rule |
|---------|------|
| Buffer | **10%** — floor locked 7:30 PT; never shrinks intraday; ratchets on profit |
| Tradable pool | **90%** (`0DTE_TRADING_CAPITAL_PCT` / alias `TRENDLINE_ACCOUNT_ALLOCATION_PCT`) |
| Slot target | `tradable_equity / 0DTE_SLOT_COUNT` (**50** local; live 7), then capped at `0DTE_MAX_POSITION_SIZE_SUM` ($600). `TRENDLINE_SLOT_COUNT` is retired |
| Entry budget | `min(slot_target, cash − buffer)` — partial slot OK when cash is short |

### 6.2 Allocation algorithm

```text
equity_basis      = cash + open_entry_costs
buffer_reserve    = max(session_floor, equity × 10%)
tradable_equity   = equity − buffer
slot_capital      = tradable_equity / 0DTE_SLOT_COUNT
entry_budget      = min(slot_capital, cash − buffer)
budget            = entry_budget × clamp(entry_size_multiplier, 0.10, 1.00)
quantity          = max(1, floor(budget / (premium × 100)))
```

**Per-trade cap:** min(1/N of tradable equity, **$600** sum cap). No contract-count cap. Reject if estimated cost is above **102% of slot**.

### 6.3 Risk gate order (execution path)

1. Per-symbol dedupe  
2. **Red Day tier gate** (§3.8)  
3. Combined cap (`TOTAL_0DTE_MAX_POSITIONS=50`, local; live 7)  
4. Path open cap (`TRENDLINE_0DTE_MAX_POSITIONS=50` · ORB2 `BREAKOUT_0DTE_MAX_POSITIONS=50`)  
   Since 10/13 (live `00611-9zz`) steps 3–4 also run first, before the chain pull and dir%, as `max_open_positions_precheck` (see §8.3).  
5. Buffer / cash  
6. GCS global slot  
7. `evaluate_intent`  
8. Sizing → **Execution Risk Layer** → broker  

**Entry cutoff:** `TRENDLINE_NO_NEW_ENTRIES_AFTER_PT=12:30`  
**Deferred retry:** `rearm_deferred_execution()` on cap/cash/Red Day block (silent Telegram).

Detail: [0DTETrendline.md §11](0DTETrendline.md#11-account-management)

---

## 7. Daily limits & exposure caps

| Control | ORB1 Breakout | Trendline | Enforced | Owner |
|---------|----------|-----------|:--------:|-------|
| Max concurrent (path) | **10** | **10** | **Y** | UAR `orb_cap_reached` / `trendline_cap_reached` |
| Combined book (shared pool) | **10** | **10** | **Y** | UAR `combined_cap_reached` |
| Slot dollars | Equal 1/N tradable | Equal 1/N tradable | **Y** | Shared allocator |
| Per-symbol dedupe | — | 1/symbol/day | **Y** | Trendline orchestrator |
| Entry window | 7:30 batch | until 12:30 PT | **Y** | Scheduler |
| Latency backoff | max 4–5 ORB | — | **Y** | ORB batch |
| **Daily loss halt** | Disabled by default | Disabled by default | Positive value only | UAR P4 `daily_loss_halt` |
| **`MAX_DAILY_LOSS_PCT`** | **0.0%** | **0.0%** | N by default | `configs/Risk.env` → UAR |

**After losses:** Open slots shrink via cash/buffer, but the research default keeps the app trading all day for entry review. `MAX_DAILY_LOSS_PCT=0.0` disables `daily_loss_halt`; restore a positive value such as `2.0` before live-capital trading to block all new ORB + Trendline entries once realized day P&amp;L breaches the threshold.

**Calendar risk filter (all paths):** The book is **enabled** on a weekday only when `should_skip_trading()` is false. A true skip is `MARKET_CLOSED` (NYSE closed), `LOW_VOLUME` (market open, desk policy off — Columbus Day, Veterans Day, Day Before Thanksgiving, Black Friday, Christmas Eve, NYE, and weekday Day After Christmas / New Year's), or `PRE_HOLIDAY` (the previous **market session** before a bank holiday or a **standalone** low-volume day). Friday before a Monday skip: 2026-09-04 Labor Day, 2026-10-09 Columbus Day. Tuesday before Veterans Day: 2026-11-10. Wednesday before Christmas Eve: 2026-12-23. Wednesday before NYE: 2026-12-30. A listed **Day Before …** day is already that session — no second walk-back (2026-11-24 and 2026-12-22 trade). Day After Christmas applies only when Dec 26 is a weekday (2026-12-28 trades). The in-sample training book can still show skipped Fridays as historical fills; the live loop will not open them. See [MarketCalendar.md](MarketCalendar.md).

Detail: [§8 Unified Account Risk](#8-unified-account-risk--account-scoped-p4-hooks)

### 7.1 Same-side 1h cooldown (reject + post-exit) {#71-same-side-1h-cooldown-reject--post-exit}

**Risk Management feature (Aug 10 · ORB1 QG parity Aug 12 · TL dual envelopes Aug 14):** After a **terminal / hard reject** (exec · MQ · path-opt QG) **or** any **filled position exit**, the system arms a **1 hour** cooldown on that path’s cooldown key. The **opposite side stays eligible** so a reverse breakout can still trade. Sibling Trendline slots stay independent (TL0 CALL ≠ TL1 CALL ≠ TL2 CALL). **TL0/TL1 dual envelopes** are also independent: AMD TL1 CALL reject or close does **not** block AMD TL1 PUT.

| Path | Cooldown key | Duration | Opposite side |
|------|--------------|----------|---------------|
| **ORB1** (ORB1 Breakout watch) | strategy + `symbol + side` | **3600s** | Open |
| **ORB2 Breakout** | strategy + `symbol + side` (ORB#2 high=CALL / low=PUT) | **3600s** | Open |
| **Trendline TL0 / TL1 / TL2 / TL3** | strategy + `symbol + trendline_slot + side` | **3600s** | Open (sibling slots too) |

**Shared duration knob:** `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS=3600` (`configs/Breakout0DTE.env`) — used by BO, TL (`path_side_reject_cooldown_seconds`), and ORB1.

#### When it triggers (best performance + risk)

| Trigger | Examples | Why |
|---------|----------|-----|
| **Terminal exec / market-quality reject** | `ask_size_too_small` · `bid_size_too_small` · `option_premium_too_low` · `option_spread_too_wide` · `pc_oi_filter_reject` · `quality_reject` · `exec_ready_ttl_expired` · other non-transient post-exec codes | Stops spam-retry on a thin or failed contract class; forces a cool-off before the same edge can qualify again |
| **Exec-ready TTL (capacity wait)** | `exec_ready_ttl_expired` arms the 1 h slot/side cooldown on `00611-9zz` (first pinned `00599-lhd`). **LOCAL ONLY:** `EXEC_READY_TTL_RETIRE_NO_COOLDOWN=true` retires the break as `exec_ready_ttl_retired`, no cooldown (see [0DTETrendline.md](0DTETrendline.md)). |
| **Filled exit (any P&L)** | `price_stop` · `asset_stop` · `und_trail` · `take_profit` · `eod_close` · scratch / BE / no-progress — **profit or loss** | Prevents accidental **immediate same-side re-entry** right after close (especially green exits that re-arm watch state) |

**Does not arm** (retry / wait classes): broker blips (`rate_limited`, `temporary_broker_error`), capital/slot waits that re-arm, or `open_same_side_dedupe` itself.

#### Desk rules

1. **Same side blocked 1h** — e.g. SMCI CALL exit or reject → SMCI CALL cannot re-enter on that path for 1h.  
2. **Opposite side open** — SMCI PUT can still qualify if the reverse edge breaks (subject to QG / ORB2 reclaim / open-position rules). **TL0/TL1 dual envelopes:** a CALL reject or close leaves the PUT envelope able to fire on the same slot.  
3. **TL slot-scoped** — ADBE TL1 CALL cooldown does **not** block ADBE TL0/TL2/TL3 CALL.  
4. **BO green or red exit** — re-arm `WAIT_BREAK` and keep the **exited side** on the **1h cooldown** (reject-parity). Opposite ORB2 edge stays eligible. Same-side needs ORB2 reclaim after the timer.  
5. **ORB1 hard reject** (QG or post-exec no-fill) — arms side cooldown + Telegram reject with reason; does **not** day-lock the whole symbol (opposite ORB1 side can still fire inside the ORB1 window).

#### Modules / logs

| Path | Arm | Check | Grep |
|------|-----|-------|------|
| TL | `mark_trendline_slot_side_cooldown` · `arm_trendline_post_exit_cooldown` | `is_trendline_slot_side_on_cooldown` | `TRENDLINE_SLOT_SIDE_COOLDOWN_BLOCK` |
| BO | `_mark_breakout_side_terminal_reject` · `arm_breakout_side_cooldown_after_exit` | `_breakout_side_is_terminal_rejected` | `BREAKOUT_SIDE_COOLDOWN_BLOCK` |
| ORB1 | `mark_orb1_side_cooldown` · `arm_orb1_side_cooldown_after_exit` | `is_orb1_side_on_cooldown` | `ORB1_SIDE_COOLDOWN_BLOCK` |

Exit wiring: `_save_orb_0dte_exit_priority_snapshot` · `_save_breakout_exit_priority_snapshot` · `_save_trendline_exit_priority_snapshot` (+ live BO exit alert path). Policy module: `modules/path_exec_day_exclude.py`. Tests: `tests/test_post_exit_side_cooldown.py`.

Post-fill monitor / close spine: [PositionMonitoring.md §8.4](PositionMonitoring.md#84-same-side-1h-cooldown-after-exit).

---

## 8. Unified Account Risk — account-scoped P4 hooks

**Module:** `modules/unified_0dte_portfolio_risk_manager.py` — `Unified0DTEPortfolioRiskManager`  
**Wired:** `easy0DTE/modules/options_trading_executor.py` → `execute_lotto_sleeve()` → `evaluate_entry()` when `UNIFIED_0DTE_RISK_ENABLED=true`  
**Account scope:** One demo ledger per app (`mock_options_history.json`) or one live broker account — ORB1 Breakout + Trendline share the same book. RESEARCH and FINAL are **separate app instances** → separate accounts.

**Not Portfolio Protection:** PP (Gates **3a/3b**) runs @ 7:30 on the **signal pool**. UAR runs at **every open** on the **traded account**. `daily_loss_halt` is disabled by the research default `MAX_DAILY_LOSS_PCT=0.0`; PP observe-only is separate.

### 8.1 Evaluation order @ `execute_lotto_sleeve`

Both ORB1 Breakout (`execute_by_strategy` → lotto) and Trendline (`trendline_options_executor` → lotto) hit the **same** choke point:

```text
execute_lotto_sleeve()
  → Unified0DTEPortfolioRiskManager.evaluate_entry()
       1. account_state_unavailable        (Live fail-closed only)
       2. P4 hooks                         ← daily_loss_halt when MAX_DAILY_LOSS_PCT > 0 · kill_switch_active
       3. broker_adapter_unavailable       (Live fail-closed only)
       4. portfolio caps                   ← combined / orb / trendline
       5. Red Day tier gate                ← when _red_day_filter_blocked (PP flag)
       6. Liquidity / ADV Guard clamps   ← vol% · OI% · book-depth · ADV (only if oversize)
       7. insufficient_buying_power
  → mock or broker fill
```

Trendline orchestrator logs `stage=risk_check | approved=deferred` when UAR is on. If a positive daily-loss threshold is restored, the hard block surfaces here as `LIVE_EXECUTION_BLOCKED | reason=daily_loss_halt`.

### 8.2 P4 account halt hooks (`risk_path=p4_halt`)

| `reject_reason` | Config | Default | Fire when |
|-----------------|--------|---------|-----------|
| **`daily_loss_halt`** | `MAX_DAILY_LOSS_PCT` | **0.0 disabled** (`Risk.env` §A) | Fires only when config is positive and account day realized P&amp;L % ≤ `-abs(MAX_DAILY_LOSS_PCT)` |
| **`kill_switch_active`** | `UNIFIED_0DTE_KILL_SWITCH` | false | Manual/env kill switch |

**Day P&amp;L source (Jun 30):**

| Mode | Source | Reset |
|------|--------|-------|
| **Demo** | Mock ledger `daily_stats.total_pnl` when `stats_trading_date` = PT today | Daily open + **EOD flatten** + startup sync |
| **Live** | In-memory tracker updated on confirmed closes | Daily open + **EOD flatten** |

**Account scope:** UAR reads the **traded account** only — demo `mock_options_history.json` per app instance (RESEARCH ≠ FINAL); live broker adapter balance + closes. ORB and Trendline share that ledger; there is no separate Trendline day-P&amp;L track.

**EOD halt clear:** When the live daily-loss halt is enabled, after unified EOD flatten (`_eod_flatten_unified_0dte`), `reset_unified_0dte_day_pnl_session(reason=eod)` zeros day P&amp;L and clears `daily_loss_halt` for the rest of the PT session. Demo persists `risk_halt_cleared_pt_date` on the mock ledger so a warm Cloud Run restart cannot re-apply a stale in-memory halt from earlier the same day.

Module: `modules/unified_0dte_account.py` — `sync_unified_0dte_day_pnl_from_account()` · `hydrate_unified_0dte_risk_from_account()` · `record_unified_0dte_realized_close()` · `reset_unified_0dte_day_pnl_session(reason=...)`

**Greps:**

```bash
grep "UNIFIED_0DTE_RISK | action=daily_loss_halt"
grep "LIVE_EXECUTION_BLOCKED | path=execute_lotto_sleeve"
grep "UNIFIED_0DTE_RISK_AUDIT"
grep "UNIFIED_0DTE_ACCOUNT | action=day_pnl_session_reset"
grep "UNIFIED_0DTE_ACCOUNT | action=eod_halt_reset_complete"
grep "UNIFIED_0DTE_ACCOUNT | action=startup_day_pnl_sync"
```

### 8.3 Portfolio caps (`risk_path=portfolio_cap`)

| `reject_reason` | Config | Default |
|-----------------|--------|---------|
| `combined_cap_reached` | `TOTAL_0DTE_MAX_POSITIONS` | **50** (local; live 7) |
| `orb_cap_reached` | `ORB_0DTE_MAX_POSITIONS` | **50** |
| `breakout_cap_reached` | `BREAKOUT_0DTE_MAX_POSITIONS` | **50** |
| `trendline_cap_reached` | `TRENDLINE_0DTE_MAX_POSITIONS` | **50** |
| `max_open_positions_reached` | same caps, gate-flow precheck (`MAX_OPEN_POSITIONS_PRECHECK_ENABLED=true`) | open_n / cap / cap_kind |

**10/13 (deployed `easy-etrade-strategy-00599-lhd`):** the cap is checked first (no API) in the TL / BO / ORB1 gate flows, counting open positions plus in-flight reservations. A full book is labelled `max_open_positions_reached` in the gate report, the reject log and the desk rejects. There is no chain pull, no dir% and no 1h cooldown. TL re-arms (exec-ready TTL still applies), BO and ORB1 hold the break. The executor cap codes above are now transient (retry, no cooldown); before, a TL post-executor `combined_cap_reached` retired the candidate. On 10/13, 88 of the 104 dir% "missing data" rejects happened with the book full.

### 8.4 Red Day tier bridge (`risk_path=red_day_tier_gate`)

When **`_red_day_filter_blocked`** is true (set by PP Gate **3a/3b** @ 7:30), UAR re-checks tier policy at open:

| Side | Tier 1 | Tier 2+ |
|------|--------|---------|
| **CALL** | ✅ | ❌ `red_day_tier2_call_blocked` |
| **PUT** | ✅ | ✅ |

Helper: `passes_red_day_0dte_style_gate()` in `modules/portfolio_protection.py`. ORB also trims queue in `prime_trading_system.py` before batch execute. Detail: [§3.8](#38-red-day-tier-execution-gate) · [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md).

### 8.5 Live fail-closed (`risk_path=account_state_unavailable` · `broker_fail_closed`)

When `UNIFIED_0DTE_FAIL_CLOSED=true` (default) and `MODE=live`:

| `reject_reason` | When |
|-----------------|------|
| `account_state_unavailable` | Broker balance cache invalid |
| `broker_adapter_unavailable` | Adapter missing or not available |

Demo mode: fail-closed **off** for balance reads; caps and P4 halts still apply.

### 8.6 Execution + buying power (inside UAR, after caps)

| `risk_path` | Typical `reject_reason` | Owner |
|-------------|-------------------------|-------|
| `execution_clamp` | `option_spread_too_wide` · `option_quote_stale` · participation · `invalid_requested_quantity` | Shared validator + Execution Risk Layer (§11) |
| `insufficient_buying_power` | Notional &gt; buying power (after pending reservations) | UAR |

Pending capital reservations: `UNIFIED_0DTE_RESERVATION_CREATED` / `RELEASED` — prevents double-spend across concurrent opens.

### 8.7 Related blocks outside UAR (same account, different layer)

| Stage / `reject_reason` | Layer | Notes |
|-------------------------|-------|-------|
| PP Gates **3a/3b** @ 7:30 | Portfolio Protection | Pool stress — not `daily_loss_halt` |
| `insufficient_unified_balance` | Trendline pre-executor | Cash/buffer before UAR |
| `buffer_reserve_exhausted` | Trendline sizing | Sacred buffer |
| `option_global_cap` · `OPTION_ENTRY_COOLDOWN` | Trendline GCS slots | Global daily slot ledger |
| `entry_kill_switch_active` | Shared 0DTE monitor health | Degraded quote streak — blocks **ORB + Trendline** entries (`0DTE_DEGRADED_*`) |
| **Same-side 1h cooldown** | Reject / post-exit RM | `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS` — [§7.1](#71-same-side-1h-cooldown-reject--post-exit) |
| Stealth / EOD / fail-safe | Post-fill | [0DTEProfitCapture.md](0DTEProfitCapture.md) |

### 8.8 Configuration (`configs/Account0DTE.env` · `configs/Risk.env`)

| Key | Default | File | Role |
|-----|---------|------|------|
| `UNIFIED_0DTE_RISK_ENABLED` | true | `Account0DTE.env` | Master switch for UAR @ open |
| `MAX_DAILY_LOSS_PCT` | **0.0 disabled** | `Risk.env` §A | Research default; set positive value such as `2.0` before live-capital trading |
| `UNIFIED_0DTE_KILL_SWITCH` | false | env | P4 manual halt all new entries |
| `UNIFIED_0DTE_FAIL_CLOSED` | true | `Account0DTE.env` | Live balance fail-closed |
| `UNIFIED_0DTE_LOG_AUDIT` | true | `Account0DTE.env` | `UNIFIED_0DTE_RISK_AUDIT` logs |
| `0DTE_SLOT_COUNT` | 50 (local; live 7) | `Account0DTE.env` | Shared equal-slot divisor — see §1 / §14.3 |
| `ORB_0DTE_MAX_POSITIONS` | 50 (local; live 7) | `Account0DTE.env` | ORB1 path cap |
| `BREAKOUT_0DTE_MAX_POSITIONS` | 50 (local; live 7) | `Account0DTE.env` | ORB2 path cap |
| `TRENDLINE_0DTE_MAX_POSITIONS` | 50 (local; live 7) | `Account0DTE.env` | Trendline path cap |
| `TOTAL_0DTE_MAX_POSITIONS` | 50 (local; live 7) | `Account0DTE.env` | Combined cap (= slot pool) |
| `0DTE_MAX_POSITION_SIZE_SUM` | 600 | `Account0DTE.env` | 10/9. `0` disables the dollar sum cap |
| `LIVE_MAX_CONTRACTS_PER_TRADE` | 0 | `Account0DTE.env` | `0` is no contract-count cap |
| `0DTE_DEGRADED_ENTRY_KILL_SWITCH_*` | true / 0.60 / 3 | `Account0DTE.env` | Shared quote-health entry halt |
| `LIVE_SIZE_CAP_ENFORCE_IN_DEMO` | **true** | `Account0DTE.env` | Demo applies Execution Risk market-limit clamps (desk soak default) |

**Tests:** `tests/test_unified_0dte_portfolio_risk_manager.py` · `tests/test_unified_0dte_account.py` · `tests/test_unified_0dte_hardening.py`

---

## 9. Single-leg vs spread sizing

| Aspect | Single-leg (active) | Debit spread (disabled) |
|--------|---------------------|-------------------------|
| Capital basis | `mid × qty × 100` | `debit × qty × 100` |
| Structure label | `strategy_type=single_leg` | `debit_spread` (inactive) |
| Selector | `select_single_leg_contract` — δ **0.18–0.42**, premium ≥**$0.03** (+ spot-scaled max), **8%** spread, near-ATM rank | `0DTE_DEBIT_LONG_*` (spread experiments only; env commented) |
| Live enabled | **Y** | `0DTE_ORB_SPREAD_*=false` |
| 35% executor guard | Demo audit | Demo + Live legacy |

Spreads disabled — if re-enabled, use max-loss/width sizing separately from single-leg premium sizing.

---

## 10. Maximum theoretical loss per position

| Structure | Capital at risk | Max loss | Exit floor |
|-----------|-----------------|----------|------------|
| **Lotto long** *(legacy label)* / **Single-leg long** | ≤ slot cap (~35% ORB · ~1/10 tradable TL) | 100% premium | Stealth · fail-safe ~−60% |
| **Debit spread** | Net debit × qty × 100 | ≤ spread width | Same stealth |

Post-fill: [0DTEProfitCapture.md](0DTEProfitCapture.md). Live qty: **Position Sizing** (slots) first; **Liquidity / ADV Guard** (§11) only when slot size exceeds market caps. `LIVE_MAX_CONTRACTS_PER_TRADE=0` (artificial hard max off).

---

## 11. Liquidity / ADV Guard (Execution Risk Layer)

**SoT (operators):** [LiquidityGuard.md](LiquidityGuard.md) — full governor table · ADV persistence · Slip Guard separation · runbook.  
**Module:** `modules/dte0_execution_risk_manager.py`  
**Wired:** ORB `execute_by_strategy()` · Trendline / Breakout lotto open → shared validator → `execute_lotto_sleeve()` → **`apply_live_execution_risk_limits()`**.

**This is not Position Sizing.** Upstream already computed `requested_qty` from the **90% tradable / 10 equal-slot** book (§5–§6). This layer is the Risk Management **ADV / liquidity guard**: it only reduces (or fail-closed rejects) when that slot request would be larger than market-executable limits for the chosen contract.

**Desk lock (Jul 31, 2026):** `LIVE_MAX_CONTRACTS_PER_TRADE=0` — no artificial 1-lot ceiling. Growth from $5k scales with slots until liquidity binds.

**Aug 3, 2026:** Startup `_ensure_liquidity_guard_adv_ready()` + GCS `{app}/adv_cache.json` — Cloud Run cold starts must not fail-closed as `underlying_adv_missing` with an empty ADV cache. See [LiquidityGuard.md §10](LiquidityGuard.md#10-runbook--underlying_adv_missing).

**Quote refresh reuse (Jul 31; Nov 2 book-size hydrate):** Before Liquidity Guard, `refresh_contract_quotes_if_enabled()` prefers a **select-time warm chain** or `OptionsChainManager.peek_cached_chain()` when age ≤ `LIVE_QUOTE_REFRESH_CACHE_MAX_AGE_SEC` (**5s**) and the matched leg has bid/ask/vol/OI/**bid_size/ask_size**. If bid/ask are live but **sizes are missing**, it does **not** `use_selected` — it hydrates sizes from the Market Quote OCC (same Demo/Live path). `LIVE_MIN_ASK_SIZE=1` fail-closes on `None` (`ask_size_missing`), not on a cheap premium. The pick hint copies `option_ask_size` / `option_bid_size` onto the order contract. Equity `volume_ratio` is never used as option capacity.

```text
# Position Sizing (upstream — always)
requested_qty = slot_affordable(entry_budget, ask × 100)   # ~9% equity per slot from 90% pool

# Liquidity / ADV Guard v1.7 (this module — binds only when needed)
entry_depth_cap = floor(ask_size × LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT)  # 200% of ask
# vol / OI / bid qty caps off (pct = 0). Bid is MQ (LIVE_MIN_BID_SIZE) only.

approved_qty = min(
  requested_qty,                                                    # slot wins → winning_governor=slot_request
  buying_power_affordable,
  entry_depth_cap,                                                  # 200% of displayed ask
  floor(ADV_dollars × LIVE_UNDERLYING_ADV_PARTICIPATION_PCT / (ask×100)),  # catastrophe
)
→ reject if approved_qty < 1 or required market fields missing (fail-closed)
```

**Aug 20, 2026 — v1.7:** Ask-depth **200%** (`LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT=2.0`). Vol / OI / exit-book off. ADV 0.5%. SoT: [LiquidityGuard.md](LiquidityGuard.md).
**Aug 17, 2026 — v1.6:** Volume qty cap off. OI off. Exit-book off. Hard max off. Min bid/ask 5. Ask 100% · ADV 0.5% unchanged. SoT: [LiquidityGuard.md](LiquidityGuard.md).
**Aug 17, 2026 — v1.5:** OI qty cap off. Book = 100% of ask; bid not a qty cap. Volume 5%. SoT: [LiquidityGuard.md](LiquidityGuard.md).
**Aug 11, 2026 — volume min floor:** `LIVE_OPTION_VOLUME_MIN_CONTRACTS=5`. Tiny session prints (e.g. OKLO vol=95 → raw cap 1) no longer unconditionally hard-cap a slot that clears the book. Missing volume still fail-closed. SoT: [LiquidityGuard.md](LiquidityGuard.md) §4 / §4.2.
### GPT desk response (Jul 31, 2026)

| GPT ask | Desk |
|---------|------|
| Vol 2% — redesign with 5m / projected option volume | **Keep 2%** on session cumulative contract volume only. No option 5m/projected series exists; do **not** invent or reuse equity `volume_ratio` as option capacity. |
| OI 1% too tight | **Raised to 5%** (`LIVE_OPTION_OI_PARTICIPATION_PCT=0.05`). Conditional OI bands deferred. |
| Book depth 25% for child orders not total position | **Keep 25% as total-position clamp** — entry is one-shot today. Telemetry splits `entry_depth_cap` / `exit_depth_cap`; `winning_governor` = `exit_book_depth` or `entry_book_depth` when one side tighter. |
| ADV 0.5% weak relationship | **Keep as secondary catastrophe** fail-closed ceiling. |
| Staged Live hard max contracts | **Keep `LIVE_MAX_CONTRACTS=0`** (off). Revisit only as explicit later ops policy. |
| Spread / exit checks | **Already implemented** — path spread caps · last-look · shared validator · true-to-live bid STC. |

**Growth examples (liquid name — high vol/OI/depth/ADV):**

| Equity | Slot (~9%) | Typical winner |
|--------|------------|----------------|
| **$5,000** | ~$450 | **`slot_request`** — guard idle |
| **$25,000** | ~$2,250 | Often still slot, unless thin book |
| **$100,000** | ~$9,000 | Guard may bind (vol/OI/depth/ADV) |

Thin names: guard can bind even on small accounts (by design — executable limits, not account policy).

**Separation:** static entry floors (`0DTE_ENTRY_MIN_VOLUME` / OI) = eligibility only · participation caps = how big vs market. Do **not** raise static floors for scaling.

### Shared entry liquidity validator (Jul 2026)

**Module:** `easy0DTE/modules/option_entry_liquidity_validator.py` · **`validate_option_entry_liquidity()`**

| Check | ORB | Trendline |
|-------|-----|-----------|
| Bid / ask present | ✓ | ✓ |
| Spread | ≤ **8%** (`orb_entry_max_spread_points`) | ≤ **30%** (`trendline_entry_max_spread_points`) |
| Volume / OI | ≥ `0DTE_ENTRY_MIN_VOLUME` / `0DTE_ENTRY_MIN_OPEN_INTEREST` (**50 / 100** — eligibility) | Same |
| Premium | `0DTE_MIN_ENTRY_PREMIUM`–`0DTE_MAX_ENTRY_PREMIUM` (**$0.03–$2.50**) | Same (ORB + TL + BO) |
| Quote age | ≤ `0DTE_LIVE_MAX_QUOTE_AGE_SECONDS` when enforced | Same pre-execution gate |
| Live chain | `REQUIRE_LIVE_OPTION_DATA` — no synthetic when required | `TRENDLINE_ALLOW_SYNTHETIC_ENTRY=false` default |

**Wiring:** ORB — `options_trading_executor.execute_by_strategy()` before `execute_lotto_sleeve()`. Trendline — `select_contract()` + `_validate_final_entry_execution()` before payload build. Market-quality in `dte0_execution_risk_manager.py` calls the same validator and emits canonical `option_*` codes.

**Greps:** `0DTE_EXECUTION_POLICY_AUDIT` · `ORB_0DTE_ENTRY_LIQUIDITY_REJECT` · `TRENDLINE_PIPELINE | stage=execution_blocked` · `LIVE_ENTRY_MARKET_QUALITY_REJECT` · `LIVE_ENTRY_BOOK_DEPTH_CLAMP` · `LIVE_ENTRY_ADV_CLAMP`

**Tests:** `tests/test_option_entry_liquidity_validator.py` · `tests/test_0dte_execution_risk_manager.py`

**Canonical execution (`MODE` only — FINAL app):**

| `MODE` | 0DTE opens/closes | Market data |
|--------|-------------------|-------------|
| **`demo`** | `MockOptionsExecutor` / GCS `mock_options_history.json` — **no** broker order submit | Live E*TRADE chains/quotes |
| **`live`** | `ETradeBrokerAdapter` — `broker_fill_confirmed` required before registration | Live E*TRADE API |

**Retired (do not set):** `0DTE_BROKER_EXECUTION_ENABLED`, `0DTE_DEMO_MOCK_FALLBACK_ON_BROKER_SUBMIT_FAIL`. Demo never falls back to mock after a broker submit failure — there is no demo broker submit path.

### Executable pricing (true-to-live PnL)

Long 0DTE single-leg options (CALL and PUT) use the same side economics:

| Action | Broker order | Price | Applies to |
|--------|--------------|-------|------------|
| **Enter** | Buy to Open | **Ask** | Demo sim + live entry cost basis |
| **Exit** | Sell to Close | **Bid** | Monitor marks, stealth exits, demo close ledger |

**Module:** `modules/option_executable_pricing.py` — `long_option_buy_to_open_price()`, `long_option_sell_to_close_price()`.

| Layer | Entry (BTO) | Exit / monitor (STC) |
|-------|---------------|----------------------|
| **Demo sim** | `MockOptionsExecutor` debits **ask** | Stealth + mock close credit **bid** |
| **Live broker** | E*TRADE `average_fill_price` (actual BTO) | Stealth signals **bid**; broker STC reconcile |
| **Chain quotes** | Live E*TRADE option chain (bid/ask/mid) | Same chain on every monitor tick |

**Stealth config:** `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS=true`, `OPTION_USE_BID_FOR_LONG_EXIT=true` (`configs/Shared.env`). Legacy Trendline aliases (`OPTION_STEALTH_TLINE_*`) still honored as fallbacks.

**Demo fidelity:** **Spread / book-size market quality** is enforced in demo when `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS=true` (default). **Live bid/ask required** at entry — no synthetic premium fallbacks. **`LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true`** so Demo qty matches Live market-limit clamps. PnL uses ask entry / bid exit.

**Tests:** `tests/test_option_executable_pricing.py` · **Post-fill detail:** [0DTEProfitCapture.md §3.4](0DTEProfitCapture.md#34-bidask-executable-pricing--monitor--exit).

### Portfolio sizing vs Liquidity / ADV Guard

| Layer | Question | Owner |
|-------|----------|--------|
| **Position Sizing** | How much **should** deploy from the book? | Sacred 10% buffer · 90% tradable · equal 1/N slots (§5–§6) · PP gates |
| **Liquidity / ADV Guard** | Is that slot size still **market-executable**? | `apply_live_execution_risk_limits()` — clamps only when oversize |

Does **not** modify Portfolio Protection, rank multipliers, stealth, or the slot allocator.

### Market governors (Liquidity / ADV Guard — order)

| # | Gate | Config | Behavior |
|---|------|--------|----------|
| 1 | Position clamp | `LIVE_MAX_CONTRACTS_PER_TRADE` (**0**=off) | Optional hard max; **off** — slots + liquidity govern growth |
| 2 | Buying power | broker balance | Clamp down; reject if &lt; 1 |
| 3 | Vol participation | `LIVE_OPTION_VOLUME_PARTICIPATION_PCT=0`, `LIVE_OPTION_OI_PARTICIPATION_PCT=0` | **Off** (v1.6). No vol/OI qty cap; missing volume/OI does not fail |
| 4 | Book depth | `LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT=2.0`, `LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT=0` | qty ≤ 200% of displayed ask; bid is MQ only |
| 5 | Underlying ADV | `LIVE_UNDERLYING_ADV_PARTICIPATION_PCT=0.02` | Catastrophe ceiling — option notional ≤ ADV$ × pct; fail-closed if ADV unknown (no generic $200M floor) |
| 6 | Market quality | Shared validator + path spread + `LIVE_MIN_BID_SIZE` / `LIVE_MIN_ASK_SIZE` + quote refresh | Eligibility: vol · OI · premium · quote age · spread · min book sizes |

**Spread cap (market quality)** routes by `strategy_type` — **not** a global `MIN()` across paths:

| Path | Config key | Cap | Resolver |
|------|------------|-----|----------|
| **ORB1 Breakout** | `0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT` | **8%** (% points) | `orb_entry_max_spread_points()` |
| **Trendline 0DTE** | `TRENDLINE_OPTION_MAX_SPREAD_PCT` | **30%** (fraction `0.30`) | `trendline_entry_max_spread_points()` |

`LIVE_MAX_SPREAD_PCT` **removed** (Jun 2026). **Strict path caps only**. See [Configs.md § Entry spread policy](Configs.md#entry-spread-policy-path-owned).

| Mode | Enforcement |
|------|-------------|
| Demo + `LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true` (desk default) | All market governors enforced |
| Demo + enforce false | Size clamps audit-only; market quality still on when true-to-live marks |
| Live | Always enforce all governors |

**Audit:** `LIVE_ENTRY_EXECUTION_RISK_AUDIT` includes `winning_governor` (`volume_participation` / `oi_participation` / `exit_book_depth` / `entry_book_depth` / `book_depth` / `underlying_adv` / `buying_power` / `position_clamp` / `slot_request` / `market_quality`) plus `entry_depth_cap` / `exit_depth_cap`.

**Greps:** `LIVE_ENTRY_EXECUTION_RISK_AUDIT` · `LIVE_ENTRY_QUANTITY_CLAMP` · `LIVE_ENTRY_BUYING_POWER_CLAMP` · `LIVE_ENTRY_PARTICIPATION_CLAMP` · `LIVE_ENTRY_BOOK_DEPTH_CLAMP` · `LIVE_ENTRY_ADV_CLAMP` · `LIVE_ENTRY_MARKET_QUALITY_REJECT`

**Tests:** `tests/test_0dte_execution_risk_manager.py`

---

## 12. Live vs Demo

| Aspect | Demo (production today) | Live |
|--------|-------------------------|------|
| Dollar sizing | Mock $5k unified account | E*TRADE **`ETRADE_0DTE_ACCOUNT_ID`** |
| Close ledger | `demo_account/mock_options_history.json` | `live_account/` + broker |
| **Profit capture** | Same `TrendlineOptionsStealthEngine` · `Shared.env` `OPTION_STEALTH_*` | Same engine · `ProfitCapture.env` |
| **Trade economics** | Ask/bid fills + modeled fees · `TRADE_CLOSE_CERTIFICATION` | Broker fills + same fee model · net P&L on ledger |
| **Jun 18 desk** | **IWM Trendline tail** (~−75%) · 0 ORB1 Breakout fills | **Flat** — 0 open |
| Portfolio Protection | Same flag @ 7:30 | Same |
| **Unified Account Risk** | Same P4 hooks @ open | Same |
| Liquidity / ADV Guard | Enforced (`LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true`) | Enforced — clamps only if slot oversize |
| Broker submit | Mock + synthetic fallback | Live broker + retry |

Detail: [0DTEProfitCapture.md §3.7–§3.8](0DTEProfitCapture.md#37-demo-vs-live--same-stealth-different-close-path) · [LiveModeReadiness.md](LiveModeReadiness.md).

---

## 13. Live scaling phases

| Phase | Action |
|-------|--------|
| **1 — Slot + guard soak** | Confirm `winning_governor=slot_request` on liquid $5k names · guard binds on thin/large · `LIVE_MAX_CONTRACTS=0` |
| **2 — Tune participation** | Adjust vol% / OI% / book-depth% / ADV% from Demo soak evidence |
| **3 — Production Live** | Same governors on Live broker · confirm `MAX_DAILY_LOSS_PCT` on live account |

Leave unchanged: `OPTION_STEALTH_*` · `LIVE_CLOSE_*` · Portfolio Protection · **UAR P4 hooks** · Convex · Hard Gate · fail-closed fills. Static entry floors stay eligibility-only unless soak proves otherwise.

---

## 14. Configuration reference

### 14.1 Portfolio Protection & Red Day

See §3.12.

### 14.1a Unified Account Risk (P4)

See [§8.8](#88-configuration-configsaccount0dteenv--configsriskenv).

### 14.2 Live execution risk (`configs/Shared.env` §6 · `configs/Account0DTE.env` §4)

| Key | Default | Role |
|-----|---------|------|
| `LIVE_MAX_CONTRACTS_PER_TRADE` | **0** (off) | Optional hard max — desk lock off |
| `LIVE_OPTION_VOLUME_PARTICIPATION_PCT` | **0** (off) | Vol qty cap **off** (v1.6) |
| `LIVE_OPTION_VOLUME_MIN_CONTRACTS` | **5** | Dormant until vol pct &gt; 0 |
| `LIVE_OPTION_OI_PARTICIPATION_PCT` | **0** | OI qty cap **off** |
| `LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT` | **2.0** | qty ≤ 200% of displayed ask |
| `LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT` | **0** | Bid not a qty cap (MQ `LIVE_MIN_BID_SIZE` still applies) |
| `LIVE_UNDERLYING_ADV_PARTICIPATION_PCT` | **0.02** | Option notional ≤ ADV$ × pct |
| `LIVE_MIN_ASK_SIZE` / `LIVE_MIN_BID_SIZE` | **1 / 1** | Min book sizes (fail-closed if **missing** / `None` — `ask_size_missing`. Bid/ask of 0 on the chain is picker `no_quote`, not this code.) |
| `LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD` | **0** | Generic missing-ADV floor **off** |
| `LIVE_REFRESH_QUOTES_BEFORE_SUBMIT` | true | Quote refresh before submit (stamps bid/ask size) |
| `LIVE_QUOTE_REFRESH_CACHE_MAX_AGE_SEC` | **5** | Reuse warm/manager chain if ≤ N seconds + complete fields |
| `LIVE_SIZE_CAP_ENFORCE_IN_DEMO` | **true** | Demo enforce market-limit clamps |
| `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS` | **3600** | Shared **same-side 1h** reject + post-exit cooldown (ORB1 · BO · TL) — [§7.1](#71-same-side-1h-cooldown-reject--post-exit) · `Breakout0DTE.env` |

**Entry spread caps** — path-owned (`modules/spread_policy.py`); see §14.2a. **`LIVE_MAX_SPREAD_PCT` removed** — do not reintroduce as a global broker gate.

### 14.2a Entry spread policy (path-owned)

| Path | File | Key | Default | Applies to |
|------|------|-----|---------|------------|
| **ORB1 Breakout** | `ORB0DTE.env` | `0DTE_MAX_BID_ASK_SPREAD_PCT` | **8.0** | Chain manager filter |
| **ORB1 Breakout** | `ORB0DTE.env` | `0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT` | **8.0** | Selector + broker entry gate |
| **Trendline** | `Trendline0DTE.env` | `TRENDLINE_OPTION_MAX_SPREAD_PCT` | **0.30** | Contract pick + broker entry gate |

Same caps in **demo sim** (`MODE=demo`) and **live broker** (`MODE=live`). Desk soak: `LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true` so Demo qty uses the same market governors as Live; **spread / book-size market quality** enforced when `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS=true` (default). **Tests:** `tests/test_spread_policy.py`.

### 14.2b Executable pricing (true-to-live PnL)

| Key | Default | File | Role |
|-----|---------|------|------|
| `OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS` | `true` | `Shared.env` §3e (DEMO) · `ProfitCapture.env` (LIVE) | BTO @ ask / STC @ bid marks for ORB + Trendline singles |
| `OPTION_USE_BID_FOR_LONG_EXIT` | `true` | same | Long CALL/PUT monitor + exit at **bid** |
| `OPTION_STEALTH_TLINE_TRUE_TO_LIVE_EXIT_MARKS` | `true` | same | Legacy alias (Trendline) — falls back if 0DTE key unset |
| `OPTION_STEALTH_TLINE_USE_BID_FOR_LONG_EXIT` | `true` | same | Legacy alias (Trendline) |

**Module:** `modules/option_executable_pricing.py` · **Tests:** `tests/test_option_executable_pricing.py` · [0DTEProfitCapture.md §3.4](0DTEProfitCapture.md#34-bidask-executable-pricing--monitor--exit).

### 14.2c Trade economics (fees · net P&L · breakeven)

| Key | Default | File | Role |
|-----|---------|------|------|
| `OPTIONS_OPEN_COMMISSION_PER_CONTRACT` | **0.65** | `ProfitCapture.env` §3e · `Shared.env` (RESEARCH) | Per-contract open commission (USD) |
| `OPTIONS_CLOSE_COMMISSION_PER_CONTRACT` | **0.65** | same | Per-contract close commission (USD) |
| `OPTIONS_OPEN_REGULATORY_FEE_PER_CONTRACT` | **0.0** | same | Optional regulatory add-on (open) |
| `OPTIONS_CLOSE_REGULATORY_FEE_PER_CONTRACT` | **0.0** | same | Optional regulatory add-on (close) |

**Module:** `modules/options_trade_economics.py` — `build_entry_economics()` · `finalize_close_economics()` · `emit_trade_close_certification()`  
**Sizing:** Trendline balance checks debit **`total_cost_basis`** (ask premium + open fees), not mid.  
**Ledger:** Closed trades persist **`economic_summary`** with **`net_pnl`** and **`breakeven_exit_premium`**.  
**Grep:** `TRADE_CLOSE_CERTIFICATION` · `trade_economics_entry` · [0DTEProfitCapture.md §3.8](0DTEProfitCapture.md#38-trade-economics--net-pnl--breakeven) · **Tests:** `tests/test_options_trade_economics.py`

### 14.3 Position sizing & concurrency (canonical — edit `Account0DTE.env` first)

**Desk rule:** Change **slot / path / total** together and keep them equal. Full table: [§1 0DTE position sizing & caps](#0dte-position-sizing--caps-one-place-to-edit).

| Group | Keys (defaults) | File |
|-------|-----------------|------|
| **Dollar sizing** | `0DTE_SLOT_COUNT=50` (local; live 7) · `0DTE_TRADING_CAPITAL_PCT=90` · `0DTE_MAX_POSITION_SIZE_PCT=35` · `0DTE_MAX_POSITION_SIZE_SUM=600` · `LIVE_MAX_CONTRACTS_PER_TRADE=0` · `0DTE_DEMO_STARTING_BALANCE=100000` | `Account0DTE.env` |
| **Open position caps** | `ORB_0DTE_MAX_POSITIONS=50` · `BREAKOUT_0DTE_MAX_POSITIONS=50` · `TRENDLINE_0DTE_MAX_POSITIONS=50` · `TOTAL_0DTE_MAX_POSITIONS=50` (live `easy-etrade-strategy-00611-9zz`, first pinned 10/13 in `00599-lhd`; FINAL Live Pilot 7 retired) | `Account0DTE.env` |
| **Monitor symbol cap** | `0DTE_MONITOR_MAX_SYMBOLS=50` (local; live 7) | `Account0DTE.env` |
| **Quote health kill switch** | `0DTE_DEGRADED_WARN_*` · `0DTE_DEGRADED_ENTRY_KILL_SWITCH_*` | `Account0DTE.env` |
| **ORB 7:30 funnel** | `ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX=50` · `0DTE_RANK_SIZING_ENABLED=false` · optional `0DTE_RANK_MULT_*` | `ORB0DTE.env` |
| **Retired aliases** | `TRENDLINE_SLOT_COUNT` · `TRENDLINE_MAX_OPEN_POSITIONS` · `ORB_0DTE_BATCH_MAX_POSITIONS` · `0DTE_MAX_POSITIONS` · `UNIFIED_0DTE_*_MAX_POSITIONS` — do not set. `MAX_TOTAL_OPTION_POSITIONS` is still read as a TOTAL fallback and is set in `Shared.env` (50); keep it equal to TOTAL | Path / legacy |
| **Entry δ (not slot count)** | `0DTE_ENTRY_DELTA_MIN/MAX` | `ORB0DTE.env` / `Trendline0DTE.env` |
| **No-data (post-fill)** | `OPTION_MAX_DEGRADED_SECONDS=90` | `ProfitCapture.env` (FINAL) · `Shared.env` (RESEARCH) |
| **Portfolio-wide (SO+0DTE)** | `MAX_OPEN_POSITIONS` (retired, see above; not in `Risk.env`) · SO `MAX_CONCURRENT_TRADES` (not set in `ORBSO.env`; code default) | `Risk.env` · `ORBSO.env` |

Trendline Red Day tier keys (`TRENDLINE_RED_DAY_GATE_*`) stay in `Trendline0DTE.env` — eligibility, not sizing.

### 14.4 Obsolete / dead (do not tune or reintroduce)

| Dead / obsolete | Notes |
|-----------------|--------|
| `0DTE_LOTTO_SLEEVE_MAX_COST` · `0DTE_LOTTO_SLEEVE_ENABLED` | Retired sleeve |
| `TRENDLINE_MAX_TRADES_PER_DAY` · `TARGET_TRENDLINE_ACTIVE` · `TRENDLINE_MAX_POSITION_PCT` | Removed Jun 2026 |
| `MAX_TRENDLINE_OPTION_SYMBOLS` (default **5**) | Replaced by `0DTE_MONITOR_MAX_SYMBOLS` (now 50 local; live 7) |
| `TRENDLINE_OPTION_NO_DATA_GRACE_SECONDS=180` · `…FORCE_EXIT…=300` · `TRENDLINE_MIN_DIAGNOSTIC_HOLD_SECONDS=120` | Superseded by `OPTION_MAX_DEGRADED_SECONDS=90` for all 0DTE singles |
| `LIVE_MAX_SPREAD_PCT` · `0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT` | Path-owned spread keys only |
| `0DTE_BROKER_EXECUTION_ENABLED` · `0DTE_DEMO_MOCK_FALLBACK_ON_BROKER_SUBMIT_FAIL` | Use `MODE=demo\|live` |

---

## 15. Code & files index

| Area | Path |
|------|------|
| **Unified Account Risk (P4)** | `modules/unified_0dte_portfolio_risk_manager.py` — `evaluate_entry()` · `_check_p4_hooks()` |
| **Account day P&amp;L** | `modules/unified_0dte_account.py` — `sync_unified_0dte_day_pnl_from_account()` |
| **UAR wiring @ open** | `easy0DTE/modules/options_trading_executor.py` — `execute_lotto_sleeve()` |
| **UAR startup sync** | `modules/unified_0dte_startup.py` — `validate_unified_0dte_startup()` |
| Portfolio Protection 3a | `modules/portfolio_protection.py` — `evaluate_portfolio_protection()` |
| PS floor + cap (0DTE) | `dte0_execution_filters` · `prime_0dte_strategy_manager._apply_portfolio_protection_zero_ps_and_cap()` |
| Red Day tier gate | `portfolio_protection.passes_red_day_0dte_style_gate()` · `prime_trading_system.py` |
| Convex Red Day refresh | `prime_trading_system._refresh_0dte_pipeline_for_portfolio_red_day()` |
| ORB sizing | `prime_0dte_strategy_manager.calculate_position_sizing()` → `compute_0dte_capital_snapshot()` |
| Shared capital math | `easyTrendline/trendline_capital_allocation.py` · `unified_0dte_account.compute_unified_0dte_capital_snapshot()` |
| ORB execute | `prime_trading_system._execute_0dte_options_trades()` |
| Slot refresh | `prime_trading_system._refresh_0dte_slot_capital()` (alias `_refresh_trendline_slot_capital`) |
| Trendline execute | `easyTrendline/trendline_options_executor.py` |
| Execution risk | `modules/dte0_execution_risk_manager.py` |
| Entry liquidity validator | `easy0DTE/modules/option_entry_liquidity_validator.py` |
| Executable pricing | `modules/option_executable_pricing.py` |
| Trade economics | `modules/options_trade_economics.py` |
| Shared broker path | `easy0DTE/modules/options_trading_executor.execute_lotto_sleeve()` |
| Combined cap | `prime_trading_system._total_option_positions_count()` |

---

## 16. Operational verification

### 16.1 Pre-open checklist

- [ ] `ENABLE_PORTFOLIO_PROTECTION=true` · split-tape + volume-coverage keys in `Risk.env` §B  
- [ ] `UNIFIED_0DTE_RISK_ENABLED=true` · research default `MAX_DAILY_LOSS_PCT=0.0`; restore a positive halt threshold before live-capital trading  
- [ ] `0DTE_SKIP_ZERO_PRIORITY_SCORE=true` · `ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX=10`  
- [ ] `TRENDLINE_RED_DAY_GATE_ENABLED=true` · slots **10/10** · combined cap **10**  
- [ ] Live market-limit soak: `LIVE_MAX_CONTRACTS_PER_TRADE=0` · `LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true` · book-depth + ADV governors 
- [ ] OAuth valid — [SORisk.md § Credential risk](SORisk.md#12-operational-credential-risk)

### 16.2 Log tokens

```bash
# Unified Account Risk (P4)
grep "UNIFIED_0DTE_RISK | action=daily_loss_halt"
grep "UNIFIED_0DTE_RISK_AUDIT"
grep "LIVE_EXECUTION_BLOCKED | path=execute_lotto_sleeve"
grep "UNIFIED_0DTE_ACCOUNT | action=day_pnl_session_reset"

# Portfolio Protection
grep protection_day
grep "0DTE_PIPELINE stage=portfolio_protection_zero_ps_cap"
grep TRENDLINE_RED_DAY_GATE

# Sizing
grep "Calculating position sizes"
grep "ORB_0DTE_CANDIDATE_SELECTED | .*capital_allocated="
grep TRENDLINE_CAPITAL_AUDIT

# Caps
grep OPTION_GLOBAL_CAP_REACHED
grep buffer_reserve_exhausted

# Live execution risk
grep LIVE_ENTRY_EXECUTION_RISK_AUDIT

# Same-side 1h cooldown (reject + post-exit)
grep BREAKOUT_SIDE_COOLDOWN_BLOCK
grep TRENDLINE_SLOT_SIDE_COOLDOWN_BLOCK
grep ORB1_SIDE_COOLDOWN_BLOCK
grep "stage=post_exit_cooldown"
grep "exited_side_cooldown_1h"

# Fills
grep ORB_0DTE_POSITION_REGISTERED
grep EXECUTION_FILL_RECONCILED
grep TRADE_CLOSE_CERTIFICATION
grep trade_economics_entry
```

### 16.3 Monitor mark fidelity (Jul 17 · ownership Jul 14)

Post-fill stealth depends on **live option marks**, not underlying price. Both DEMO and LIVE share the same Premium Engine → evaluate path; Live adds broker synchronizer first:

| Step | Module | Behavior |
|------|--------|----------|
| **Scheduler** | `_monitor_loop` → `Unified0DTEPositionMonitor.maybe_tick` | ~**2s** · fill `request_wake` · `u0dte_<ms>` |
| Exact OCC quotes | `fetch_exact_contract_quotes` | Batch by OCC; inject into position metadata |
| Bridge (incomplete only) | `_orb_hydrate_chain_cache_for_monitor` | Unique underlyings with missing OCC; empty set = **zero** chain requests |
| Broker sync (Live) | `BrokerPositionSynchronizer.synchronize()` | Sole registry writer · repair · ownership gate on dispatch |
| Premium Engine | `premium_engine.evaluate` via `update_positions_with_real_prices` | Frozen `PremiumSnapshot` · sole-writer HWM/PnL patch |
| ORB / TL exits | `_0dte_run_stealth_exit_decision` → `stealth.evaluate` | Pure `ExitDecision` → latch → close executor |
| 30s watchdog | `shared_loop_30s_watchdog` | Unhealthy-only; may call core without exact-quote context |

**Healthy session greps:**

```bash
grep UNIFIED_0DTE_MONITOR
grep BROKER_POSITION_SYNC
grep ORB_0DTE_MONITOR_CHAIN_HYDRATE_SKIPPED
grep ORB_0DTE_MONITOR_CHAIN_HYDRATED
grep OPTION_POSITION_MONITOR_TICK
grep premium_source=exact
grep OPTIONS_STEALTH_EXIT
```

**Anti-pattern:** sustained `premium_source=modeled_proxy` + `synthetic_active=true` on open positions → trailing/profit-lock exits blocked → EOD-only flatten. Detail: [0DTEProfitCapture.md §2.2](0DTEProfitCapture.md#22-monitor-cadence).

---

## 17. Changelog

| Date | Change |
|------|--------|
| **Sep 2, 2026** | **9.2 path SS admit hydrate** — restamp `qg_admit` after the Layer C chain so persist includes IV/dir%/ATM \|delta\|/`otm_pct`; RSI/BB/ATR from cached 1m bars; `wick_against`/`close_with` aliases. No extra broker fetch. Local until `deploy now`. §2.2 · [Data.md](Data.md) · [0DTESkipSignal.md §0](SkipSignal/0DTESkipSignal.md#0-live-skip-signal-stack) |
| **Sep 1, 2026** | **Liquidity Guard** — ADV **2.0%** · generic $200M floor **off** (`LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD=0`) · min bid/ask **1**. Local until `deploy now`. [LiquidityGuard.md](LiquidityGuard.md) · §11 |
| **Aug 31, 2026** | **QG admit hydrate** — `stamp_qg_admit_fields` before enter/skip (cached 1m + last quote + L1 + same Layer C chain). Compact `qg_admit` on `watch_fsm`. Full PO snapshot execution-only. Suggested QG 31 is **not** live `GATE_*`. §2.2 · [Data.md](Data.md) · [0DTEQualityGates.md](0DTEQualityGates.md). Local until `deploy now`. |
| **Aug 27, 2026** | **Skip Signal stack** — absolute CALL-SS-1724 · PUT-SS-1724 · per-strategy/side 0-MFE (`SSORB1CALL` · `SSTL1PUT` · `TL2+SSCALL` · …) on the QG stack before execution. Reject if either absolute side skip **or** the path/side knife matches. Wide collect stays. Local until `deploy now`. [0DTESkipSignal.md §0](SkipSignal/0DTESkipSignal.md#0-live-skip-signal-stack) · §2.2b |
| **Aug 20, 2026** | **Liquidity Guard v1.7** — ask-depth **200%** (`LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT=2.0`) · vol / OI / exit-book **off** · hard max **off** · min bid/ask **5** · ADV **0.5%**. Local until `deploy now`. [LiquidityGuard.md](LiquidityGuard.md) · §11 |
| **Aug 17, 2026** | **Liquidity Guard v1.6** — vol / OI / exit-book qty caps **off** · hard max **off** · min bid/ask **5** · ask-depth **100%** · ADV **0.5%**. Local until `deploy now`. [LiquidityGuard.md](LiquidityGuard.md) · §11 |
| **Aug 12, 2026** | **ORB1 hard QG reject → 1h same-side cooldown** — parity with BO/TL; Telegram reject always includes reason; TL keys **TL0–TL3**; §7.1 |
| **Aug 10, 2026** | **Same-side 1h cooldown (RM)** — after terminal exec reject **and** any filled exit, block **strategy/symbol/side** for `BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS=3600`; opposite side open; ORB1 · ORB2 BO · TL0–TL3 · §7.1 · [PositionMonitoring §8.4](PositionMonitoring.md#84-same-side-1h-cooldown-after-exit) |
| **Jul 31, 2026** | **Liquidity / ADV Guard desk pass** — Position Sizing remains 90%/10 slots (§5–§6); `LIVE_MAX_CONTRACTS=0`; OI participation **5%**; book-depth total-position clamp + entry/exit telemetry; ADV catastrophe; quote refresh warm/manager reuse ≤**5s**; `LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true`; GPT response table in §11 · Breakout shares slot pool (UAR path label still maps BO→ORB — combined TOTAL=10 binds) |
| **Jul 27, 2026** | **Unified entry contract package (local R+F)** — δ **0.18–0.42** · premium floor **$0.03** · `PREFER_LOWEST_DELTA=false` (near-ATM) · OTM prefer/hard **12%/15%** · `unified_contract_select.py` walk · §5.6 / §11 · [0DTEORB](0DTEORB1Breakout.md#unified-entry-contract-package-jul-27--local-rf) · [Configs](Configs.md) |
| **Jul 17, 2026** | **Unified monitor P0 docs** — exact OCC · empty-set force_refresh · tick-id dedup · one `maybe_tick` · §16.3 |
| **Jul 14, 2026 (c)** | **Broker-truth ownership** — synchronizer · Premium Engine · `evaluate` cutover · §16.3 · [0DTEProfitCapture](0DTEProfitCapture.md) · [LiveModeReadiness](../LiveModeReadiness.md) |
| **Jul 14, 2026 (b)** | **Shared 0DTE post-fill** — `unified_0dte_premium_bucket` · `0DTE_DEGRADED_*` kill switch gates ORB+TL · `OPTION_MAX_DEGRADED_SECONDS=90` for all singles · `0DTE_MONITOR_MAX_SYMBOLS=10` · sizing docs grouped in §1 / §14.3 |
| **Jul 14, 2026** | **Unified equal-slot sizing** — ORB + Trendline share `0DTE_SLOT_COUNT=10` · sacred buffer + `tradable/N` · `TOTAL_0DTE_MAX_POSITIONS=10` · rank selects top-K · `0DTE_RANK_SIZING_ENABLED=false` by default · `compute_0dte_capital_snapshot` shared |
| **Jun 30, 2026 (b)** | Cross-link **no-progress 60 min** + ORB profile routing fix — layer 8 note · [NoProgressTimeoutImprovementGuide](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/NoProgressTimeoutImprovementGuide.md) · [0DTEProfitCapture.md §15.6](0DTEProfitCapture.md#156-jun-30--no-progress-timeout--profile-routing-tsla-430c) |
| **Jun 30, 2026** | **Unified Account Risk (UAR / P4) documented** — `daily_loss_halt` · account-scoped day P&amp;L from demo ledger · EOD + daily-open reset · distinct from Portfolio Protection observe-only · §8 added |
| **Jun 29, 2026** | **Canonical ORB1 Breakout stack frozen** — Convex v2.5 + **`REGIME_ORB_MULT=1.0`** + PS **≥ 0.10** — matrix baseline established |
| **Aug 6, 2026** | **PS floor raised to ≥ 0.30** under PS v2.1 — skips SBUX/RNG/ASTS/KTOS/RKLB/CSCO junk; keeps lowest wins (≥0.348) |
| **Aug 6, 2026** | **pc_oi observe all paths** — `GATE_ORB/TL/BO_pc_oi=observe` (stamp only); hard focus on dir% · MACD · vol/VWAP/OTM path filters · keep collecting skew data |
| **Aug 6, 2026** | **E16 ORB QG** (E15 opportunity + mild soft-neg deadband) · **PS v2.1** · was E15:  — dir **>0.54** · vol **0.12** · orb **≤8%** · MACD/pc **observe** · **BO beginner** vol **≥1.35** · dir **≥0.55** · MACD observe · OTM **≤3.5%** · BO `asset_stop` **−0.8%** · [ORB guide E15](Improvement%20Guide/ORB0DTEImprovementGuide.md) · [Breakout #20](Improvement%20Guide/BreakoutImprovementGuide.md) · [Exit Opt §5.10.16](0DTE_Exit_Optimization.md) |
| **Aug 5, 2026** | **ORB Quality Gate lock** — E14 OFF; Layer C pc_oi/dir%; E10/E11/E7/chase/PUT-wide; MACD eps **0.50** + ORB-only deadband quality (`ORB≤1.0` · `|macd|≥0.60` or `vol<0.20`); vol floor **0.12** (QG + Hard Gate); `admit_pre_exec` owns ORB admit (ConvexEligibilityFilter shim) |
| **Jun 26, 2026 (b)** | Convex pc_oi-only docs (superseded) · `convex_chain_skew.py` broker enrich |
| **Jun 26, 2026** | **PP execution-queue + observe-only** — PP on `_pending_dte0_signals` (`path=0dte_execution_queue`) · single execution rank · `PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false` in `Risk.env` |
| **Jun 26, 2026** | **PP hardening (local)** — GCS Step-5 marker · `portfolio_protection_step5.py` + `portfolio_protection_gcs.py` · `raw_volume_ratio()` (no default to 1.0) · `PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT=80` in `Risk.env` · `ensure_pp_ready_before_execution()` before 0DTE · `tests/test_live_portfolio_protection_gates.py` |
| **Jun 25, 2026** | **PP parity unification** — one Step 5 evaluation in FINAL + RESEARCH · retired SO ∪ 0DTE pool · `live_0dte_slice` |
| **Jun 24, 2026 (b)** | **Deployed true-to-live executable pricing** — `modules/option_executable_pricing.py` · BUILD **`00400-jun24-0dte-sizing-alert-fix`** |
| **Jun 24, 2026 (a)** | Tier arming on 0DTE zero-fill · **`0DTE_MAX_EXECUTION_CANDIDATES=10`** aligned with LIVE *(slice workaround superseded Jun 25)* |
| **Jun 23, 2026** | **Deploy `00386-wdh`** — Hard Gate before rank · caps **10/10/20** · `max_position_sum_usd` sizing · alert HTML hardening |
| **Jun 22, 2026** | **Monitor mark fidelity (local)** — chain-first Trendline quotes · shared cache `update_positions_with_real_prices` · ORB/Trendline registry split · §15.3 |
| **Jun 18, 2026** | **Phase 2 config trim** — `Risk.env` reduced to §A ceilings · §C ORB SO `STEALTH_*` · §D slip guard (3 keys); VAR/stress/regime removed · PP keys in `Risk.env` §B |
| **Jun 18, 2026** | **Rev Jun 18 (local)** — Jun 18 PP day desk · **IWM post-fill tail** · cross-link [Stop loss guide](../../1.%20The%20Easy%20ORB%20Strategy%20FINAL%20APP/docs/Improvement%20Guide/0DTEStopLossImprovementGuide.md) · §11 Demo/Live profit-capture parity |
| **Jun 15, 2026** | **Telemetry hygiene** — `MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES` (**400**) · GCS lifecycle namespaced snapshots + Trendline prefixes **40d** · ORB `execution_payload` / `EXECUTION_FILL_SUMMARY` |
| **Jun 12, 2026** | **Rev Jun 12** — Trendline **Phase 2 Priority Gate** (`trendline_emit_gates.py`: follow-through · material-break score · emit break floor · index above-ORB PUT block) · **7:30 bar merge preservation** (geometry parity DEMO/LIVE) · `_fetch_spy_vix_for_portfolio_protection()` · Gate **3b REDUCE → 0DTE max deploy** · **`EXECUTION_MARKET_CONTEXT`** · Trendline **FIX-7** micro-volume gate |
| **Jun 11, 2026** | Jun 11 grind desk on **`00370-trm`** — Gate **3b REDUCE** @ 50% · **0** ORB1 Breakout fills (**WULF** selector fail) · PO snapshot **`vix_level: 15.0`** artifact = daily default (real VIX **~21.3** @ Trendline executes) |
| **Jun 8, 2026 (deploy)** | **`00367-gsd`** on Cloud Run — Trendline **10/10** · combined cap **25** · Red Day gate · Execution Risk Layer · Live exit hardening |
| **Jun 8, 2026** | **Complete rewrite** — single go-to for 0DTE risk: full Portfolio Protection §3 (Patterns, Protection Day A/B, gates 3a–3c, tier execution, Convex collateral, config, greps) |
| Jun 8, 2026 | **`dte0_execution_risk_manager.py`** · Trendline Red Day gate · combined cap 25 |
| [Jun 8, 2026](doc_elements/Sessions/2026/Jun%208%20Session/SESSION_SUMMARY_JUN08_2026.md) | Initial extract from legacy `Risk.md` + [ORB0DTEPositionSizingAudit.md](ORB0DTEPositionSizingAudit.md) |
| Rev 00374 | SO-mirror rank · Gate 3c · cap **10** · Hard Gate before rank |
| May 18, 2026 | Convex PUT Red Day floor 0.62 |

---

*Post-fill exits: [0DTEProfitCapture.md](0DTEProfitCapture.md). Three-path PP session replay & desk log: [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md). ORB SO sizing: [SORisk.md](SORisk.md).*

## Oct 13 2026 — Trendline global daily slot fast path (deployed `easy-etrade-strategy-00599-lhd`)

`_trendline_try_claim_global_daily_slot(symbol, local_open)` claims markers at `{ns}/{date_PT}/{mode}/slot_NN.txt` with create-if-absent semantics. The markers are released on close, fail, or EOD.

Old behaviour: every claim scanned slots 1..cap in order. When the unified risk manager is on, the in-process open-cap check is skipped. So once TL was full, each signal cost `cap` failed GCS creates. On Oct 13 that was 184 rejects × 7 ≈ 1.3k Class A ops, about $0.006/day. At cap 50 it would be about 9.2k ops/day (≈ $0.05/day) plus up to 50 sequential round-trips of latency per signal.

New behaviour (`TRENDLINE_GLOBAL_DAILY_CAP_FAST_PATH=true`):
- If the in-process TL open count is at or above the cap, the claim is rejected with zero GCS calls (`stage=global_cap_fast_reject`).
- Otherwise probing starts at slot `local_open+1` and wraps around, so every slot is still tried once.

Restart recovery is unchanged:
- The GCS markers are still the cross-instance and restart source of truth.
- A restarted instance with a lost ledger (`local_open=0`) still scans all slots and rejects when every marker is held.

Setting the flag to `false`, or omitting `local_open`, restores the legacy 1..cap scan. Tests: `tests/test_trendline_global_slot_fast_path.py`, which includes a restart simulation.

## Oct 13 2026 — TL slot claim after Trade Profile admit (SINGLE_TP_ADMIT, deployed `easy-etrade-strategy-00599-lhd`)

With `SINGLE_TP_ADMIT=true` the §8.5 order for Trendline becomes: dedupe → Red Day → caps precheck → Trade Profile admit → **buffer/cash + GCS slot as one step** (`modules/tl_slot_admit.py`) → `evaluate_intent` → sizing → Execution Risk Layer → broker. Sizing is unchanged (§5.3: entry_budget = min(1/N tradable capped at $600, cash − buffer)) except that in-flight TL holds (claimed, not yet filled) are subtracted from available cash. Rejected signals use no GCS ops and hold no cash. Flag off: unchanged. Deviation flagged: the TL-local hold overlaps UAR pending reservations (§8.6); UAR's pending total is not yet read by the TL sizing snapshot.

## Oct 13 2026 — canonical spread rule (SINGLE_TP_ADMIT, deployed `easy-etrade-strategy-00599-lhd`)

Flag off (rollback; live through `00598`): ORB1 8 % (opening selector classes 9/10/12, hard 20), Trendline **and Breakout** 30 % (`TRENDLINE_OPTION_MAX_SPREAD_PCT=0.30`; BO reuses the TL executor), QG `spread` gate skips at admit (no contract yet). Flag on: one rule, per path — spread % of mid ≤ ORB1 **8** / Breakout **30** / Trendline **30** (`0DTE_MAX_SPREAD_PCT_ORB1` / `_BREAKOUT` / `_TRENDLINE`; Breakout now has its own explicit key instead of inheriting the TL cap; ORB1 opening-class 9/10/12 variants retired to 8) **or** ask−bid ≤ `0DTE_SPREAD_CHEAP_MAX_USD` (default $0.01), applied in the shared picker and re-checked at submit. Exit-grade spread (`OPTION_STEALTH_EXIT_GRADE_MAX_SPREAD_PCT`), `EXEC_LAST_LOOK_MAX_SPREAD_PCT`, Hard Gate 5 % and Skip-Signal chain-spread stamps are out of scope and unchanged. Review: `session_monitor/spread_1013/SPREAD_REVIEW.md`.
