# Alert System User Guide
## Easy ORB Strategy - Complete Alert System Documentation

**Last Updated**: August 17, 2026 — Exit **`Moon: ⭐️`** when realized P&L **> +200%** · Fill **and** exit **`Liq Cap: ⚖️`** when Liquidity Guard size-downs · Compact rejects · **TL0–TL3** trade titles.
**Version**: Rev **Aug 11** — reject emoji strip. Prior **Aug 10** watch-mode alert contracts. Prior **Aug 9** strategy consolidation. Prior **Jul 31** singles exit taxonomy. Prior **Jul 24** Breakout fill/close aligned with Trendline.  
**Purpose**: User guide for the **RESEARCH Mode app** (`1. The Easy ORB Strategy RESEARCH APP`): **unified 0DTE ORB + Trendline + Breakout** on one options ledger. Includes Telegram setup and alert types.

**⚠️ Note**: For sensitive deployment-specific information (Telegram bot tokens, chat IDs), see [PrivateSecrets.md](PrivateSecrets.md). For service URLs and project-specific cloud data, see [CloudSecrets.md](CloudSecrets.md).

---

## 📋 **Table of Contents**

1. [Alert System Overview](#alert-system-overview)
2. [Telegram Setup Guide](#telegram-setup-guide)
3. [Shared pipeline alerts (morning + protection)](#shared-pipeline-alerts-morning--protection)
4. [0DTE Strategy Alerts (Easy ORB 0DTE)](#0dte-strategy-alerts)
5. [Easy Trendline 0DTE Alerts](#easy-trendline-0dte-alerts)
6. [Easy Breakout 0DTE Alerts](#easy-breakout-0dte-alerts)
7. [OAuth System Alerts](#oauth-system-alerts)
8. [Alert Configuration](#alert-configuration)
9. [Daily Alert Flow](#daily-alert-flow)
10. [Alert Formatting](#alert-formatting)
11. [Troubleshooting](#troubleshooting)

---

## 🚨 **Alert System Overview**

The **RESEARCH app** sends Telegram alerts for **one unified 0DTE options account** (ORB + Trendline + Breakout paths) plus shared pipeline + OAuth. Desk banner: **`🏁 RESEARCH APP 🏁 RESEARCH APP 🏁`**.

1. **Easy ORB 0DTE (ORB1 path)**: Rolling **per-fill and per-reject** via `send_strategy_options_execution_alert("orb1")`, durability follow-ups, per-position exits via `send_strategy_position_exit_alert`. Titles use **`🔮 Breakout Order Filled`** / **`🔮 Breakout Order Closed`** (historical ORB1 branding — not ORB2 Breakout). **Watch default:** rejects use the same single-emit contract as ORB2/TL — **not** the legacy 7:30 batch “Easy ORB 0DTE pipeline” funnel. **Fill context (Aug 12):** optional **`Liq Cap:`** when Liquidity Guard size-downs. **Exit (Aug 17):** optional **`Moon: ⭐️`** when realized premium PnL **> +200%**. **Reject copy (Aug 11):** `⚠️ SYMBOL CALL|PUT` without trailing 🟢/🔴 · `Reason:` bare code (`bid_size_too_small`, …) · no diagnostics block. **QG reject (Aug 12):** `Stage: quality_reject` · `Reason: {gate_id}:{reason_token}` ([0DTEQualityGates.md §3.2](0DTEQualityGates.md#32-path-opt-hard-cutover--reject-reason-review-aug-12)). Batch `send_options_execution_alert` retained for **`ORB_0DTE_DISCOVERY_MODE=batch`** rollback only.
2. **Easy Trendline 0DTE**: Fill/exit alerts (wrappers → shared strategy contract), optional monitor heartbeats. Headers use **`📏 Trendline TL0|TL1|TL2|TL3 Order Filled`** / **`… Closed`** (`_trendline_slot_alert_label`; missing slot defaults **TL1**; mixed-slot batch may omit the slot). **TL0 = TL1–TL3 for trade Telegrams** (entry + exit + post-exec reject). **No Telegram for TL0 geometry build** (same as no separate TL1-build alert). Same Aug 12 **`Liq Cap:`** / Aug 17 **`Moon: ⭐️`** (> +200%) lines as ORB1/BO. Post-exec reject → **1h** cooldown on **symbol + TL slot + side**. Hard **QG / reclaim** rejects also Telegram (`quality_reject` stage). Same Aug 11 MQ reject copy as ORB1/BO. See [0DTETrendline.md](0DTETrendline.md).
3. **Easy Breakout 0DTE (ORB2 path)**: Fill/exit alerts. Headers use **`⛓️ Breakout Order Filled`** / **`⛓️ Breakout Order Closed`**. Same contract-line body as Trendline (no Line Geometry). Same Aug 12 **`Liq Cap:`** / Aug 17 **`Moon: ⭐️`** (> +200%) · Aug 11 MQ reject copy + Aug 12 **QG** `quality_reject` Reasons. Post-exec / QG reject → **1h** cooldown on **symbol + side**.
4. **Combined scheduled EOD**: **One** Telegram at 12:55 PM PT — **`🏦 END-OF-DAY ORB 0DTE`** (`send_scheduled_unified_0dte_eod`) — combined account P&L (ORB + Trendline + Breakout on one ledger).
5. **Shared morning pipeline**: Good Morning, **0DTE ORB1 Capture**, **TL0** geometry build (≥06:45 · **no build Telegram**; TL0 **fills/closes still alert**), **ORB1 Breakouts** (06:50–07:10), **0DTE ORB2 Capture** (≥07:30) → TL1 build → TL/BO unlock. ORB SO / Signal Collection **paused**.
6. **OAuth**: Token expiry and renewal.

**Retired / paused on RESEARCH desk:** ORB SO **Standard Order Execution** + SO Signal Collection path **paused**; SO ETF EOD skipped where wired (`skipped:so_etf_retired`). **`send_rapid_exit_alert`**, **`send_options_health_check_alert`** removed. **Active risk alerts:** Portfolio Protection, Red Day (collection validation), post-fill premium stealth exits. See [LiveModeReadiness.md](../LiveModeReadiness.md).

### **Core Features**

- **Multi-Channel Delivery**: Telegram notifications with HTML formatting
- **Rich Formatting**: Emoji-enhanced messages with structured data
- **Dual Timezone Support**: Morning, OAuth, and Portfolio Protection display PT and ET under the title. **0DTE ORB Capture Complete** and **scheduled unified 0DTE EOD** (12:55 PM PT) place **`📅 Report Date:`** + **`Time:`** at the **bottom** (Rev **Jun 11** / **00381**). **Execution** and **Closed** trade alerts omit the header **`Time:`** row (Rev **00380**). Any alert that shows **`{mode} Mode`** puts it on an **indented second line** after the title row (Rev **Jun 11** — `_alert_mode_header_line()`).
- **Dollar formatting (Rev 00380 / 00381)**: Trade and EOD alerts format USD with thousands separators via `_fmt_usd` / `_fmt_usd_signed` (e.g. `$2,305.50`, `+$11,614.50`). EOD **Profit Factor** uses `_fmt_profit_factor` / `pf_str` when values are large.
- **Intelligent Throttling**: Prevents alert spam while maintaining critical notifications
- **Source Tracking**: Clear identification of alert source
- **Trade ID Formatting**: Shortened trade IDs for cleaner format
- **Enhanced Execution Alerts**: Bold formatting for key metrics
- **0DTE execution funnel** (Rev 00318): Legacy batch Telegram **0DTE pipeline (this run)** block — **paused** in watch mode; Cloud grep **`SO_PIPELINE`** / **`0DTE_DEMO | synthetic_chain`** for rollback path only

### **Alert Delivery**

All alerts are delivered via **Telegram** using HTML formatting. The system supports:
- Real-time trade notifications
- System status updates
- Performance summaries
- System alerts via `send_system_alert()` or `send_telegram_alert()`
- OAuth token management alerts

### **Alert Pipeline (canonical — Aug 9, 2026)**

Every Telegram payload follows **one send path** in `modules/prime_alert_manager.py`:

```
Formatter (send_* / send_strategy_* method)
    → _send_telegram_message()
        → RESEARCH: _prepare_research_app_telegram_message()  # HTML + 🏁 banner
        → FINAL:    _prepare_telegram_message_for_send()      # HTML only (no banner)
        → truncate (Telegram limit)
        → Telegram HTTP API (HTML; plain-text fallback on parse error)
```

| Layer | RESEARCH app | FINAL (Live) app |
|-------|--------------|------------------|
| **Desk banner** | ✅ `🏁 RESEARCH APP 🏁` | ❌ Not injected |
| **Mode line** | `{mode} Mode` via `_alert_mode_header_line()` | Same |
| **Trade entry/exit** | `send_strategy_*` shared contracts | Same |
| **OAuth midnight expiry** | `ETradeOAuth/login/oauth_backend.py` (direct Telegram) | Same |

**Removed (Jun 25 cleanup):** `send_error_alert`, `send_warning_alert`, `send_rapid_exit_alert`, `send_options_health_check_alert`, SO ETF exit/EOD formatters, superseded 0DTE collection/orb helpers, `enhanced_oauth_alerts.py` stub.

---

## 📱 **Telegram Setup Guide**

### **Step 1: Create a Telegram Bot**

1. Open Telegram and search for **@BotFather**
2. Start a conversation with BotFather
3. Send the command: `/newbot`
4. Follow the prompts to:
   - Choose a name for your bot (e.g., "Easy ORB Strategy Alerts")
   - Choose a username for your bot (must end in `bot`, e.g., `easy_orb_alerts_bot`)
5. BotFather will provide you with a **Bot Token** (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
6. **Save this token** - you'll need it for configuration

### **Step 2: Get Your Chat ID**

1. Open Telegram and search for **@userinfobot**
2. Start a conversation with @userinfobot
3. The bot will reply with your **Chat ID** (e.g., `123456789`)
4. **Save this Chat ID** - you'll need it for configuration

**Alternative Method** (if @userinfobot doesn't work):
1. Create a group chat or use an existing one
2. Add your bot to the group
3. Send a message in the group
4. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
5. Look for the `"chat":{"id"` field in the response
6. Use the **negative number** (e.g., `-123456789`) for group chats

### **Step 3: Configure Telegram Credentials**

#### **For Local Development**

1. Navigate to `secretsprivate/` directory
2. Copy `telegram.env.template` to `telegram.env`
3. Edit `telegram.env` and add your credentials:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   TELEGRAM_ENABLED=true
   ```
4. **Important**: `telegram.env` is gitignored - never commit it to version control

#### **For Cloud Deployment (GCP)**

Store credentials in Google Cloud Secret Manager:

```bash
# Store bot token
echo -n "your_bot_token_here" | gcloud secrets create telegram-bot-token --data-file=-

# Store chat ID
echo -n "your_chat_id_here" | gcloud secrets create telegram-chat-id --data-file=-
```

The system will automatically load these credentials in production.

### **Step 4: Test Your Setup**

1. Start the trading system
2. You should receive a test alert or the Good Morning alert
3. If you don't receive alerts, check:
   - Bot token is correct
   - Chat ID is correct
   - Bot is not blocked
   - System logs for errors

### **Step 5: Configure Alert Preferences**

Edit `configs/Alerts.env` (Telegram only — **7 keys** after Phase 2 trim, Jun 18):

```bash
ALERTS_ENABLED=true
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_ENABLED=true
TELEGRAM_MAX_MESSAGES_PER_MINUTE=20
ALERT_COOLDOWN_SECONDS=60   # canonical key (code reads ALERT_COOLDOWN_SECONDS)
TELEGRAM_BOT_TOKEN=           # empty here — use secretsprivate/telegram.env locally
TELEGRAM_CHAT_ID=
```

**Alert level toggles** (`ALERT_LEVEL_*`) live in **`configs/System.env`**, not `Alerts.env`. OAuth renewal schedule keys remain in secrets / runtime — not in `Alerts.env`.

### **Troubleshooting Telegram Setup**

**Issue**: No alerts received
- ✅ Verify bot token is correct
- ✅ Verify chat ID is correct
- ✅ Check that bot is not blocked
- ✅ Review system logs for errors
- ✅ Test bot manually: `https://api.telegram.org/bot<TOKEN>/getMe`

**Issue**: Alerts received but formatting is broken
- ✅ Check that HTML formatting is enabled (default)
- ✅ Verify message doesn't contain invalid HTML
- ✅ System will auto-fallback to plain text if HTML fails

**Issue**: Too many alerts (spam)
- ✅ Adjust `TELEGRAM_MAX_MESSAGES_PER_MINUTE` in `Alerts.env`
- ✅ Increase `ALERT_COOLDOWN_SECONDS` (default **60**)
- ✅ Review `ALERT_LEVEL_*` toggles in `System.env`

---

## 📊 **Shared pipeline alerts (morning + protection)**

Alerts below are **shared infrastructure** for ORB capture and (when enabled) 7:30 signal collection / portfolio protection. **Default desk (Aug 7+):** ORB SO + Signal Collection + PP **paused** — morning Telegrams are ORB1/ORB2 capture + watch-mode fills/rejects. Legacy SC/PP sections remain for rollback.

### **Retired ORB SO ETF alerts (Live app only)**

| Alert | Method | Live app |
|-------|--------|----------|
| Standard Order Execution | `send_orb_so_execution_aggregated` | ❌ Removed — SO ETF path retired |
| SO position closed / aggregated exit | `send_trade_exit_alert`, `send_aggregated_exit_alert` | ❌ Removed — use `send_options_*` exits |
| ETF EOD (`🛂 END-OF-DAY REPORT`) | `send_end_of_day_report` / `_send_*_eod_summary` | ❌ Removed — `main.py` → `skipped:so_etf_retired` |

Full SO ETF alert history remains in **DEMO Mode** app docs.

### **Active risk-management alerts (0DTE + shared pipeline)**

| Alert | Method | When |
|-------|--------|------|
| Portfolio Protection @ 7:30 | `send_portfolio_protection_alert` | Gate **3a** or **3b** `SKIP_EXECUTION` @ Step 5; Gate **3c** PS floor (agreed **≥ 0.10** ★) + 0 fills (`classify_0dte_zero_execution_outcome`) |
| Red Day (collection path) | `send_red_day_alert` | **`_signal_collection_validation_blocked`** + `zero_signals_reason` — **not** canonical Gate **3a** (use §4 instead) |
| Post-fill exits | `send_options_position_exit_alert`, `send_trendline_position_exit_alert` | Premium stealth closes (includes rapid-exit reasons — no separate `send_rapid_exit_alert`) |

### **1. Good Morning Alert (5:30 AM PT / 8:30 AM ET)**

**Trigger**: Cloud Scheduler at 5:30 AM PT daily  
**Method**: `send_oauth_morning_alert()`  
**Purpose**: Broker token validation, live-data restore for engines skipped at boot, multi-broker readiness block, and morning schedule  
**Content**:
- **`🔐 Broker:`** block — E*TRADE production token (metadata + live API probe when applicable); **Interactive Brokers** and **TastyTrade** shown as **`❏ n/a`** until wired
- **`💎 Status:`** — **`Demo = Ready`** / **`Live = Data Only`** on valid-token path; renew-now / critical copy on invalid paths
- **`📊 Trading Schedule:`** — **ORB1 Capture** 6:30–6:45 AM PT · **TL0 Discovery** 6:45–7:10 AM PT · **ORB1 Breakouts** 6:50–7:10 AM PT · **ORB2 / TL1 unlock** ≥7:30 AM PT (no TL0 build Telegram; discovery **24/10s**; no SC/7:30 batch)
- OAuth dashboard link

**Variants**: Valid metadata + live API · metadata valid but API rejected · invalid/expired (see DEMO [Alerts.md](../1.%20The%20Easy%20ORB%20Strategy%20RESEARCH%20APP/docs/Alerts.md) §1 for full example blocks).

**Features**:
- Time validation: Only sends between 5:30-5:35 AM PT
- Deduplication: One alert per day maximum (GCS-based)
- **No `{mode} Mode` suffix** on this alert

### **2. 0DTE ORB1 Capture Complete (6:45 AM PT / 9:45 AM ET)**

**Trigger**: After ORB#1 capture completes (6:30–6:45 AM PT)  
**Method**: `send_orb_capture_complete_alert`  
**Purpose**: Confirms opening ranges captured for the **0DTE watchlist universe** (ORB1 Breakouts / **TL0** / later TL1+ / Breakout share this ORB#1 freeze). **TL0** dual-side draw may run right after this stamp — **no build Telegram** (TL0 **Order Filled / Closed** use the same contracts as TL1–TL3).

**Watchlist:** Single file — **`data/watchlist/0dte_list.csv`** (**90** data rows + header typical). Loaded via `watchlist_loader.load_watchlist_symbols()` → `easy0DTE.modules.prime_0dte_strategy_manager.load_0dte_symbols()`.

**Count validation:** Alert logs a warning if **symbols captured** ≠ watchlist size. Full success → captured = active = watchlist size.

**Content**:
- **Mode** on its own line (`| DEMO Mode` or `| LIVE Mode`)
- Single **0DTE ORB1 Capture** block (no separate SO “Opening Range” section — SO/SC paused)
- **ORB1 Window** + **Next: ORB1 Breakouts** (06:50–07:10 PT)
- **`📅 Report Date:`** + **`Time:`** at the **bottom**

**Example**:
```
====================================================================

✅ <b>0DTE ORB1 Capture Complete</b>
          | DEMO Mode

✅ Status: All opening ranges captured successfully!

🔮 <b>0DTE ORB1 Capture:</b>
          • <b>Symbols Captured:</b> 90
          • <b>Active Symbols:</b> 90
          • <b>Capture Duration:</b> 3.0 seconds

⚛️ <b>ORB1 Window:</b>
          6:30 - 6:45 AM PT (9:30 - 9:45 AM ET)
🎯 <b>Next:</b> ORB1 Breakouts
          6:50 - 7:10 AM PT (9:50 - 10:10 AM ET)

📅 <b>Report Date:</b> 2026-08-07
          Time: 06:45 AM PT (09:45 AM ET)
```

### **2b. 0DTE ORB2 Capture Complete (≥7:30 AM PT / 10:30 AM ET)**

**Trigger**: After ORB#2 session H/L freeze completes (06:30–07:30 PT box, stamped ≥07:30)  
**Method**: `send_orb2_capture_complete_alert`  
**Purpose**: Confirms day-locked ORB#2 levels for **ORB2 Breakout** + **Trendline TL1** unlock. Telegram **Next** window still prints **7:30–11:30 AM PT** (legacy string in `send_orb_capture_complete_alert`); path cutoffs are **12:30 PT**.

**Dedup:** Once per day via `_orb2_capture_alert_sent_today` + `daily_markers.orb2.alert_sent` (`mark_orb2_alert_sent`).

**Example**:
```
====================================================================

✅ <b>0DTE ORB2 Capture Complete</b>
          | DEMO Mode

✅ Status: All opening ranges captured successfully!

🔮 <b>0DTE ORB2 Capture:</b>
          • <b>Symbols Captured:</b> 90
          • <b>Active Symbols:</b> 90
          • <b>Capture Duration:</b> 3.0 seconds

⚛️ <b>ORB2 Window:</b>
          6:30 - 7:30 AM PT (9:30 - 10:30 AM ET)
🎯 <b>Next:</b> ORB2 Breakout and Trendline Options
          7:30 - 11:30 AM PT (10:30 AM - 2:30 PM ET)

📅 <b>Report Date:</b> 2026-08-07
          Time: 07:30 AM PT (10:30 AM ET)
```

### **3. Trade Signal Collection (7:30 AM PT / 10:30 AM ET)** — **PAUSED (rollback only)**

**Status (Aug 7+):** `ENABLE_ORB_SIGNAL_COLLECTION=false` — this alert does **not** fire on the default watch desk. Content below documents the legacy batch path when `ORB_0DTE_DISCOVERY_MODE=batch` + SC re-enabled.

**Trigger**: After signal scan + 7:30 cutoff revalidation (legacy)  
**Method**: `send_so_signal_collection` (legacy method name — **not** SO ETF orders in Live app)  
**Purpose**: Single morning funnel alert for **0DTE paths only** in the Live app: Convex/Hard Gate pipeline counts + CALL/PUT collection lists.

**Live app (Rev Jun 17)**: No **`📑 ORB Breakouts Ready`** / **`📑 Standard Orders Ready`** section and no **`ORB Signals Generated`** results line. SO ETF collection (`_pending_so_signals`) is not built or persisted. Priority rank for ORB 0DTE uses **`_live_0dte_rank_pool()`** — CALL rows from **`_pending_dte_signals`**.

**Watchlist order**: Display order follows **`0dte_list.csv`** row order.

**Header (Rev Jun 11; mode Rev Jun 18; bar Rev Jun 23)**: Live app **`send_so_signal_collection()`** prepends **`====================================================================`** + **one blank line** before **`🪽 Trade Signal Collection`**. **No** desk banner — RESEARCH sibling injects **`🏁 RESEARCH APP 🏁`** via **`_inject_research_app_alert_banner()`**. Title → **`{mode} Mode`** on indented second line via **`_alert_mode_header_line()`**; **`Time:`** on the row below. Mode from runtime **`MODE`** via **`_resolve_telegram_mode_label()`** (e.g. soak deploy on **`easy-etrade-strategy-live`** with **`MODE=demo`** shows **`| DEMO Mode`**).

**Content**:
- **Symbols scanned** — unified watchlist count (`0dte_list.csv`)
- **ORB Signals Generated** — included when **`MODE=demo`** (or any non-`LIVE` trading mode on the Live tree); omitted when **`MODE=live`** (`live_only_0dte` — 0DTE-only Results block, no Breakouts list)
- **0DTE options ready** — **`🔮 0DTE Options Ready`** with **CALL:** and **PUT:** symbol lines; pipeline counts on multi-line **`0DTE Pipeline`** block
- **Zero signals**: optional `zero_signals_reason` diagnostic
- **Red Day @ collection**: may replace this alert with **`RED DAY DETECTED`** (see §8b)

**Live @ 7:30 order (Rev Jul 30)**: ORB rank + deferred Convex/Hard Gate + **Signal Collection alert** → **Step 5 PP** → ORB 0DTE execution (ORB1) → **ORB2 capture** → **TL1 geometry build (ORB2)** → Trendline **watch/emits** + Breakout activate. Grep **`path=0dte_collection_ranked`** · **`ORB_RANK | gate_3a_3b=deferred`** · **`path=step5_gate_3a`** · **`stage=orb2_then_tl1`**.

**Example (Live app — `MODE=demo` soak; no DEMO banner)**:
```
====================================================================

🪽 <b>Trade Signal Collection</b>
          | DEMO Mode
          Time: 07:31 AM PT (10:31 AM ET)

Signal collection finished, ordered by watchlist priority…

📊 <b>Results:</b>
          • <b>Symbols Scanned:</b> 78
          • <b>ORB Signals Generated:</b> 22
          • <b>0DTE Options Signals:</b> 23
             CALL = 22, PUT = 1
          • <b>0DTE Pipeline:</b> collection=23
             convex=6, hard_gate=6
             queued_exec=6

🔮 <b>0DTE Options Ready</b> (23 symbols):
          <b>CALL:</b> SPX, SPY, IWM, RUT, IBIT, AMZN, FDX, HD, CEG, MU, DELL, VST, NBIS, CRWV, OKLO, SOFI, BE, CLSK, CIFR, OPEN, WULF, IREN
          <b>PUT:</b> NFLX

📡 <b>Signal Window:</b>
          7:15-7:30 AM PT (10:15-10:30 AM ET)
🚀 <b>Next:</b> ORB Options Execution
          7:30 AM PT (10:30 AM ET)
```

**Example (`MODE=live` — 0DTE-only body)**: Same header bar layout; omit **`ORB Signals Generated`** line and **`📑 Breakouts Ready`** block; **`{mode} Mode`** shows **`| LIVE Mode`**.

### **4. Portfolio Protection Alert (7:30 PT — pre-fill skip)**

**Title**: three-line header — **`🛡️ PORTFOLIO PROTECTION`** → **`| SKIP TRADING`** → **`| {mode} Mode`** (`_portfolio_protection_alert_header()`)
**Method**: `send_portfolio_protection_alert` in `prime_alert_manager.py`

**When sent (canonical Portfolio Protection — Gates 3a / 3b `SKIP_EXECUTION`):**

| Gate | Evaluator | `trigger_id` values |
|------|-----------|---------------------|
| **3a** Unified batch | `evaluate_portfolio_protection()` on **0DTE protection pool** (priority-ranked Signal Collection) | `red_day_pattern_1` · `red_day_pattern_2` · `red_day_pattern_3` · **`protection_day`** · **`protection_day_split_tape`** |
| **3b** Composite risk | `_run_gate_3b_composite_risk()` inside `_run_portfolio_protection_gates_at_step5_before_execution()` | **`enhanced_red_day_skip_execution`** |
| **3c** Zero-PS (zero-fill backup) | `filter_dte0_signals_below_min_priority_score()` | **`portfolio_protection_zero_ps`** |

**0DTE protection pool:** `_build_0dte_protection_pool_enriched()` → `build_ranked_dte_collection_list()` on `_pending_dte_signals` (CALL + PUT collection rows); watchlist fallback when pool &lt; 3 symbols. **No** SO ETF rows · **no** `build_live_protection_pool()` union.

**Wiring (one evaluation per PT day — `_portfolio_protection_alert_sent_today`):**

| Path | When | Log token |
|------|------|-----------|
| **Step 5 Gate 3a** (canonical) | `_run_portfolio_protection_gates_at_step5_before_execution()` after collection alert | `path=step5_gate_3a` · `path=0dte_collection_ranked` |
| **Step 5 Gate 3b** | Same helper when composite risk → `SKIP_EXECUTION` | `path=step5_gate_3b` |
| **Zero-fill @ Step 5** | 0 ORB 0DTE fills + PP or Gate **3c** | `path=pre_0dte_execution_alert` |

**Does NOT send §4:**

| Situation | Why |
|-----------|-----|
| **Convex 3a preview only** | `_evaluate_portfolio_red_day_pattern()` on **ranked 0DTE collection** in `orb_shared_730_pipeline.py` — log-only PUT-floor hint · **no** flag · **no** Telegram |
| **Gate 3b `REDUCE_POSITION_SIZE`** | Logs `MEDIUM RISK - Reducing position sizes by X%` only — **no** SKIP TRADING (DEMO parity) |
| **Convex / Hard Gate / chain-only 0 fills** | Pipeline filters — not Portfolio Protection |
| **`_signal_collection_validation_blocked`** | Legacy **`RED DAY DETECTED`** (§7) — not §4 |

**Purpose**: Notify when **weak-day protection** is active. **`Reason`** from `PortfolioProtectionAssessment` (3a) or composite-risk / Gate **3c** summary.

**Evaluation order (Gate 3a — first match blocks):**

| `trigger_id` | When |
|--------------|------|
| `red_day_pattern_1` | ≥70% RSI &lt;40 **and** ≥80% weak volume |
| `red_day_pattern_2` | ≥80% RSI &gt;80 **and** ≥80% weak volume (MACD / RS vs SPY / VWAP overrides may clear) |
| `red_day_pattern_3` | ≥80% weak volume **and** ≥50% low/high RSI breadth |
| `protection_day` | ≥**50%** pool below VWAP **and** median `volume_ratio` **&lt; 0.45** |
| `protection_day_split_tape` | Median VWAP distance **&lt; 2%** **and** median `volume_ratio` **&lt; 0.45** (VWAP coverage ≥ **80%**) |

**On Gate 3a / 3b `SKIP_EXECUTION` block:**

- `_red_day_filter_blocked = True` (canonical PP — **not** validation failures)
- Optional post-block safety close via `_force_close_positions_on_red_day()` (included in alert when positions closed)
- **Telegram §4** — **`SKIP TRADING`** @ Step 5 (single canonical path)
- **`_refresh_0dte_pipeline_for_portfolio_red_day()`** — Convex PUT floor **0.62** before deferred pipeline

**On 0 ORB 0DTE fills @ Step 5:**

- Zero-fill PP (if not already sent) → **`🔮 Breakout Order Filled`** with `rejection_category=portfolio_protection` when applicable

**0DTE policy when `_red_day_filter_blocked`:**

- Tier 2+ **CALL** blocked at Step 5 + unified risk submit · **Tier 1 CALL + all PUT** may continue
- **Telegram Action copy (Rev Jun 18):** **`7:30 protection gate active`** · **`0DTE CALLs:`** indented — **`non–Tier 1 BLOCKED`** · **`Tier 1 + Puts continue`** (no ORB SO line — Live app has no SO ETF batch)

**Deduplication**:

- **§4** uses **`_portfolio_protection_alert_sent_today`** — **one** canonical **`SKIP TRADING`** Telegram per PT day @ 7:30
- Legacy **`RED DAY DETECTED`** (§7) uses **`_red_day_alert_sent_today`** — separate template; does **not** suppress §4

**Log greps (post-deploy)**:

```text
PORTFOLIO_PROTECTION_ALERT_SENT
PORTFOLIO_PROTECTION | path=0dte_collection_ranked
path=step5_gate_3a
path=step5_gate_3b
path=pre_0dte_execution_alert
ORB_RANK | gate_3a_3b=deferred
PORTFOLIO PROTECTION — EXECUTION SKIPPED
PORTFOLIO PROTECTION — 0DTE POLICY FLAG
enhanced_red_day_skip_execution
signal_collection_validation_blocked
classify_0dte_zero_execution_outcome
portfolio_protection_zero_ps
ORB_RANK_FOR_0DTE
0DTE_ZERO_EXEC_ALERT_SENT
0DTE_CONVEX_RED_DAY_REFRESH | after_pp_block
0DTE_CONVEX_RED_DAY_PREVIEW
protection_day
protection_day_split_tape
```

### **§4 Examples — Portfolio Protection (Protection Day replay)**

**Example — Protection Day (Trigger A, May 27 replay)**:

```
====================================================================

🛡️ <b>PORTFOLIO PROTECTION</b>
          | SKIP TRADING
          | LIVE Mode

🔍 Trigger: Protection Day
          protection_day

📌 Reason:
          Protection Day: 67% of pool below VWAP and median volume_ratio 0.31 < 0.45

📊 Pool metrics:
          • Oversold (RSI under 40): 29%
          • Overbought (RSI above 80): 5%
          • Weak volume (below 1.0x): 100%
          • Below VWAP: 67%
          • Median volume_ratio: 0.310
          • Median VWAP distance: -5.40%
          • Avg RSI: 58.2 | Avg vol: 0.42x
          • Avg MACD: 0.412
          • Avg VWAP dist: -2.15%

💰 Action:
          • 7:30 protection gate active
          • 0DTE CALLs:
              non–Tier 1 BLOCKED;
              Tier 1 + Puts continue

📊 Protection pool: 0DTE candidates 28 | Scanned <dynamic>
📋 0DTE pool (sample): GLD, GDX, SLV, NVDA, MRK, LLY, MRVL, MSTR, SMCI, NBIS, SNDK, KTOS
====================================================================
```

**Note:** Live app **does not** include **`ORB SO: BLOCKED`** or **`ORB pool (sample)`** — no SO ETF execution path.

**Example — Protection Day (Jun 18 desk, Trigger A + B)**:

```
🛡️ PORTFOLIO PROTECTION
          | SKIP TRADING
          | LIVE Mode

🔍 Trigger: Protection Day

📌 Reason:
          50% of pool below VWAP and median volume_ratio 0.34 < 0.45; median VWAP distance -0.54% < 2.00% …

📊 Pool metrics:
          • Oversold (RSI under 40): 50%
          • Overbought (RSI above 80): 0%
          • Weak volume (below 1.0x): 100%
          • Below VWAP: 50%
          • Median volume_ratio: 0.345
          • Median VWAP distance: -0.54%
          • Avg RSI: 44.1 | Avg vol: 0.35x
          • Avg MACD: 1.326
          • Avg VWAP dist: 28.03%

💰 Action:
          • 7:30 protection gate active
          • 0DTE CALLs:
              non–Tier 1 BLOCKED;
              Tier 1 + Puts continue

📊 Protection pool: 0DTE candidates 12 | Scanned 78

📋 0DTE pool (sample): GLD, GDX, SLV, NVDA, MRK, LLY, MRVL, MSTR, SMCI, NBIS, SNDK, KTOS
```

**LIVE mode line:** `| LIVE Mode` (indented second header via `_alert_mode_header_line()`).

**Example — Protection Day (Jun 15-style, Trigger A):**

```
🛡️ PORTFOLIO PROTECTION
          | SKIP TRADING
          | LIVE Mode

🔍 Trigger: Red Day Pattern 3

📌 Reason:
          100% of pool below VWAP and median volume_ratio 0.34 < 0.45; …

📊 Pool metrics:
          • Oversold (RSI under 40): 0%
          • Overbought (RSI above 80): 0%
          • Weak volume (below 1.0x): 100%
          • Below VWAP: 100%
          • Median volume_ratio: 0.340
          • Median VWAP distance: -8.72%
          • Avg RSI: 54.0 | Avg vol: 0.34x
          • Avg MACD: 0.108
          • Avg VWAP dist: -8.74%

💰 Action:
          • 7:30 protection gate active
          • 0DTE CALLs:
              non–Tier 1 BLOCKED;
              Tier 1 + Puts continue

📊 Protection pool: 0DTE candidates 13 | Scanned <dynamic>
📋 0DTE pool (sample): …
```

**Example — Split-tape Protection Day (Trigger B, May 26 replay, Rev 00375)**:

```
🔍 Trigger: Protection Day Split Tape
          protection_day_split_tape

📌 Reason:
          Protection Day: median VWAP distance 0.10% < 2.00% and median volume_ratio 0.32 < 0.45
```

**Reference**: [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md)

### **5. Retired: post-fill BAD DAY / rapid exit formatters (Live app)**

`send_options_health_check_alert` and `send_rapid_exit_alert` were **removed** Jun 25 (not wired in Live tree). ETF **BAD DAY / WEAK DAY** (15m health loop) is **RESEARCH app only** (SO stealth book). Live post-fill exits use premium stealth → **`send_options_position_exit_alert`** / **`send_trendline_position_exit_alert`**.

### **6. Legacy Red Day Alert (signal collection path)**

**Title**: **`🚨 RED DAY DETECTED`**  
**Method**: `send_red_day_alert` — **replaces** the normal Signal Collection alert in that path.

**Trigger**: During **0DTE Signal Collection** when **`_signal_collection_validation_blocked`** is set with a **`zero_signals_reason`** (validation candle empty, all-neutral, zero-signals validation).

**Not** fired for:

- Canonical **`_red_day_filter_blocked`** / Gate **3a** / **`protection_day`** — use §4 **`SKIP TRADING`** instead (`send_portfolio_protection_alert`)
- Convex **3a preview** log lines (`0DTE_CONVEX_RED_DAY_PREVIEW`) — preview only

**Also fires when** `red_day_blocked and red_day_reason` at collection time **if** the flag was already set before the collection alert branch (rare on Live; DEMO may set earlier).

**Note**: Canonical Portfolio Protection @ 7:30 → **`send_portfolio_protection_alert`** (§4) with explicit `trigger_id` and `path=step5_gate_3a`. §7 is for **validation/data** failures during collection.

**Example (legacy `send_red_day_alert` on LIVE — validation @ collection):**
```
====================================================================

🚨 <b>RED DAY DETECTED</b>
          | LIVE Mode
          Time: 07:30 AM PT (10:30 AM ET)

🔍 Red Day Reason:
          <zero_signals_reason / validation message>

💰 Action Taken:
          • ORB SO ETF batch: N/A (retired in Live app)
          • Signal collection: validation or early flag — not canonical Gate 3a
          • Canonical protection @ 7:30: SKIP TRADING alert (Gate 3a/3b)

📊 Signal Collection:
          • Symbols Scanned: …
          • Breakout pool: …
          • 0DTE Signals: …

💡 Strategy Impact:
          • 0DTE pipeline continues to 7:30 when candidates exist
          • Tier 1 CALL + all PUT allowed when protection flag active at Step 5

📊 Next: 0DTE options execution @ 7:30 PT
====================================================================
```

**Note:** `protection_day` and other Gate **3a** triggers use §4 **`SKIP TRADING`**, not §7.

### **8. Holiday / Pre-Holiday / Election Day Alert**

**Trigger**: Cloud Scheduler `oauth-market-open-alert` at 5:30 AM PT when `should_skip_trading()` is true — **instead of** Good Morning. Weekends are not scheduled.  
**Purpose**: One disabled-session notice; trading stays off all day.  
**Copy** (`_holiday_alert_copy` in `prime_alert_manager.py`):

| Reason | Emoji | Title |
|--------|-------|-------|
| `MARKET_CLOSED` | 🏖️ | `Holiday! - {name}` |
| `LOW_VOLUME` | 🎃 | `Holiday! - {name}` |
| `PRE_HOLIDAY` | 🛡️ | `Pre-Holiday Risk Session! - Day before {holiday}` |
| `ELECTION_DAY` | 🗳️ | `Election Day! - {name}` (`U.S. Presidential Election Day` or `U.S. Midterm Election Day`) |

Calendar rules and the 2026 skip table: [MarketCalendar.md](MarketCalendar.md).

**Example** (live format):
```
====================================================================

🏖️ <b>Holiday! - Christmas Day</b>
          Thursday, December 25, 2025

🎭 <b>No Trading Today!</b> ☁️🏖️🏝️⛱️🌤️☁️☁️

🚫 <b>Status:</b>
          Trading DISABLED today.

💡 <b>Why:</b>
          U.S. stock market is closed today. Trading disabled to preserve capital quality.

✅ <b>System Status:</b>
          Normal

🔍 <b>Next Trading:</b>
          System will resume at next normal trading day

🌐 <b>Dashboard:</b>
          https://easy-0dte.web.app
```

### **9. End-of-Day Reports (3:55 PM ET / 12:55 PM PT)**

**Trigger**: Cloud Scheduler **`end-of-day-report`** → **`POST /api/end-of-day-report`**. Baseline **12:55 PM PT** weekdays.  
**Before Telegram**: `flatten_all_paths_for_eod_scheduler()` — ORB 0DTE + Trendline (SO ETF path retired).  
**Deduplication**: GCS markers per report type.

**One scheduled Telegram summary** (Live app):

| # | Account | Method / header |
|---|---------|-----------------|
| 1 | **Unified 0DTE** (ORB + Trendline) | `send_scheduled_unified_0dte_eod` → **🏦 END-OF-DAY ORB 0DTE** |

**Retired in Live app:**
- ETF EOD **`🛂 END-OF-DAY REPORT`** — `main.py` → `skipped:so_etf_retired`
- Separate **ORB 0DTE EOD** + **Trendline EOD** pair — replaced by unified EOD (Jun 9)

**Content** (unified report — Rev **00381** + Jun 8 period-% fix + Jun 11 header layout): shared structure with:
- Header: **`🏦 END-OF-DAY ORB 0DTE | 🎮 🍒`** (demo) or **`| 💰`** (live), then **`| {MODE} Mode`** on the next indented line (Rev **Jun 11**)
- `📈 P&L (TODAY):` — bold primary **% + $** line; win rate; profit factor; averages; best/worst
- **Today/week %** = period P&L ÷ **balance at period open** (`period_pnl_pct` in `eod_report_helpers.py`) — **not** vs original demo seed
- **All-time %** = cumulative P&L ÷ **starting balance** (e.g. $5,000 demo seed)
- `🎖️⭐ P&L (WEEK M-F):`
- `💎 Account Balances (All Time):`
- `📅 Report Date: {YYYY-MM-DD}`
- `Time: {pt_time} ({et_time})` — **last row** (moved from header in Rev **00381**)

**Note**: Intraday flatten runs **12:50 PT** (`SO_ETF_EOD_CLOSE_*` env keys — legacy name; flattens **options** paths in Live). Scheduled **Telegram** EOD is separate from position flatten.

**Deduplication**: GCS marker `unified_0dte_eod_sent_{date}_{mode}.txt` — skips Cloud Scheduler retries after a successful send. The trading loop does not send this Telegram.

See **§4** below for unified EOD example.

### **8. ORB Capture Failed Alert**

**Trigger**: When ORB capture fails for all symbols  
**Purpose**: Notification of data collection failure  
**Content**:
- Number of symbols attempted
- Failure reason
- Recovery actions taken
- Next steps

---

## 🎯 **0DTE Strategy Alerts**

**Branding**: Telegram HTML for this path uses **`Easy ORB 0DTE`** in headers so option fills/exits/EOD are distinct from **Easy Trendline 0DTE** (below).

When `ENABLE_0DTE_STRATEGY=true` (`configs/ORB0DTE.env`), the ORB 0DTE options stack is active. This is the primary 7:30 execution path in the Live app (no SO ETF batch).

**Alert inventory (Easy ORB 0DTE + Trendline + Breakout)** — Aug 9 shared `send_strategy_*` contracts:

| # | Alert | Method | Telegram header (pattern) |
|---|-------|--------|---------------------------|
| — | ORB capture / signal collection | `send_orb_capture_complete_alert(phase=…)` / `send_so_signal_collection` | **0DTE ORB1/ORB2 Capture Complete** + **0DTE Signal Collection** |
| 3 | Options execution (ORB1 rolling) | `send_strategy_options_execution_alert("orb1")` | **`🔮 Breakout Order Filled`** (one Telegram per fill) |
| 3b | Options execution (batch / reject funnel) | `send_options_execution_alert` | **`🔮 Breakout Order Filled`** — retained for reject/zero-fill funnel; not the live ORB1 fill path |
| 3a | Durable confirmation | `send_options_durability_update_alert` | **`Easy ORB 0DTE — Durable Confirmation Update`** |
| 3a | Exit-grade durable | `send_options_exit_grade_durable_alert` | **`Exit-Grade Durable (Stage B)`** |
| 3a | Durability terminal | `send_options_durability_terminal_alert` | **`Easy ORB 0DTE — Durability terminal notice`** |
| 4 | Position closed | `send_strategy_position_exit_alert` (via `send_options_position_exit_alert` / TL·BO wrappers) · EOD/batch `send_options_aggregated_exit_alert` | Intraday: **`🔮 Breakout Order Closed`** / **`📏 Trendline TL0–TL3 Order Closed`** / **`⛓️ Breakout Order Closed`**. EOD flatten: **`🔮 Batch Order Closed`** (all strategies, one batch) |
| 5–6 | Partial / ladder runner | `send_options_partial_profit_alert` / `send_options_runner_exit_alert` | **Legacy** — formatters retained; **`0DTE_AUTO_PARTIAL_ENABLED=false`** (stealth owns full-close exits) |
| 7 | Portfolio health | *(removed Jun 25)* | Was `send_options_health_check_alert` — not wired |
| 8 | Scheduled EOD (unified account) | `send_scheduled_unified_0dte_eod` → `send_options_end_of_day_report` | **`END-OF-DAY ORB 0DTE`** |
| 9 | Demo system | `send_system_alert` via MockOptionsExecutor | **`🎮 DEMO \| Easy ORB 0DTE \| …`** |
| TL | Trendline execution | `send_trendline_options_execution_alert` → strategy entry | **`📏 Trendline TL0|TL1|TL2|TL3 Order Filled`** |
| TL | Trendline closed | `send_trendline_position_exit_alert` → strategy exit | **`📏 Trendline TL0|TL1|TL2|TL3 Order Closed`** |
| BO | Breakout execution / closed | `send_breakout_options_*` → strategy contracts | **`⛓️ Breakout Order Filled`** / **`⛓️ Breakout Order Closed`** |
| TL/BO | *(silent)* | — | Capacity / **`red_day_tier2_call_blocked`** — no Telegram |

**Retired (Live scheduler):** separate `send_scheduled_orb_0dte_eod` / `send_scheduled_trendline_eod` EOD telegrams — **removed** Jun 2026; `main.py` sends **one** unified EOD only (`send_scheduled_unified_0dte_eod`).

### **1. 0DTE ORB Capture (same alert as §3.2)**

**Trigger**: After ORB capture completes (6:45 AM PT)  
**Method**: `send_orb_capture_complete_alert`  
**Purpose**: One Telegram confirms opening ranges for the **0DTE watchlist** used by ORB 0DTE and Trendline.

**Content** (Rev **Jun 11** — see §3.2 example):
- Title + **`| {MODE} Mode`** line (no emoji in title)
- **0DTE Watchlist Capture** block — captured / active / duration
- **ORB Window** + **Next** (0DTE Signal Collection)
- **`📅 Report Date:`** + **`Time:`** footer

**Note**: One capture alert — **`0dte_list.csv`** (**78** symbols) drives the full ORB universe. Keep Live and DEMO app copies identical for count parity. See [data/watchlist/WATCHLIST_REVIEW.md](../data/watchlist/WATCHLIST_REVIEW.md).

### **2. Trade Signal Collection (same alert as §3)**

**Trigger**: 7:30 AM PT — same event as **Trade Signal Collection** above  
**Purpose**: 0DTE CALL/PUT funnel in **one** alert via `send_so_signal_collection()` — **0DTE-only body in Live app when `MODE=live`** (no ORB Breakouts / Standard Orders block).  
**Header (Rev Jun 23)**: Live app — **`====`** bar + **one blank line** + title; **no** DEMO banner (see §3).  
**Path docs**: [ProcessFlow.md](ProcessFlow.md), [0DTEORB1Breakout.md](0DTEORB1Breakout.md) §2 Signal Collection.

**Content** (integrated — no second Telegram):
- **`🔮 0DTE Options Ready`** — CALL + PUT lines + pipeline diagnostics
- Watchlist size from **`0dte_list.csv`**

### **3. Breakout Order Filled Alert (ORB1)**

**Telegram title (Rev Jun 17)**: **`🔮 Breakout Order Filled`** — section heading retained for navigation.

**Trigger**: ORB1 watch fills (rolling) and/or reject/zero-fill funnel after rank + Convex/Hard Gate. LONG → CALL, SHORT → PUT.  
**Purpose**: Confirm each durable fill (rolling) or surface the pipeline funnel when there are rejects / zero fills.  
**Method (live fills)**: `send_strategy_options_execution_alert("orb1", …, single_emit=True)` — **one Telegram per filled trade**, same contract-line body as Trendline/Breakout (shared `_trendline_exec_context_line_items`: `chain_source` / budgets / optional **`Liq Cap:`**).  
**Method (reject / zero-fill funnel)**: `send_options_execution_alert` — retained batch formatter with **🎙️ Easy ORB 0DTE pipeline (this run)** counts.

**Content**:
- **Header (Rev Jun 17)**: **`🔮 Breakout Order Filled`** → **`{mode} Mode`** on indented second line — **no** `Time:` row under the title
- **🎙️ Easy ORB 0DTE pipeline (this run)** (Rev 00318 + Rev 00331 durability + Rev 00332 two-stage / quote rows):
  - **Watchlist** (`0dte_list.csv` size)
  - **Collection CALL+PUT** — rows from Signal Collection scan
  - **Convex-qualified** (after strategy / Convex filter)
  - **Hard gate → execution queue** (post-Hard Gate, pre-rank pool)
  - **Queued for execution** (post-rank + Gate 3c ★ PS floor + cap, ≤10)
  - **Selector loop (sized)** · **Broker routing (selector)** · **Broker submitted**
  - **Execution durable** · **Exit-grade durable** (single-line counts; Stage A/B still in durability follow-up alerts §3a)
  - **Failed before/after selector** counters · **Execution attempts** (single-line count)
  - **Broker accepted total** (durable + pending + monitoring-not-yet-durable)
  - **Pending confirmation** (not durable yet — surfaces broker timing lag, not failure)
  - **Dropped at Convex** (structure + dedupe count on one line)
  - **Dropped after Hard Gate** (rank + Gate 3c cap; shown when &gt; 0)
  - **Execution failed/rejected** (true rejects + durability failures)
  - Optional **Avg Momentum** when momentum data present
- **`Details`** (Rev Jun 17; selector detail Rev **Jun 18**): per-signal bullets; on single-leg rejects show **Premium / Delta / Spread filter** summary (not generic lotto copy). See DEMO [Alerts.md](../1.%20The%20Easy%20ORB%20Strategy%20RESEARCH%20APP/docs/Alerts.md) §3 for examples.
- **`📋 Symbol execution diagnostics`**: durable + reject rows; lotto_first rows use **multi-line** meta (`execution_structure` / `spread_primary` · `strategy_type_at_730` / `spread_fallback` · `selector_result` · `reject_reason` on separate lines; Premium/Delta/Spread `reject_reason` may wrap after ` — `)
- **Durability classification (Rev 00331)**: counters come from canonical buckets `durable_executed`, `submitted_pending_confirmation`, `monitoring_active_not_yet_durable`, `chain_health_failed`, `selector_failed`, `durability_failed`, `broker_submit_failed` (see telemetry **`ORB_0DTE_DURABILITY_ALERT_BUCKETS`**). A delayed durability reconciliation pass (**`ORB_0DTE_DURABILITY_RECONCILE_START`** / **`_RESULT`**) runs before final classification so submitted-but-not-yet-durable orders are not counted as rejected.
- **Two-stage durability + quote row (Rev 00332, May 11)**: summary lines **`Execution durable`**, **`Exit-grade durable`**, **`Monitored, degraded / pending exit-grade`**; per durable row **`quote_grade`** (e.g. `cached_recent`, `exit_grade`) and **`exit_grade_ready`** so Stage A (execution path live) is visible when Stage B (exit-grade marks) is still pending.
- **When durable trades fill**: **`💼 Easy ORB 0DTE — options executed:`** count, per-position blocks (debit spread, credit spread, lotto as applicable) with rank/score, **delta** (fill-time abs δ — expect **0.18–0.42** on single-leg directional; near-ATM / higher δ preferred), strikes, premiums, **`$X deployed (Y.Y%)`** on its own line for lotto (Rev **00380**; spreads still show **`X.X% Of Account`**), **Trade ID** (compact on **new** fills: **`SYM-strikep-seq`**, e.g. `COIN-165p-047`; leftover `D-`/`L-` stripped in Telegram/Event Tape; stored ledger keys are not rewritten); pending rows still appear under **`⏳ Submitted, awaiting durable confirmation`** when present.
- **Capital Deployment**: deployed / account (%)
- **When orders submitted but not yet durable** (Rev 00331): **`⏳ ORB 0DTE orders submitted; awaiting durable confirmation`** with `Execution attempts`, **`Broker accepted total`**, `Pending confirmation`, true rejection reason rollups, and the **`⏳ Submitted, awaiting durable confirmation`** per-symbol block (symbol, trade ID, monitor state). System status line: **`Monitoring active positions; durable confirmation pending`**.
- **When zero fills (and zero pending)** (Rev 00333 + Rev Jun 11): **`⚠️ NO OPTIONS EXECUTED`** with **`no_trades_reason`** — when `rejection_category=portfolio_protection`, body uses **`Portfolio Protection trigger`** + **`Reason:`** from `format_0dte_execution_skip_reason()` (Gate **3a** or **3c**); otherwise structured block (Convex blocked all, Hard Gate empty queue, chain health, position caps, etc.) plus **Actual rejection reasons**. Orchestrator logs **`0DTE_ZERO_EXEC_ALERT_SENT`** once per PT day. **Alert order:** §4 PP Telegram **before** this alert when PP caused 0 fills.
- **Distinction:** Convex / Hard Gate / chain-only 0-fill days → **no** §4 PP alert; grep **`0DTE_ZERO_EXEC_ALERT_SENT`** for execution summary.
- **🧪 Chain health diagnostics block** (when chain-health failures present): retry count, relaxed pass count, prequeue extension rejects, execution count, plus per-failure-type histogram (`no_chain_data`, `empty_chain`, `no_valid_strikes`, `spread_too_wide`, `low_liquidity`, `API_error`, `timeout`, `unknown`).
- **Per-symbol execution rejects (Rev 00329)**: Text like **`SYMBOL: No debit spread found from chain/strike selector`** or **`SYMBOL: No ITM probability spread found from chain/strike selector`** means spread selection returned no valid pair (premium/delta/OTM/pairing/liquidity gates), not “ORB rules failed.”
- **Selector leg diagnostics (Rev 00329)**: On ITM spread failures, Cloud logs include `0DTE_SELECTOR_DIAG` plus `ITM_SPREAD_SELECTOR` with failure stage (for example: `long_leg_unavailable`, `short_leg_unavailable`, `long_leg_liquidity`, `short_leg_liquidity`) so leg-level root cause is explicit.
- After payoff guardrails, **`poor_payoff_profile`** remains separate from **`spread_selection_failed`**.

**Ops / logs**: Grep **`ORB_RANK_FOR_0DTE`**, **`0DTE_PIPELINE`**, **`ORB_0DTE_DURABILITY_*`**, **`0DTE_DEMO | synthetic_chain`** (DEMO synthetic chain generation), **`spread_selection_failed`**, **`single_leg_selection_failed`**, **`No debit spread found`**, **`No ITM probability spread found`**, **`0DTE_SELECTOR_DIAG`**, **`ITM_SPREAD_SELECTOR`** for strike-selection root cause, **`ORB_0DTE_SELECTOR_FULL_REPLAY`** for full selector ladder / width-ladder rejects, **`ORB_0DTE_DURABILITY_ALERT_BUCKETS`** / **`ORB_0DTE_DURABILITY_RECONCILE_START`** / **`ORB_0DTE_DURABILITY_RECONCILE_RESULT`** for durability classification. **May 19:** **`0DTE_ZERO_EXEC_ALERT_SENT`**, **`0DTE_CONVEX_RED_DAY_PREVIEW`**, **`0DTE_CONVEX_RED_DAY_REFRESH`**, **`CONVEX_REJECT`** / **`CONVEX_PASS`**. **Jun 5:** **`0DTE_HARD_GATE_SYMBOL_SUMMARY`**, **`0DTE_LOTTO_BREAKDOWN_PREMIUM_RELAX`**, **`0DTE_LOTTO_BREAKDOWN_PREMIUM_LADDER`**, **`ORB_CRITICAL_WINDOW`**, **`STARTUP_CONFIG_AUDIT`**. May 11 additions: **`ORB_0DTE_EXECUTION_DURABLE_CONFIRMED`**, **`ORB_0DTE_EXIT_GRADE_DURABLE_CONFIRMED`**, **`ORB_0DTE_STRATEGY_TYPE_PROPAGATED`**, **`ORB_0DTE_OVEREXTENSION_SOFT_PENALTY`** / **`ORB_0DTE_OVEREXTENSION_ALLOWED_STRONG_CONTINUATION`** / **`ORB_0DTE_OVEREXTENSION_EXTREME_REJECT`**, **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`**, **`ORB_OPTIONS_EXIT_DEFERRED_AUDIT`**, **`ORB_OPTIONS_FORCED_DEGRADED_EXIT`**.

**Example (with trades — lotto row, Rev Jul 24)**:
```
====================================================================

🔮 <b>Breakout Order Filled</b>
          | DEMO Mode

🎙️ <b>Easy ORB 0DTE pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): <dynamic>
          • <b>Collection CALL+PUT</b>: 34
          • <b>Strategy-qualified (pc_oi + ORB)</b> (strategy filter): 20
          • <b>Hard gate → execution queue</b>: 12
          • <b>Queued for execution</b>: 12
          • <b>Selector loop (sized)</b>: 11
          • <b>Broker routing</b> (selector): 11
          • <b>Broker submitted</b>: 8
          • <b>Execution durable</b>: 8
          • <b>Exit-grade durable</b>: 0
          • <b>Execution attempts</b>: 11
          • <b>Broker accepted total</b>: 8
          • <b>Pending confirmation</b>: 0
          • <b>Dropped at Convex</b>: 14
          • <b>Execution failed/rejected</b>: 3

💼 <b>Easy ORB 0DTE — options executed:</b> 1

1) 🟢 <b>2 • COIN CALL Lotto @ $1.89 x 100 =</b>
          $378.00 deployed (7.9%)
          Δ 0.21
          Trade ID: COIN-195c-670

💰 <b>Capital Deployment:</b>
          • <b>Deployed:</b> $378.00 / $4,755.25 (7.9%)

🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~2s + shared backup)
====================================================================
```

**Shared contract line (ORB lotto fills)**: `{qty} • {SYM} {CALL|PUT} Lotto @ {premium} x 100 =` then indented **`$X deployed (Y.Y%)`**, **`Δ {delta}`**, **`Trade ID:`**.

**Example (with trades — mixed strategies; header omits Time)**:
```
====================================================================

🔮 <b>Breakout Order Filled</b>
          | DEMO Mode

🎙️ <b>Easy ORB 0DTE pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): <dynamic>
          • <b>Collection CALL+PUT</b>: 34
          • <b>Strategy-qualified (pc_oi + ORB)</b> (strategy filter): 20
          • <b>Hard gate → execution queue</b>: 12
          • <b>Queued for execution</b>: 12
          • <b>Selector loop (sized)</b>: 11
          • <b>Broker routing</b> (selector): 11
          • <b>Broker submitted</b>: 8
          • <b>Execution durable</b>: 8
          • <b>Exit-grade durable</b>: 0
          • <b>Execution attempts</b>: 11
          • <b>Broker accepted total</b>: 8
          • <b>Pending confirmation</b>: 0
          • <b>Dropped at Convex</b>: 14
          • <b>Execution failed/rejected</b>: 3

💼 <b>Easy ORB 0DTE — options executed:</b> 3
          (numbered lotto / debit / credit lines — lotto uses qty • SYM SIDE Lotto @ $prem x 100 =)

💰 <b>Capital Deployment:</b>
          • <b>Deployed:</b> $1,250.00 / $5,000.00 (25.0%)

🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~2s + shared backup)
====================================================================
```

**Example (orders submitted, not yet durable — Rev 00331)**:
```
====================================================================

🔮 <b>Breakout Order Filled</b>
          | DEMO Mode

🎙️ <b>Easy ORB 0DTE pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): <dynamic>
          • <b>Collection CALL+PUT</b>: 34
          • <b>Convex-qualified</b> (strategy filter): 20
          • <b>Hard gate → execution queue</b>: 12
          • <b>Queued for execution</b>: 12
          • <b>Execution attempts</b>: 6
          • <b>Broker accepted total</b>: 3
          • <b>Pending confirmation</b>: 3
          • <b>Dropped at Convex</b>: 14
          • <b>Execution failed/rejected</b>: 3

⏳ <b>ORB 0DTE orders submitted; awaiting durable confirmation</b>

<b>Execution attempts:</b> 6
<b>Broker accepted total:</b> 3
<b>Pending confirmation:</b> 3

💡 <b>True rejection reasons (so far):</b>
          • <b>chain_health_failed</b>: 2 (e.g. LEN, AEHR)
          • <b>spread_selection_failed</b>: 1 (e.g. BLDR)

⏳ <b>Submitted, awaiting durable confirmation</b>:
          - MU (DEMO_MU_260507_..._c_725) — submitted_pending
          - IREN (DEMO_IREN_260507_61_62_c_725) — monitoring_active_not_yet_durable
          - SLV (DEMO_SLV_260507_..._c_725) — submitted_pending

📊 <b>System status:</b> Monitoring active positions; durable confirmation pending
====================================================================
```

**Example (zero executions — all attempts rejected, Rev Jun 25)**:
```
====================================================================

🔮 <b>Breakout Order Filled</b>
          | DEMO Mode

🎙️ <b>Easy ORB 0DTE pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): 78
          • <b>Collection CALL+PUT</b>: 8
          • <b>Convex-qualified</b> (strategy filter): 4
          • <b>Hard gate → execution queue</b>: 4
          • <b>Queued for execution</b>: 4
          • <b>Selector loop (sized)</b>: 4
          • <b>Broker routing</b> (selector): 4
          • <b>Broker submitted</b>: 0
          • <b>Execution durable</b>: 0
          • <b>Exit-grade durable</b>: 0
          • <b>Failed before selector</b>: 1
          • <b>Failed selector</b>: 3
          • <b>Execution attempts</b>: 4
          • <b>Broker accepted total</b>: 0
          • <b>Dropped at Convex</b>: 4
          • <b>Execution failed/rejected</b>: 4

⚠️ <b>NO EASY ORB 0DTE OPTIONS TRADES EXECUTED</b>

<b>Execution attempts:</b> 4 (each line below is one queued signal that hit chain/momentum/strategy gates).

All 4 attempts were rejected or failed.

💡 <b>Actual rejection reasons:</b>
• <b>No single-leg contract found for strategy lotto …</b>: 4 symbols (e.g. MSTR, LLY, IBIT, NFLX)

🔍 <b>Details (first 4 signals):</b>
          • MSTR: Selector filter: no contract passed selector filters
          • LLY: Premium filter: allowed $0.03–$31.17 — 15% of chain rejected on premium

📋 <b>Symbol execution diagnostics</b>:
          • MSTR | PUT | <b>stage=pre_execution</b>
             result=reject | single_leg_selection_failed
             Selector filter: no contract passed selector filters
             execution_structure=lotto_first | spread_primary=false
             strategy_type_at_730=single_leg | spread_fallback=false
             selector_result=no_contract
             reject_reason=single_leg_selection_failed
          • LLY | PUT | <b>stage=pre_execution</b>
             result=reject | single_leg_selection_failed
             Premium filter: allowed $0.03–$31.17
             15% of chain rejected on premium
             execution_structure=lotto_first | spread_primary=false
             strategy_type_at_730=single_leg | spread_fallback=false
             selector_result=no_contract
             reject_reason=Premium filter: allowed $0.03–$31.17
             15% of chain rejected on premium

📊 <b>System status:</b> Ready for next trading opportunity
====================================================================
```

**Example (zero executions — Rev 00333, May 19)**:
```
====================================================================

🔮 <b>Breakout Order Filled</b>
          | DEMO Mode

🎙️ <b>Easy ORB 0DTE pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): <dynamic>
          • <b>Collection CALL+PUT</b>: 25
          • <b>Convex-qualified</b> (strategy filter): 0
          • <b>Hard gate → execution queue</b>: 0
          • <b>Queued for execution</b>: 0
          • <b>Execution attempts</b>: 0

⚠️ <b>NO OPTIONS EXECUTED</b>

<b>Reason:</b> Convex blocked all candidates (portfolio Red Day; PUT floor 0.62 after refresh)

💡 <b>Top rejection reasons:</b>
          • <b>convex_score_below_min</b>: 18 (e.g. MU, IREN, SLV)
          • <b>momentum_insufficient</b>: 7

📊 <b>System status:</b> No ORB 0DTE positions opened this session
====================================================================
```

### **3a. Easy ORB 0DTE — Durability Follow-Up Alerts (May 11+)**

These are **separate Telegram messages** sent after the main **Options Execution** alert when broker-submitted rows transition through the two-stage durability pipeline. They are **observability only** — they do not mutate orders, positions, or exit state.

| Alert | Method | Header | When it fires |
|-------|--------|--------|---------------|
| **Durable Confirmation Update** | `send_options_durability_update_alert` | **`✅ Easy ORB 0DTE — Durable Confirmation Update`** | Previously pending submissions become **Stage A durable** and fully monitored |
| **Exit-Grade Durable (Stage B)** | `send_options_exit_grade_durable_alert` | **`🎯 Exit-Grade Durable (Stage B)`** | Exit-grade marks stabilize for a submitted row (`good_mark_ticks`, `quote_source`). **Deferred until that trade's fill/entry Telegram has been sent**, then flushed in receive order. |
| **Durability terminal notice** | `send_options_durability_terminal_alert` | **`⚠️ Easy ORB 0DTE — Durability terminal notice`** | After reconcile + quote pumps, rows remain outside a terminal bucket (mixed-batch path; May 12 fix) |

**Durable Confirmation Update — content**:
- Header includes **`Time:`** row (PT + ET) — unlike execution/closed trade alerts
- Per-row blocks reuse execution trade-line format (symbol, trade ID, **`quote_grade`**, **`exit_grade_ready`**, delta, premiums)
- Up to **10** rows per message

**Exit-Grade Durable — content**:
- Per row: **`{symbol} ({trade_id}) — exit-grade durable • good_mark_ticks=N • quote_src=…`**
- Footer: marks reached exit-grade stability; monitoring continues
- Order: fill/entry Telegram first; Stage B never jumps ahead of that trade's fill (buffered until fill send succeeds). Fill Telegrams are one-per-`trade_id` (no duplicate entry alerts).

**Durability terminal notice — content**:
- **`Run:`** `{run_id}` · **`Issue:`** `{reason}` (may note siblings already durable)
- Per row: **`state=`** monitor state · **`code=`** reason code
- **`Next steps:`** grep **`ORB_0DTE_DURABILITY_BLOCKED_REASON`**, **`ORB_0DTE_MONITOR_ATTACHED`**, **`ORB_0DTE_FIRST_QUOTE_HYDRATED`**
- Explicit footer: **observability only** — does not close positions or change durability state

**Ops / logs**: **`ORB_0DTE_EXECUTION_DURABLE_CONFIRMED`**, **`ORB_0DTE_EXIT_GRADE_DURABLE_CONFIRMED`**, **`ORB_0DTE_DURABILITY_RECONCILE_START`** / **`_RESULT`**, **`ORB_0DTE_DURABILITY_RECONCILE_PUMP`**

**Example (Durable Confirmation Update)**:
```
====================================================================

✅ <b>Easy ORB 0DTE — Durable Confirmation Update</b>
          | DEMO Mode
          Time: 07:32 AM PT (10:32 AM ET)

⏳ Previously pending 0DTE submissions are now <b>durable and fully monitored</b>:

1) 🔴 <b>MU PUT Lotto</b>
          5 × MU 260608P95 @ $0.42
          $210.00 deployed (4.2%)
          Δ -0.31
          Trade ID: D-MU-95p-047
          quote_grade=exit_grade • exit_grade_ready=true
```

**Example (Durability terminal notice — observability only)**:
```
====================================================================

⚠️ <b>Easy ORB 0DTE — Durability terminal notice</b>
          | DEMO Mode
          Time: 07:34 AM PT (10:34 AM ET)
          Run: <code>orb_0dte_20260608_0730</code>

<b>Issue:</b> Mixed batch: some siblings durable; rows below still pending exit-grade

<b>Positions still outside a terminal observability bucket after reconcile + quote pumps:</b>
          • <b>IREN</b> (D-IREN-61c-052) — state=monitoring_active_not_yet_durable — code=pending_exit_grade

🛈 <b>Observability only:</b> This message does not close positions, modify orders, or change strategy or durability state.
```

### **4. Easy ORB 0DTE — Position Exit Alerts**

#### **Individual Exits**

**Trigger**: When an Easy ORB 0DTE options position closes (stealth / EOD path).  
**Method**: `send_options_position_exit_alert` → `send_strategy_position_exit_alert("orb1")` — header **`🔮 Breakout Order Closed`**.  
**Header (Rev 00380)**: **no** `Time:` row under the title.  
**Purpose**: Detailed exit information with real-time options P&L  
**Content**:
- **Shipped live:** `price_stop`, `premium_stop`, `price_trail`, `mfe_take_profit` (thin ARM), `breakeven`, `scratch`, `time_exit`, `no_progress`, `end_of_day_exit` (+ emergency `fail_safe` / `health_emergency`). **Aug 11 bands (alert IDs unchanged):** `breakeven` ≥**60m** prem **−0.05%…&lt;+2%** · `scratch` ≥**62m** prem **&lt;−0.05%**. **Config-off:** always-bank `mfe_take_profit`, `mfe_giveback`, `take_profit` (und hard TP PCT=0), `hold_exit` (env family removed). **Removed Aug 17:** `moon_hold` (historical logs only). **Disabled / normalize-only:** `mfe_trailing_stop`.
- Entry and exit **premium** and underlying **`Entry $:`** / **`Exit $:`** (one field per line — no bullet joins)
- P&L (absolute and percentage) — based on actual options marks; comma-formatted USD
- **Contract line**: `{qty} • {SYM} {CALL|PUT} @ {entry_premium} x 100 =` (`_format_option_contract_line`)
- **`Moon: ⭐️`** (Aug 17) — only when **realized premium PnL > +200%** (`pnl_pct > 2.0`); omitted otherwise. Placed after **Reason**, after optional **Liq Cap**, before **Account Balance**. Moon hold H2 is **removed** — this tag is not a hold state.
- **`Liq Cap: ⚖️ {reason}`** (Aug 16) — fill **and** exit, only when Liquidity Guard sized down qty; omitted otherwise. Exit: after **Reason**, immediately above **Moon**.
- **`$X deployed (Y.Y%)`** — **Y = deployed ÷ account equity at entry**
- **`Exit Premium: $X • Net: ±$Y`**
- **`Expiry:`** / **`DTE:`** / **`Is_0dte:`** — resolved from nested `execution_payload` / `signal_snapshot` / trade-id (`_collect_expiry_fields_for_exit_alert`, Aug 10)
- **No Delta** on ORB1 / Trendline / ORB2 close alerts
- **`Max P&L (Bid):`** — shared on ORB1 / Trendline / ORB2 exit alerts (was `Max PnL:` on ORB; `Max Liquidatable…` / `Max Liquid…` on TL·BO)
- Quote / mark diagnostics (source, age, exit grade)
- Plain labels: **Trade ID**, **Reason**, **Account Balance**

**Example (individual exit — Rev Aug 12 · non-moon)**:
```
====================================================================

🔮 <b>Breakout Order Closed</b>
          | DEMO Mode

🔰 <b>+20.90% +$84.00</b>
          6 • AMZN CALL @ $0.67 x 100 =
          $402.00 deployed (8.2%)
          Exit Premium: $0.81 • Net: +$0.14
          Expiry: 2026-08-10
          DTE: 0
          Is_0dte: true
          Entry $: $248.49
          Exit $: $249.03 (+0.22%)
          Max P&L (Bid): +74.63%
          Exit P&L (liq): +20.90%
          Quote Source: exact_quote
          Quote Age: 0.0s
          Mark Quality: live_single_leg
          Exit Grade: true
          Trade ID: D-AMZN-250c-069
          Reason: price_trail
          Account Balance: $4,478.50
====================================================================
```

**Moon / Liq Cap (Aug 17):** optional tags after **Reason**, before **Account Balance**. **`Liq Cap: ⚖️ …`** only when Liquidity Guard sized down. **`Moon: ⭐️`** only when realized premium PnL **> +200%**.

**Real-time contract marks**:
- Exit decisions use actual option premiums from the broker; open positions are polled on the **unified 0DTE monitor** (default **2s**, `0DTE_POSITION_MONITOR_INTERVAL_SEC`) for ORB, Trendline, and Breakout
- P&L reflects contract marks when quotes are available (strict modes: `OPTION_REQUIRE_LIVE_QUOTES` / `REQUIRE_LIVE_OPTION_DATA`)

#### **Aggregated Exits**

**Trigger**: Batch closes (EOD at 12:50 PM PT, emergency exits) for the **unified 0DTE book** (ORB1 Breakout + ORB2 Breakout + Trendline).  
**Method**: `send_options_aggregated_exit_alert` — header **`Batch Order Closed`**.  
**Header**: **no** `Time:` row; uses shared `_format_option_closed_blocks` layout.  
**Purpose**: ONE Telegram for every options position closed in that flatten — not a Breakout-only batch, and not per-trade Trendline singles.  
**Content**:
- Title: **`🔮 Batch Order Closed`** + `| {MODE} Mode`
- Summary line: **`Closed: N • Total PnL: +$X`**
- Trades ordered by strategy when tagged: **ORB1 Breakout → ORB2 Breakout → Trendline (TLn)**
- Strategy group headers between groups when the batch mixes strategies (`🔮 ORB1 Breakout` / `⛓️ ORB2 Breakout` / `📏 Trendline TLn`)
- Per-position blocks: same body as a single exit (contract line → deployed → Exit Premium • Net → DTE/expiry → Entry/Exit $ → MFE → liq PnL → quote → Trade ID → Reason)
- **Reason** for the 12:50 PT flatten is always **`end_of_day_exit`** (catalog ID). Legacy `EOD_CLOSE` / `eod_close` normalize to that same ID.
- **One blank line between trades**
- If the payload exceeds Telegram length, split into continuation messages (`Continued: i–j of N`) instead of truncating
- Optional EOD flatten footer + account balance on the last part

**Intraday** fills/exits stay path-labeled (`🔮 Breakout Order Closed`, `⛓️ Breakout Order Closed`, `📏 Trendline TLn Order Closed`). Only the simultaneous EOD/batch flatten uses **`Batch Order Closed`**.

**Example**:
```
====================================================================

🔮 Batch Order Closed
          | DEMO Mode

💼 Closed: 17 • Total PnL: -$948.10

🔮 ORB1 Breakout

1) 📉 -33.33% -$116.20
          14 • VZ CALL @ $0.21 x 100 =
          $294.00 deployed (0.8%)
          Exit Premium: $0.14 • Net: -$0.07
          ...
          Reason: end_of_day_exit

2) 📉 -13.03% -$64.60
          2 • NEM CALL @ $2.38 x 100 =
          ...
====================================================================
```

### **5–6. Easy ORB 0DTE — Partial / ladder Runner Alerts (legacy — off)**

**Status (Aug 9):** Formatters `send_options_partial_profit_alert` / `send_options_runner_exit_alert` remain in `prime_alert_manager.py` but are **not** on the live exit path. Executor is constructed with **`auto_partial_enabled=False`** (`0DTE_AUTO_PARTIAL_ENABLED` default **`false`**). Full-close profit capture is owned by premium stealth / price-runner (`price_trail`, `price_stop`, `breakeven`, …) — see [0DTEProfitCapture.md](0DTEProfitCapture.md).

Do not expect FIRST/SECOND TARGET or ladder **RUNNER EXIT** Telegrams under the shipped Shared.env §2b policy.

### **7. Easy ORB 0DTE — Options Health Check (removed Jun 25)**

**Status**: `send_options_health_check_alert` was **removed** during alert cleanup (was never wired in Live app). Post-fill risk uses premium stealth exit alerts.

### **8. Unified 0DTE — Scheduled End-of-Day Report**

**Trigger**: Cloud Scheduler endpoint at 3:55 PM ET (12:55 PM PT) daily  
**Source**: Single source — Cloud Scheduler **`POST /api/end-of-day-report`** only (Rev 00260)  
**Method**: `send_scheduled_unified_0dte_eod` → `send_options_end_of_day_report`  
**Deduplication**: GCS marker `unified_0dte_eod_sent_{date}_{mode}.txt`  
**Purpose**: Daily **combined** 0DTE options performance for the **unified account** (ORB 0DTE + Trendline 0DTE paths share one ledger / one broker account).

**Note**: Intraday open/close alerts remain **path-labeled** (ORB vs Trendline). EOD is **one** summary for the whole account (combined P&L — no per-path breakdown subsection).  
**Content**:
- Total options P&L (based on actual options prices)
- Number of options trades executed
- Win rate (winning trades / total trades)
- Best and worst trades (with strategy types)
- Strategy breakdown:
  - Debit spreads: count, P&L, win rate
  - Long calls/puts: count, P&L, win rate
  - Momentum scalpers: count, P&L, win rate
  - ITM probability spreads: count, P&L, win rate
  - Lotto sleeves: count, P&L, win rate
- Average P&L per trade
- Capital efficiency (capital deployed / available capital)

**Example (unified EOD — Jun 9 desk, ORB + Trendline on one account)**:
```
====================================================================

🏦 <b>END-OF-DAY ORB 0DTE</b> | 🎮 🍒
          | DEMO Mode

📈 <b>P&L (TODAY):</b>
          <b>+12.40% +$1,240.00</b>
          Win Rate: 66.7% • Total Trades: 3
          ...

🎖️⭐ <b>P&L (WEEK M-F):</b>
          <b>+12.40% +$1,240.00</b>
          ...

💎 <b>Account Balances (All Time):</b>
          <b>+24.80% +$1,240.00</b>
          <b>$6,240.00</b>
          ...

📅 <b>Report Date:</b> 2026-06-09
          Time: 12:55 PM PT (03:55 PM ET)
```

**Period-% math:** today/week % = period P&L ÷ **balance at period open** (`period_pnl_pct`). All-time % = cumulative P&L ÷ **demo seed** (`0DTE_DEMO_STARTING_BALANCE`, default **$5,000**).

**Example (unified EOD — Rev 00381 layout reference)**:
```
====================================================================

🏦 <b>END-OF-DAY ORB 0DTE</b> | 🎮 🍒
          | DEMO Mode

📈 <b>P&L (TODAY):</b>
          <b>+161.15% +$11,614.50</b>
          Win Rate: 100.0% • Total Trades: 1
          Wins: 1 • Losses: 0
          Profit Factor: 11,614.50
          Average Win: $11,614.50
          Average Loss: $0.00
          Best Trade: +$11,614.50
          Worst Trade: +$11,614.50

🎖️⭐ <b>P&L (WEEK M-F):</b>
          <b>+161.15% +$11,614.50</b>
          Win Rate: 100.0% • Total Trades: 1
          Profit Factor: 11,614.50

💎 <b>Account Balances (All Time):</b>
          <b>+161.15% +$11,614.50</b>
          <b>$18,285.50</b>
          Win Rate: 100.0% • Total Trades: 1
          Profit Factor: 11,614.50
          Wins: 1 • Losses: 0

📅 <b>Report Date:</b> 2026-06-05
          Time: 12:55 PM PT (03:55 PM ET)

====================================================================
```

### **9. Demo: per-position system alerts (Easy ORB 0DTE)**

When `OptionsTradingExecutor` uses **`MockOptionsExecutor`** (demo), opening or closing a spread/lotto may send **`send_system_alert`** with titles such as:

- `🎮 DEMO | Easy ORB 0DTE | Debit Spread Opened` / `Lotto Sleeve Opened` / `Credit Spread Opened`
- `🎮 DEMO | Easy ORB 0DTE | Position Closed`

Body first line: **`Strategy: Easy ORB 0DTE (options)`**. These complement (do not replace) the batch **`send_options_execution_alert`** summary after the Step 5 execution loop.

---

## 📈 **Easy Trendline 0DTE Alerts**

**Enable**: `ENABLE_TRENDLINE_STRATEGY=true` (see `configs/Trendline0DTE.env` / runtime env). Uses the same **0dte_list.csv** / ORB capture universe as Easy ORB 0DTE. **Demo + Live fills** register on the **unified options ledger** (`MockOptionsExecutor` / broker `ETRADE_0DTE_ACCOUNT_ID`) via `TrendlineOptionsExecutor._execute_unified_lotto_open()` — not a separate Trendline-only EOD ledger.

**Delivery** (wrappers → shared Aug 9 contracts):
- **Execution fills**: `send_trendline_options_execution_alert` → `send_strategy_options_execution_alert("trendline")` when `single_emit=True`
- **Position closes**: `send_trendline_position_exit_alert` → `send_strategy_position_exit_alert("trendline")`
- **Monitor alerts**: optional `send_system_alert` path, controlled by `TRENDLINE_MONITOR_ALERT_ENABLED` (default off)
- **Scheduled EOD**: included in **unified** **`🏦 END-OF-DAY ORB 0DTE`** (`send_scheduled_unified_0dte_eod`) — Trendline P&L rolls into combined account totals; **no** separate Trendline EOD Telegram

| Event | Title pattern | Notes |
|--------|-----------------|--------|
| Fill / execution reject | `📏 Trendline TL0\|TL1\|TL2\|TL3 Order Filled` → `\| {mode} Mode` | **One Telegram per position-open execution attempt** — slot from `trendline_slot` (`trendline0`→**TL0**, …). Contract line **`{qty} • {SYM} {CALL\|PUT} @ $prem x 100 =`**; body: **`$X deployed`** → **`Strike • Delta`** → **`Setup`** → **`Trigger`** → **`Line Geometry`** → **`Trade ID`** → optional context (`chain_source` / budgets / **`Liq Cap:`** when Liquidity Guard sized down); **no** Option Leg / Entry Premium / Structure rows |
| Exit (stealth / EOD path) | `📏 Trendline TL0\|TL1\|TL2\|TL3 Order Closed` → `\| {mode} Mode` | Same slot label as fill; **`{qty} • {SYM} {CALL\|PUT} @ $prem x 100 =`** → deployed → Exit Premium → **`Expiry` / `DTE` / `Is_0dte`** → Entry/Exit $ → **`Max P&L (Bid):`** → Exit P&L → quotes → Trade ID / Reason → optional **`Liq Cap: ⚖️`** → optional **`Moon: ⭐️`** → Account Balance; **no Delta** on close; **no** header **`Time:`** |
| Periodic monitor | `🔮 Easy Trendline 0DTE — Monitor \| LIVE` | Optional; disabled by default via `TRENDLINE_MONITOR_ALERT_ENABLED=false`; if enabled, throttled by `TRENDLINE_MONITOR_ALERT_INTERVAL_SEC` |
| Scheduled summary | *(unified EOD)* | See **§4.8** — **`END-OF-DAY ORB 0DTE`** (combined account totals) |

**Labels in code**: `ALERT_LABEL_EASY_TRENDLINE_0DTE` in `modules/prime_alert_manager.py` (used by `prime_trading_system.py` for Trendline system alerts).

**Docs**: [0DTETrendline.md](0DTETrendline.md) for behavior, env keys, and telemetry.

**Alert policy (execution only)**: `📏 Trendline TLn Order Filled` / **`… Closed`** apply to **TL0–TL3** equally (entry + exit + post-exec reject). Not a signal-emit, quality-skip, or **geometry-build** alert (there is **no** TL0/TL1 “build complete” Telegram). Unscoped `📏 Trendline Order Filled` only for mixed-slot batch / missing slot (defaults **TL1**).

| Pipeline stage | Telegram? |
|----------------|-----------|
| Quality skip (pre-emit: RSI, VWAP, volume, etc.) | **No** — logs only |
| Signal emit without executor handoff | **No** |
| Pre-execution block (capacity, cooldown, red-day, geometry, orchestrator, risk) | **No** — grep **`execution_alert_suppressed`** |
| **Executor fill** | **Yes** — **`Result: Filled • execution`** |
| **Executor reject** (`post_executor`: delta, spread, chain, sizing) | **Yes** — **`Result: Rejected • execution`** |

**Code gates**: `_trendline_failed_row_may_send_execution_alert()` · `execution_attempted` · `_TRENDLINE_EXECUTION_REJECT_ALERT_STAGES` = `{post_executor, quality_reject}` in `modules/prime_trading_system.py`; `_STRATEGY_EXECUTION_REJECT_ALERT_STAGES` in `prime_alert_manager.py` (orb1 / breakout / trendline all allow **`quality_reject`**).

**Silent stages** (no Telegram — log **`TRENDLINE_PIPELINE | stage=execution_alert_suppressed`**):
- **Capacity / rearm**: `open_cap_reached`, `buffer_reserve_exhausted`, `option_global_cap`, `global_daily_cap`, `option_entry_cooldown`, `red_day_tier2_call_blocked` (Portfolio Protection Tier 2+ CALL on Red Day — **`00017-x9n`**)
- **Pre-executor**: `orchestrator_geometry`, `orchestrator`, `risk_manager`

**Quality Gate rejects (Aug 12 path-opt):** hard QG / reclaim on TL · BO · ORB1 send Reject Telegram with **`Stage: quality_reject`** and **`Reason: {gate_id}:{reason}`** (e.g. `RSI:put_41.2<48`, `Vol_Ratio:0.257<0.400`, `VWAP:ext_2.09>15`, `BB:put_32<40`). Display via `_qg_reject_display_code` — matches [0DTEQualityGates.md §3.2](0DTEQualityGates.md#32-path-opt-hard-cutover--reject-reason-review-aug-12). Soft data-gap defers stay log-only (`quality_defer`).

**Executor rejects** (delta, chain, spread, sizing / market-quality after handoff) send **`📏 Trendline TLn Order Filled`** with **`Result: Rejected • execution`**. **`Reason:`** is the **bare** executor / MQ code (**`bid_size_too_small`**, **`ask_size_too_small`**, **`red_day_call_non_tier1`**, **`spread_too_wide`**, **`order_submit_failed`**, **`delta_out_of_band`**, **`option_premium_too_low`**, etc.) — not a prefixed sentence and not a second `reject_reason=` line. Reject title line: **`⚠️ SYMBOL CALL|PUT`** with **no** trailing 🟢/🔴 (fills keep direction emoji). **No** `📋 Symbol execution diagnostics` on single-emit Filled/Rejected. Tier 2+ CALL on Red Day should be **`red_day_tier2_call_blocked`** at Prime (silent — no Telegram). **Jul 27:** `delta_out_of_band` with δ≈0/missing is **transient** (retry; no day-exclude); empty band / `contract_otm_too_deep` / `no_contract_in_band` stay terminal day-excludes where wired.

**Telemetry note**: successful Trendline fills log `TRENDLINE_EXECUTION_HANDOFF` and `TRENDLINE_PIPELINE`. Priority Optimizer entry/exit snapshots follow `ENABLE_EXECUTION_FEATURE_SNAPSHOT` in `configs/Shared.env` (shipped **`true`** — see [PriorityOptimizer.md](PriorityOptimizer.md)).

**Example (intraday fill — Rev Aug 11)**:
```
====================================================================

📏 <b>Trendline TL1 Order Filled</b>
          | DEMO Mode

💼 <b>Result:</b> Filled • execution

1) 🟢 <b>11 • SPY CALL @ $1.11 x 100 =</b>
          $1,221.00 deployed (23.7%)
          Strike: 690.00 • Delta: 0.25
          Setup: ascending_support
          Trigger: bounce_up
          Line Geometry: BULL Line
          Trade ID: TL_SPY_44736_403736

🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~2s + shared backup)
```

**Example (intraday execution reject — Rev Aug 11)**:
```
====================================================================

📏 <b>Trendline TL1 Order Filled</b>
          | LIVE Mode

💼 <b>Result:</b> Rejected • execution

1) ⚠️ <b>APLD PUT</b>
          Setup: ascending_support
          Stage: post_executor
          Reason: delta_out_of_band
          Trade ID: TL_APLD_44712_342478
```

**Example (QG reject — Aug 12 path-opt)**:
```
====================================================================

📏 <b>Trendline TL1 Order Filled</b>
          | DEMO Mode

💼 <b>Result:</b> Rejected • execution

1) ⚠️ <b>XYZ CALL</b>
          Setup: descending_resistance
          Stage: quality_reject
          Reason: RSI:call_78.6>52
          Trade ID: n/a
```

**Example (intraday exit — Rev Jul 24)**:
```
====================================================================

📏 <b>Trendline TL1 Order Closed</b>
          | DEMO Mode

🔰 <b>+45.45% +$555.00</b>
          11 • SPY CALL @ $1.11 x 100 =
          $1,221.00 deployed (23.7%)
          Exit Premium: $1.62 • Net: +$0.51
          Expiry: 2026-08-11
          DTE: 0
          Is_0dte: true
          Entry $: $688.91
          Exit $: $690.36 (+0.21%)
          Max P&L (Bid): +90.09%
          Exit P&L (liq): +45.45%
          Quote Source: exact_quote
          Quote Age: 0.0s
          Mark Quality: live_single_leg
          Exit Grade: true
          Trade ID: TL_SPY_44736_403736
          Reason: price_trail
          Account Balance: $5,709.25
====================================================================
```

**Alert semantics:** title **`11 • SPY CALL @ $1.11 x 100 =`** = qty + symbol + side + entry premium (×100); next line is deployed capital. Strike appears on the fill row only, not the closed contract line.

**Moon / Liq Cap (Aug 17):** optional tags after **Reason**, before **Account Balance**. **`Liq Cap: ⚖️ …`** only when Liquidity Guard sized down. **`Moon: ⭐️`** only when realized premium PnL **> +200%**. Same shared exit layout for ORB1 / TL / BO.
```
          Reason: price_trail
          Liq Cap: ⚖️ Book Ask 25%
          Moon: ⭐️
          Account Balance: $96,586.65
```

---

## ⛓️ **Easy Breakout 0DTE Alerts**

**Enable**: `ENABLE_BREAKOUT_STRATEGY=true`. Same 0DTE monitor / exit manager as Trendline. Formatters: `send_breakout_options_execution_alert` / `send_breakout_position_exit_alert` → shared `send_strategy_*` contracts in `modules/prime_alert_manager.py`.

| Event | Title pattern | Notes |
|--------|-----------------|--------|
| Fill / reject | `⛓️ Breakout Order Filled` → `\| {mode} Mode` | Same body order as Trendline fill; **Setup** = `breakout above orb high` / `breakout below orb low`; **Trigger** = `above_orb_high` / `below_orb_low`; **no** Line Geometry. Optional **`Liq Cap:`** when Liquidity Guard sized down (vol / OI / book / ADV). **Reject (Aug 11):** `⚠️ SYMBOL CALL|PUT` **without** 🟢/🔴 · `Reason:` bare MQ/exec code · **no** diagnostics block. **QG reject (Aug 12):** `Stage: quality_reject` · `Reason: {gate_id}:{reason}` (e.g. `soft_break:brk_0.4<0.55_vol_0.5<0.95_mins_30<60`, `BB_mid:call_20<50`) |
| Exit | `⛓️ Breakout Order Closed` → `\| {mode} Mode` | Same body as Trendline close (Reason → optional **`Liq Cap: ⚖️`** → optional **`Moon: ⭐️`** → Account Balance); common live reasons: `price_stop` (−0.8% BO), `price_trail`, `breakeven`, `scratch`, `end_of_day_exit` |

**Example (fill — Rev Aug 12 · Liq Cap when guard binds)**:
```
====================================================================

⛓️ <b>Breakout Order Filled</b>
          | DEMO Mode

💼 <b>Result:</b> Filled • execution

1) 🟢 <b>3 • TGT CALL @ $0.85 x 100 =</b>
          $256.95 deployed (0.3%)
          Strike: 155.00 • Delta: 0.34
          Setup: breakout above orb high
          Trigger: above_orb_high
          Trade ID: D-260814-TGT-155c-127
          chain_source=live_retry
          slot_capital_target=$600.00
          entry_budget=$600.00
          Liq Cap: ⚖️ Option Vol 0.02

🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~2s + shared backup)
```

Uncapped fills omit `Liq Cap:`. Binding labels (config knobs from `execution_risk` audit · [LiquidityGuard.md §7](LiquidityGuard.md#7-audit-today)):

| `winning_governor` | Alert line |
|--------------------|------------|
| `volume_participation` | `Liq Cap: ⚖️ Option Vol 0.05` |
| `oi_participation` | `Liq Cap: ⚖️ Option OI …` (off at v1.5) |
| `entry_book_depth` | `Liq Cap: ⚖️ Book Ask 100%` |
| `exit_book_depth` | `Liq Cap: ⚖️ Book Bid …` (off at v1.5) |
| `book_depth` | `Liq Cap: ⚖️ Book …` (unused at v1.5) |
| `underlying_adv` | `Liq Cap: ⚖️ Price ADV 0.5%` |

Not shown for `slot_request` · `buying_power` · `position_clamp` · market-quality rejects.

**Example (reject — Rev Aug 11)**:
```
====================================================================

⛓️ <b>Breakout Order Filled</b>
          | DEMO Mode

💼 <b>Result:</b> Rejected • execution

1) ⚠️ <b>RKLB CALL</b>
          Setup: breakout above orb high
          Stage: post_executor
          Reason: bid_size_too_small
          Trade ID: n/a
```

**Example (exit — Rev Jul 24)**:
```
====================================================================

⛓️ <b>Breakout Order Closed</b>
          | DEMO Mode

🔰 <b>+22.03% +$91.00</b>
          7 • AMD PUT @ $0.59 x 100 =
          $413.00 deployed (8.7%)
          Exit Premium: $0.72 • Net: +$0.13
          Entry $: $481.20
          Exit $: $478.55 (-0.55%)
          Max P&L (Bid): +55.93%
          Exit P&L (liq): +22.03%
          Quote Source: exact_quote
          Quote Age: 0.0s
          Mark Quality: live_single_leg
          Exit Grade: true
          Trade ID: BO_AMD_44736_401234
          Reason: price_trail
          Account Balance: $4,846.25
====================================================================
```

**Shared contract-line convention (ORB lotto / Trendline / Breakout)**:
```
{qty} • {SYM} {CALL|PUT} [@ Lotto] @ ${premium} x 100 =
          ${deployed} deployed ({pct}%)
```

---

**EOD note:** Trendline / Breakout P&L rolls into the **unified account** totals in **`END-OF-DAY ORB 0DTE`** — intraday alerts remain path-labeled; the scheduled EOD does **not** include a per-path breakdown subsection (Rev **Jun 11**).

*(Retired: standalone `💎 END-OF-DAY TREND 0DTE` Telegram — merged into unified EOD Jun 9. Retired Jun 11: **`📊 Path breakdown (TODAY):`** subsection in unified EOD.)*

---

## 🔐 **OAuth System Alerts**

**Delivery paths:** Midnight expiry uses **`ETradeOAuth/login/oauth_backend.py`** (direct Telegram). Good Morning is **only** `PrimeAlertManager.send_oauth_morning_alert` via trading-app `POST /api/alerts/market-open` (weekdays 5:30–5:35 AM PT). OAuth Cloud Run `/cron/morning-alert` and `/test-telegram` are **disabled no-ops** (do not Telegram). **`enhanced_oauth_alerts.py`** stub **removed** Jun 25.

### **1. OAuth Token Expiry Alert (Midnight)**

**Trigger**: Cloud Scheduler at 9:00 PM PT (12:00 AM ET) **Sun–Fri nights** (`0 0 * * 1-6` America/New_York = Mon–Sat midnight ET). **Skipped Saturday night** (no Sunday trading session).  
**Purpose**: Alert when production token expires at midnight ET (sandbox deprecated — only production is used for data)  
**Delivery**: Direct Telegram API (works 24/7, independent of main trading system)  
**Independence**: Sends even when trading system is not actively running  
**Guard**: `oauth_backend.py` `/cron/midnight-expiry-alert` also no-ops when local PT weekday is Saturday

**Example**:
```
====================================================================

⚠️ <b>OAuth Tokens Expired</b>
          Time: 09:00 PM PT (12:00 AM ET)

🚨 <b>Token Status:</b>
          E*TRADE tokens are <b>EXPIRED</b> ❌

🌐 <b>Public Dashboard:</b>
          https://easy-trading-oauth-v2.web.app

⚠️ Renew Production Token (used for all data and trading — Demo and Live)

👉 <b>Action Required:</b>
1. Visit the public dashboard
2. Click "Renew Production" (production token only; sandbox not used)
3. Enter access code (see PrivateSecrets.md)
4. Complete OAuth authorization
5. Token will be renewed and stored

====================================================================
```

### **2. OAuth Production Token Renewed**

**Trigger**: Successful production token renewal (via any management portal URL)  
**Purpose**: Confirmation when production tokens are renewed  
**Delivery**: Direct Telegram API call (works 24/7, independent of trading system)

### **3. OAuth Sandbox Token Renewed** *(deprecated)*

**Trigger**: Sandbox token renewal (deprecated — only production tokens are used for data and trading)  
**Purpose**: Informational only. Sandbox tokens are deprecated; the app uses **production tokens only** for data and API. You only need to renew the production token.
**Delivery**: Direct Telegram API call (works 24/7, independent of trading system)

---

## ⚙️ **Alert Configuration**

### **Configuration Files**

**Local Development**: `secretsprivate/telegram.env`
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
TELEGRAM_ENABLED=true
```

**Production**: Google Cloud Secret Manager
- `telegram-bot-token`
- `telegram-chat-id`

**Alert Settings**: `configs/Alerts.env` (Phase 2 trim — Telegram only; email/webhook/Slack removed Jun 18)
```bash
ALERTS_ENABLED=true
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_ENABLED=true
TELEGRAM_MAX_MESSAGES_PER_MINUTE=30
TELEGRAM_MIN_INTERVAL_SEC=1.05
TELEGRAM_SEND_MAX_RETRIES=8
ALERT_COOLDOWN_SECONDS=60
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

**Outbound pacing (Aug 12):** `_send_telegram_message()` serializes all Telegram posts on one lock, spaces by `max(TELEGRAM_MIN_INTERVAL_SEC, 60/TELEGRAM_MAX_MESSAGES_PER_MINUTE)`, and retries on HTTP **429** using Telegram `retry_after`. Fill and reject alerts are **never dropped** for flood — they wait their turn. Grep: `TELEGRAM_SEND | action=pace_wait|throttle_retry|sent`.

Per-level toggles: **`configs/System.env`** — `ALERT_LEVEL_INFO`, `ALERT_LEVEL_WARNING`, `ALERT_LEVEL_ERROR`, `ALERT_LEVEL_CRITICAL`, `ALERT_LEVEL_SUCCESS`.

### **Alert Manager**

The alert manager (`modules/prime_alert_manager.py`) is the **single Telegram gateway** for the RESEARCH app:
- **Send path:** `_send_telegram_message()` → pace/retry lock → `_prepare_research_app_telegram_message()` (HTML normalize + **`🏁 RESEARCH APP 🏁`** desk banner) → truncate → HTML send (+ plain-text fallback)
- **Public API:** session `send_*` formatters + `send_telegram_alert()` / `send_system_alert()` / `send_custom_alert()`
- GCS deduplication — **per Cloud Run service** (`demo_account/` vs FINAL `live_account/`): Good Morning, Signal Collection, ORB capture, EOD — keys include **`alert_dedup_service_token()`**

**Shared trade contracts (Aug 9):**
- **Rolling entry:** `send_strategy_options_execution_alert(strategy_id, …)` — ORB1 watch / ORB2 / Trendline single-emit (thin wrappers: `send_breakout_options_execution_alert`, Trendline `single_emit=True` path)
- **Exit:** `send_strategy_position_exit_alert(strategy_id, …)` — ORB1 / ORB2 / Trendline (wrappers keep call sites stable)
- **Capture:** `send_orb_capture_complete_alert(..., phase="orb1"|"orb2")` — `send_orb2_capture_complete_alert` is a thin wrapper
- **Contract line:** `_format_option_contract_line` (shared by fill/close)
- **Paused / funnel batch:** `send_options_execution_alert` retained for ORB1 reject/zero-fill funnel (and legacy batch summary)

**Active `send_*` methods (RESEARCH — Aug 9):**

| Category | Methods |
|----------|---------|
| **Morning / OAuth** | `send_oauth_morning_alert`, `send_holiday_alert`, `send_oauth_renewal_success`, `send_oauth_renewal_error`, `send_oauth_token_renewed_confirmation`, `send_oauth_alert`, `send_oauth_warning` |
| **Capture / collection** | `send_orb_capture_complete_alert` (`phase`), `send_orb2_capture_complete_alert` (wrapper), `send_orb_capture_failed_alert`, `send_so_signal_collection` |
| **Protection** | `send_portfolio_protection_alert`, `send_red_day_alert` |
| **Shared trade lifecycle** | `send_strategy_options_execution_alert`, `send_strategy_position_exit_alert` |
| **ORB 0DTE** | `send_options_execution_alert` (funnel/batch), `send_options_durability_*`, `send_options_position_exit_alert` → strategy exit, `send_options_aggregated_exit_alert`, `send_options_end_of_day_report` |
| **Trendline / Breakout** | `send_trendline_options_execution_alert`, `send_breakout_options_execution_alert`, `send_trendline_position_exit_alert`, `send_breakout_position_exit_alert` (wrappers) |
| **Legacy (off)** | `send_options_partial_profit_alert`, `send_options_runner_exit_alert` — formatters only; auto-partial disabled |
| **EOD scheduler** | `send_scheduled_unified_0dte_eod` |
| **Generic** | `send_system_alert`, `send_telegram_alert`, `send_custom_alert` |

**Retired SO ETF path (RESEARCH may still hold code; FINAL scheduler skips):** `send_orb_so_execution_aggregated`, `send_trade_exit_alert`, `send_aggregated_exit_alert`, `send_end_of_day_report`, `send_trade_entry_alert`.

**Alert label constants:** `ALERT_LABEL_EASY_ORB_0DTE_OPTIONS`, `ALERT_LABEL_EASY_TRENDLINE_0DTE`, `ALERT_LABEL_EASY_BREAKOUT_0DTE` (active). `ALERT_LABEL_EASY_ORB_ETF` — legacy SO ETF only.

---

## 📅 **Daily Alert Flow**

**Typical Trading Day** (Monday-Friday) — **watch-mode default (Aug 11)**:

1. **9:00 PM PT (Midnight ET) Sun–Fri**: OAuth Tokens Expired alert 🔴 (skipped Saturday night)
2. **5:30 AM PT (8:30 AM ET)**: Good Morning alert 🌅 (schedule: ORB1 Capture · ORB1 Breakouts · ORB2/TL1 unlock)
3. **~6:45 AM PT**: **0DTE ORB1 Capture Complete** → then **TL0** dual-side build (**no build Telegram**; later TL0 fills → **`📏 Trendline TL0 Order Filled`**, closes → **`… Closed`**)
4. **06:50–07:10 PT**: **ORB1 Breakouts** — per-fill / per-reject **`🔮 Breakout Order Filled`** via `send_strategy_options_execution_alert("orb1")` (same single-emit as TL/BO)
5. **07:10–07:30 PT**: Arm TL1 pending (no ORB2/unlock spam; no Signal Collection alert)
6. **≥07:30 PT**: **0DTE ORB2 Capture Complete** → TL1 build → unlock TL + BO
7. **Unlock–12:30 PT**: Trendline (**TL0–TL3**) + Breakout rolling fills/rejects/exits (`📏 Trendline TLn …`); capacity/Red Day blocks **silent** (grep **`execution_alert_suppressed`**); **1h** reject cooldowns
8. **Throughout day**: Exit alerts include **Expiry / DTE / Is_0dte**; reasons include `breakeven` / `scratch` (Aug 11 bands); Exit-Grade Stage B when `exact_quote` promotes
9. **~12:50 PM PT**: Intraday flatten (`flatten_all_paths_for_eod_scheduler`) — unified 0DTE book
10. **12:55 PM PT**: **`POST /api/end-of-day-report`** — **one** unified Telegram EOD: **`🏦 END-OF-DAY ORB 0DTE`**

**Legacy batch rollback only** (`ORB_0DTE_DISCOVERY_MODE=batch` + SC/PP on): 7:30 Signal Collection → PP → batch ORB1 funnel (`send_options_execution_alert`) → then ORB2/TL1. Not the default desk.

**Holidays/Weekends**:
- **Holiday Alert**: Sent at 5:30 AM PT instead of Good Morning alert
- **No Trading Alerts**: System skips trading-related alerts on holidays

---

## 🎨 **Alert Formatting**

### **Rev Jun 29 — Sibling dedup + no desk banner (June 29, 2026)**

| Change | Detail |
|--------|--------|
| **No banner** | FINAL **`_send_telegram_message()`** → **`_prepare_telegram_message_for_send()`** only (HTML normalize) |
| **RESEARCH contrast** | Sibling RESEARCH app injects **`🏁 RESEARCH APP 🏁 RESEARCH APP 🏁`** on every alert |
| **Dedup isolation** | **`alert_dedup_service_token()`** in Good Morning / Signal Collection / EOD keys; markers under **`live_account/alert_markers/`** and **`signal_collection_markers/`** — independent from RESEARCH |
| **Large payloads** | Signal Collection with 50+ 0DTE rows may hit Telegram 3900-char limit — naive truncation can break HTML `<b>` tags (see Troubleshooting) |

### **Rev Jun 23 — Live app Telegram header bar (June 23, 2026)**

| Change | Detail |
|--------|--------|
| **Live app only** | **`send_so_signal_collection()`** prepends **`====================================================================`** + **one blank line** before the title row |
| **No desk banner** | **`_send_telegram_message()`** calls **`_prepare_telegram_message_for_send()`** only — no **`🏁 RESEARCH APP 🏁`** line |
| **RESEARCH app contrast** | RESEARCH sibling injects bar + **`RESEARCH_APP_ALERT_BANNER`** on every Telegram — see [Alerts.md](../1.%20The%20Easy%20ORB%20Strategy%20RESEARCH%20APP/docs/Alerts.md) Rev Jun 29 |
| **Signal Collection §3** | Example uses Jun 23 production soak copy (`MODE=demo` on **`easy-etrade-strategy-live`**) |

### **Rev Jun 25 — PP parity on 0DTE collection pool (June 25, 2026)**

| Change | Detail |
|--------|--------|
| **One PP @ Step 5** | `_run_portfolio_protection_gates_at_step5_before_execution()` runs **after** rank + Signal Collection alert — **not** before |
| **Pool** | `_build_0dte_protection_pool_enriched()` → priority-ranked **0DTE Signal Collection** (`path=0dte_collection_ranked`) — no `build_live_protection_pool()` |
| **Rank path** | `orb_rank_for_0dte` defers Gates **3a/3b** (`ORB_RANK | gate_3a_3b=deferred`); Convex preview log-only on same pool |
| **Daily flow §8** | Steps 2–6 reordered: rank/collection → PP → Trendline build → ORB 0DTE → Trendline watch |

### **Rev Jun 18 — LIVE Step 5 PP ordering + selector diagnostics (June 18, 2026)**

| Change | Detail |
|--------|--------|
| **PP before execution** | **Superseded Jun 25** — PP now runs **after** collection alert on 0DTE pool only (see Rev Jun 25) |
| **Mode label** | **`_resolve_telegram_mode_label()`** from runtime **`MODE`** — fixes prod Jun 18 case where LIVE service showed **`DEMO Mode`** on ORB 0DTE alert |
| **Selector rejects** | **`summarize_single_leg_selector_reject()`** in **`Details`** / diagnostics — Premium/Delta/Spread instead of generic lotto message |
| **PP Action footer** | LIVE **`💰 Action:`** — **`7:30 protection gate active`** · **`0DTE CALLs:`** indented sub-lines · **`Protection pool: 0DTE candidates N | Scanned X`** · **0DTE pool sample only** (no ORB SO / ORB pool) |
| **Trendline fill/close** | **`📏 Trendline Order Filled`** / **`📏 Trendline Order Closed`** · **`qty • SYM SIDE [@ $prem x 100 =]`** |
| **Breakout fill/close** | **`⛓️ Breakout Order Filled`** / **`⛓️ Breakout Order Closed`** · same contract-line convention as Trendline |
| **Tests** | **`test_live_portfolio_protection_gates.py`** — Step 5 ordering assertions |

### **Rev Jun 11 — Global mode header line (June 11, 2026)**

Applies to **all Telegram alerts** in `prime_alert_manager.py` that display **`{mode} Mode`** (DEMO / LIVE):

| Change | Detail |
|--------|--------|
| **Helper** | **`_alert_mode_header_line(mode)`** → `          \| DEMO Mode` / `          \| LIVE Mode` |
| **Layout** | Title row first; **mode on indented second line** (no mode emoji in mode line) |
| **Scope (Live app)** | 0DTE signal collection, ORB capture complete, 0DTE execution/durability/closed/partials/runners/health, Trendline execution/closed/monitor, Portfolio Protection, Red Day, unified **`END-OF-DAY ORB 0DTE`** |
| **Exceptions** | Good Morning, OAuth, holiday, BAD/WEAK DAY emergency (no `{mode} Mode` suffix); retired SO ETF alerts |

### **Rev Jun 11 — 0DTE ORB Capture layout (June 11, 2026)**

Applies to **`send_orb_capture_complete_alert`** (6:45 AM PT):

| Change | Detail |
|--------|--------|
| **Title** | `✅ 0DTE ORB Capture Complete` — mode emoji removed from title |
| **Mode line** | `| DEMO Mode` or `| LIVE Mode` on the line below the title |
| **`Time:` placement** | Moved from header to **bottom** after **`📅 Report Date:`** (Pacific `YYYY-MM-DD`) |
| **Watchlist count** | **Symbols Captured** / **Active** reflect full **`0dte_list.csv`** size (**78** on full success) |
| **Loader** | `load_0dte_symbols()` — tolerates CSV with or without `symbol,tier,category,notes` header |

### **Rev Jun 11 — Unified EOD layout (June 11, 2026)**

Applies to **`send_options_end_of_day_report`** / **`send_scheduled_unified_0dte_eod`** (12:55 PM PT):

| Change | Detail |
|--------|--------|
| **Mode line** | **`| DEMO Mode`** / **`| LIVE Mode`** on the line below the title (aligned with ORB Capture / Trendline execution headers) |
| **Header** | **`🏦 END-OF-DAY ORB 0DTE | 🎮 🍒`** (demo) or **`🏦 END-OF-DAY ORB 0DTE | 💰`** (live) — mode suffix no longer on the same line |
| **Path breakdown** | **`📊 Path breakdown (TODAY):`** subsection **removed** from unified EOD Telegram (helpers remain in `eod_report_helpers.py` for payload only) |

### **Rev 00381 — Scheduled EOD layout (June 5, 2026)**

Applies to **unified 0DTE** scheduled EOD Telegram (12:55 PM PT):

| Change | Detail |
|--------|--------|
| **`Time:` placement** | Moved from header to **last row** after **`📅 Report Date:`** |
| **Thousands separators** | All `$` rows via `_fmt_usd` / `_fmt_usd_signed` (`fmt_usd` in `eod_report_helpers.py`) |
| **Profit Factor** | `_fmt_profit_factor` / `pf_str` — e.g. `11,614.50` when all wins, no losses |
| **Unified EOD header (Jun 9)** | **`🏦 END-OF-DAY ORB 0DTE`** (demo 🎮 🍒 / live 💰) — replaces separate ORB + Trendline EOD headers |
| **Path breakdown (Jun 9–10)** | *(Retired Jun 11)* Optional **`📊 Path breakdown (TODAY):`** — ORB vs Trendline trade counts and today P&L |
### **Rev 00381 — Closed alert payload (June 5, 2026)**

| Path | Change |
|------|--------|
| **ORB 0DTE closed** | Header **`Breakout Order Closed`** (single + aggregated); shared `_format_option_closed_blocks` layout; contract line **`{rank} • {side} {sym} @ …`** (Rev **Jul 1**) |
| **Trendline closed** | **`{qty} • {side} {sym} @ …`** contract line; **`Delta: {entry} - {exit}`**, **`Entry $:`** / **`Exit $:`** (+ underlying move %) |

### **Rev Jun 8 — Deployed % + closed line order (June 8, 2026 — `00367-gsd`)**

| Path | Change |
|------|--------|
| **Deployed %** | **`$X deployed (Y%)` = deployed ÷ account equity at entry** — ORB 0DTE, Trendline |
| **Closed line order** | Contract line (`@ {entry_premium} x 100 =`) → deployed → **`Exit Premium • Net`** |
| **Trendline execution alerts** | **Execution only** (Rev Jun 11): fill + **`post_executor`** reject; not emit/quality/pre-exec — grep **`execution_alert_suppressed`** |
| **Durability follow-ups** | §3a documents **`send_options_durability_*`** trio (May 11+; terminal notice May 12 mixed-batch fix) |

### **Rev 00380 — Execution & Closed trade alerts (June 5, 2026)**

Applies to **ORB 0DTE** and **Trendline 0DTE** execution and closed trade Telegram:

| Change | Detail |
|--------|--------|
| **No header `Time:`** | Title goes straight to body (morning, OAuth, Portfolio Protection, etc. still show PT/ET under title where applicable) |
| **Thousands separators** | `_fmt_usd` / `_fmt_usd_signed` — e.g. `$2,305.50`, `+$11,614.50` |
| **Lotto execution layout** | Line 1: `qty × SYM expiryPstrike @ $premium`; Line 2: `$X deployed (Y.Y%)`; plain `Δ` and `Trade ID:` |
| **Closed capital row** | `$X deployed (Y.Y%)` — **Y = deployed ÷ account equity at entry** (Rev **Jun 8**); order: contract line → deployed → **`Exit Premium • Net`** |
| **ORB 0DTE closed delta** | **`Delta: {entry}`** only — label **`Delta:`** (not **`Entry Delta:`**); Exit Delta and Delta PnL removed from Telegram (Rev **Jul 1** / **00380**) |

### **Execution Alerts**

**Enhanced Formatting**:
- **Bold Priority Rank**: `<b>Rank #1</b>`
- **Bold Priority Score**: `<b>Priority Score: 0.856</b>`
- **Bold Confidence**: `<b>Confidence: 85%</b>`
- **Bold Momentum**: `<b>Momentum: 75/100</b>`
- **Bold Delta**: `<b>Delta: 0.25</b>`

**Trade ID Format** (canonical — Sep 19 compact builder, no mode prefix):

- **New fills:** `COIN-165p-047` (same-day 0DTE) · spreads `SPY-585/590c-546`
- **Demo vs Live** is the **ledger / app**, not the id. Do **not** prefix `D-` or `L-` on new fills.
- **Display:** Event Tape and Telegram strip leftover `D-`/`L-` for operator copy. Stored ledger keys are **not** rewritten.
- **Builder:** `easy0DTE/modules/options_trade_id.build_options_position_id`
- **Broker `clientOrderId` (Live submits):** separate ≤20 **alphanumeric** token via `to_broker_client_order_id` — **not** the hyphenated app Trade ID
- **Legacy:** historical ledger rows may still show `D-`/`L-` / `DEMO_*` / `TL_*` until aged out

### **Exit Alerts**

- Clear exit reason
- Entry/exit prices and premiums (comma-formatted USD)
- P&L highlighted
- **`$X deployed (Y.Y%)`** on closed options alerts — **deployed ÷ account equity at entry** (Rev **Jun 8**)
- **ORB 0DTE**: **`{rank} • {side} {sym} @ …`** contract line → deployed → **`Exit Premium • Net`** → **`Delta: {entry}`** (Rev **Jul 1**)
- **Trendline**: **`{qty} • {side} {sym} @ …`** contract line; same premium/deployed order; **`Delta: {entry} - {exit}`**, **`Entry $:`** / **`Exit $:`** (Rev **00381**)
- Hold time on options exits where applicable
- Trade ID (shortened format)

### **System / error-style alerts**

Use **`send_system_alert(title, message, level)`** or **`send_telegram_alert(message, level)`**. Legacy **`send_error_alert`** / **`send_warning_alert`** were **removed** Jun 25.

---

## 🛠️ **Troubleshooting**

### **Common Issues**

**1. Alerts Not Received**
- ✅ Check Telegram bot token and chat ID configuration
- ✅ Verify Cloud Scheduler jobs are running
- ✅ Check Cloud Run service logs for errors
- ✅ Test bot manually: `https://api.telegram.org/bot<TOKEN>/getMe`
- ✅ Verify bot is not blocked

**2. Duplicate Alerts**
- ✅ Should be fixed with deduplication (GCS-based)
- ✅ Check alert deduplication logic
- ✅ Verify Cloud Scheduler jobs aren't running multiple times

**3. Trade IDs Too Long**
- ✅ Should be fixed with shortened format (Rev 00231)
- ✅ Verify shortened format is being used
- ✅ Format: `DEMO_QQQ_260105_485_488_c_704400`

**4. Missing Bold Formatting**
- ✅ Should be fixed with enhanced formatting (Rev 00231)
- ✅ Check alert manager formatting code
- ✅ Verify HTML formatting is enabled

**5. Too Many Alerts (Spam)**
- ✅ Adjust `TELEGRAM_MAX_MESSAGES_PER_MINUTE` in `Alerts.env`
- ✅ Increase `ALERT_COOLDOWN_SECONDS` (default **60**)
- ✅ Review `ALERT_LEVEL_*` toggles in `System.env`

**6. Alerts Received But Formatting Broken**
- ✅ Check that HTML formatting is enabled (default)
- ✅ Verify message doesn't contain invalid HTML
- ✅ System will auto-fallback to plain text if HTML fails

**7. Many 0DTE names in Signal Collection, few or zero in Options Execution (Rev 00318; zero-alert Rev 00333)**
- ✅ Expected when Convex, Hard Gate, position caps, or chain/momentum gates drop symbols after the scan
- ✅ Read **0DTE pipeline (this run)** in the Options Execution alert (Watchlist → Collection CALL+PUT → Convex-qualified → Hard gate passed → Queued for execution → Execution attempts)
- ✅ When **zero** options execute, you should still receive **`🔮 Breakout Order Filled`** with **`⚠️ NO OPTIONS EXECUTED`** — if missing, grep **`0DTE_ZERO_EXEC_ALERT_SENT`**
- ✅ Optional Telegram note explains when collection underlyings exceed execution attempts; grep logs for `CONVEX_REJECT`, **`0DTE_HARD_GATE_SYMBOL_SUMMARY`**, `HARD_GATE_SUMMARY`, and rejection reasons in the zero-trade alert

**8. Trendline signal fired but no execution Telegram (Jun 8+; policy clarified Jun 11)**
- ✅ **Expected** when the symbol **did not reach executor** — quality skip, signal emit only, capacity/cooldown/red-day, geometry, orchestrator, or risk block
- ✅ Grep **`execution_alert_suppressed`** and **`TRENDLINE_PIPELINE | stage=execution_alert_suppressed`** — system rearms for a later tick
- ✅ **Executor rejects** (after handoff: delta, spread, chain) **do** send **`📏 Trendline Order Filled`** with **`Result: Rejected • execution`**

**9. Durability terminal notice after 7:30 execution**
- ✅ **Observability only** — does not close positions (§3a)
- ✅ Grep **`ORB_0DTE_DURABILITY_BLOCKED_REASON`**, **`ORB_0DTE_MONITOR_ATTACHED`**, **`ORB_0DTE_FIRST_QUOTE_HYDRATED`** for listed trade IDs
- ✅ May appear in **mixed batches** when some siblings are already durable (May 12 reconcile fix)

### **Useful Commands**

```bash
# Test Telegram bot
curl "https://api.telegram.org/bot<TOKEN>/getMe"

# Test sending message
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>" \
  -d "text=Test message"

# View recent logs
gcloud run services logs read easy-etrade-strategy --region=us-central1 --limit 50
```

---

## 📝 **Revision History**

### **August 17, 2026 — Moon star = realized > +200% · moon hold removed**

- ✅ **`Moon: ⭐️`** on shared exit alerts when realized premium PnL **> +200%**. No longer tied to moon hold.
- ✅ Moon hold H2 **removed** (module + `OPTION_MOON_HOLD_*` deleted). Runner moons stay on +60% MFE → 0.50% `price_trail`.

### **August 16, 2026 — Liq Cap ⚖️ on fill+exit · Moon moved under Reason**

- ✅ **`Liq Cap: ⚖️ {reason}`** on fill **and** close when Liquidity Guard sized down. Exit order: Reason → Liq Cap → Moon → Account Balance.
- ✅ **`Moon: ⭐️`** moved from after the contract line to after Reason (still moon-hold trades only).

### **August 12, 2026 — Liq Cap fill line · Moon exit line (local)**

- ✅ **Fill (`Liq Cap:`):** Shared `_trendline_exec_context_line_items` — Liquidity Guard size-downs append one line via `format_liquidity_guard_cap_alert_line()` (ORB1 / TL / BO). Uncapped / BP / position_clamp omit. SoT: [LiquidityGuard.md](LiquidityGuard.md).
- ✅ **Exit (`Moon: ⭐️`):** Shared `send_strategy_position_exit_alert` + `_format_option_closed_blocks` — after contract line when `moon_hold_triggered` sticky (or reason `moon_hold`). Sticky survives `clear_moon_hold_state`.
- ✅ **Deploy:** local only until explicit `deploy now`.

### **Latest Updates (July 24, 2026 - Rev Jul 24)**

**Rev Jul 24 (Jul 24 — unified 0DTE trade alert contract lines, local)**:
- ✅ **ORB / Trendline / Breakout** fill + exit share `{qty} • {SYM} {CALL|PUT} [@ Lotto] @ $prem x 100 =` contract lines
- ✅ Breakout fill/close body aligned with Trendline (no Option Leg / Entry Premium; Trigger on fill; Max P&L (Bid) on close; no Delta on TL/BO close)
- ✅ Docs: examples + Breakout section in Alerts.md

### **Latest Updates (July 1, 2026 - Rev Jul 1 ORB 0DTE closed layout)**

**Rev Jul 1 (Jul 1 — ORB 0DTE closed alert format, local)**:
- ✅ **Contract line** — **`{priority_rank} • {side} {sym} @ {entry_premium} x 100 =`** via `_format_orb_0dte_closed_contract_line` (falls back to **quantity** when rank missing); removed **`on`** between side and symbol (e.g. **`5 • CALL HOOD @ $0.60 x 100 =`**)
- ✅ **Delta row** — **`Delta: {entry}`** only (was **`Entry Delta:`**); Exit Delta / Delta PnL remain off Telegram
- ✅ **Stealth diagnostics** — **Exit Grade** on full close rows (Spread Net Legs / Watchdog Gap / Timeout Clock Frozen removed from ORB1 exit alerts)
- ✅ **Trendline unchanged** — **`{qty} • {side} {sym} @ …`** and **`Delta: {entry} - {exit}`** range on **`📏 Trendline Order Closed`**
- ✅ **Code**: `_format_orb_0dte_closed_contract_line`, `_format_option_closed_blocks`, `_options_exit_alert_block_from_position` in `prime_alert_manager.py`
- ✅ **Tests**: `test_orb_0dte_delta_gate_and_alerts.py`

### **Latest Updates (June 25, 2026 - Alert pipeline cleanup)**

- ✅ **Unified Telegram pipeline** — all formatters → `_send_telegram_message()`; Live tree has **no** DEMO banner
- ✅ **Removed dead methods** — `send_error_alert`, `send_warning_alert`, `send_rapid_exit_alert`, `send_options_health_check_alert`, SO ETF alert family, superseded 0DTE helpers
- ✅ **Deleted** `enhanced_oauth_alerts.py` stub; OAuth midnight alert stays in `oauth_backend.py`
- ✅ **§5** documents retired BAD DAY / rapid-exit formatters (RESEARCH app §5 for ETF BAD DAY)

### **Latest Updates (June 25, 2026 - Rev Jun 25 PP parity)**

**Rev Jun 25 (Jun 25 — PP parity on 0DTE collection, local)**:
- ✅ **Portfolio Protection §4** — one Gate **3a + 3b** @ scheduler Step 5 on `_build_0dte_protection_pool_enriched()` (`path=0dte_collection_ranked`)
- ✅ **`orb_rank_for_0dte`** — PP deferred (`ORB_RANK | gate_3a_3b=deferred`); retired `build_live_protection_pool` @ Gate 3a
- ✅ **Daily flow §8** — rank/collection → PP → ORB 0DTE → Trendline watch
- ✅ **Tests**: `test_live_portfolio_protection_gates.py` updated

### **Latest Updates (June 23, 2026 - Rev Jun 23)**

**Rev Jun 23 (Jun 23 — Trade Signal Collection header + 0DTE funnel labels, local)**:
- ✅ **Trade Signal Collection §3** — Live app header: **`====`** bar + **one blank line** (no DEMO banner); example aligned to Jun 23 soak Telegram
- ✅ **0DTE Pipeline block** — `collection` / `queued_exec` labels; **`queued_exec`** = post-Convex + Hard Gate + rank + Gate 3c cap (≤**10**)
- ✅ **ORB 0DTE execution §3** — **Queued for execution** wired to post-cap queue; **Dropped at Convex** / **Dropped after Hard Gate** funnel lines
- ✅ **`MODE=demo` vs `MODE=live`** — documented when **`ORB Signals Generated`** appears on Live tree
- ✅ **Alert Formatting** — Rev Jun 23 table; DEMO vs LIVE header contrast
- ✅ **Code**: `prime_alert_manager.send_so_signal_collection()` — inline bar in Live tree; body spacing fix

### **Latest Updates (June 18, 2026 - Rev Jun 18)**

**Rev Jun 18 (Jun 18 — LIVE Step 5 PP + alert clarity, local)** — **PP ordering superseded Jun 25**; selector/mode items remain:
- ✅ **`_run_portfolio_protection_gates_at_step5_before_execution()`** — was PP **before** rank (Jun 25: **after** collection on 0DTE pool)
- ✅ **Runtime `{mode} Mode`** — `_resolve_telegram_mode_label()` from **`MODE`** env
- ✅ **Portfolio Protection §4** — LIVE **`0DTE CALLs:`** Action block · **`Protection pool: 0DTE candidates N | Scanned X`** · no ORB pool line
- ✅ **Trendline fill/close** — **`📏 Trendline Order Filled`** / **`📏 Trendline Order Closed`** · **`CALL on SYM`** leg labels
- ✅ **ORB 0DTE selector reject copy** — Premium/Delta/Spread in **`Details`** + diagnostics
- ✅ **Daily flow §8** — reordered Steps 2–5 to match code
- ✅ **Tests**: `test_live_portfolio_protection_gates.py`, `test_summarize_single_leg_selector_reject_*`

### **Latest Updates (June 17, 2026 - Rev Jun 17)**

**Rev Jun 17 (Jun 17 — alert doc + Live 0DTE-only collection, local)**:
- ✅ **Good Morning §1** — multi-broker block, schedule labels, Demo/Live readiness status (shared with DEMO app)
- ✅ **Trade Signal Collection §3** — Live app **0DTE-only** alert body; removed **`ORB Breakouts Ready`** / **`ORB Signals Generated`** from docs and runtime
- ✅ **ORB 0DTE execution** — title **`🔮 Breakout Order Filled`**; **`Details`** + diagnostics CALL/PUT formatting
- ✅ **Protection pool** — `_pending_dte_signals` + `_live_0dte_rank_pool()`; no `_pending_so_signals` in Live app
- ✅ **Code**: `prime_trading_system.py`, `prime_alert_manager.py`, `orb_rank_for_0dte.py` (Live tree)

### **Latest Updates (June 11, 2026 - Rev Jun 11 Live PP)**

**Rev Jun 11 (June 11 — Global mode header line)**:
- ✅ **`_alert_mode_header_line(mode)`** in `prime_alert_manager.py` — shared indented **`{mode} Mode`** row (Live + DEMO apps)
- ✅ **All mode-suffixed alerts** updated: 0DTE signal collection, ORB capture, 0DTE execution/durability/closed/partials/runners/health, Trendline execution/closed/monitor, Portfolio Protection, Red Day, unified **`END-OF-DAY ORB 0DTE`**
- ✅ **Code**: `_format_option_closed_blocks`, `send_options_end_of_day_report`, `_alert_mode_header_line`

**Rev Jun 15 Live PP (June 15 — Step 5 alert sync + Gate 3b DEMO parity)**:
- ✅ **`_ensure_portfolio_protection_gate_at_step5()`** — canonical **`SKIP TRADING`** on Step 5 instance (fixes multi-instance miss when preview-only logged block)
- ✅ **`_apply_portfolio_protection_gate_block()`** — shared flag + emergency close + Telegram (`path=step5_gate_3a` / `path=informational_730_rank`)
- ✅ Gate **3b** moved to **`_run_gate_3b_at_step5_once()`** — skipped on rank loop (`ORB_RANK | gate_3b=skipped`) · DEMO log `Reducing position sizes by X%`
- ✅ Zero-fill path re-syncs Gate **3a** alert if not yet sent
- ✅ Tests **`test_live_portfolio_protection_gates.py`** · [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md) updated

**Rev Jun 11 Live PP (June 11 PM — Portfolio Protection hardening for broker readiness)**:
- ✅ Gate **3b** wired in **`orb_rank_for_0dte`** — SPY/VIX composite risk · `enhanced_red_day_skip_execution`
- ✅ **LIVE protection pool** — 0DTE collection + ranked CALL enrichment (`build_live_protection_pool`) — **no** parallel SO ETF list
- ✅ **`_signal_collection_validation_blocked`** split from **`_red_day_filter_blocked`** — validation failures no longer tier-block CALLs
- ✅ **Informational PP alert** @ rank when 3a/3b fire — **`path=informational_730_rank`** — even with Tier 1 fills
- ✅ **`_refresh_0dte_pipeline_for_portfolio_red_day()`** after PP block
- ✅ Data-quality guard · tests **`test_live_portfolio_protection_gates.py`**

**Rev Jun 11 (June 11 — Portfolio Protection alert order + 0DTE zero-fill routing)**:
- ✅ §4 **`send_portfolio_protection_alert`** when ORB 0DTE **0 fills** due to Gate **3a** policy or Gate **3c** PS floor ★
- ✅ Alert order: **`PORTFOLIO PROTECTION` / `SKIP TRADING`** **before** **`🔮 Breakout Order Filled`**
- ✅ `classify_0dte_zero_execution_outcome()` · `rejection_category=portfolio_protection` on zero-fill execution alert
- ✅ Gate **3a** wiring: **`orb_rank_for_0dte`** (Live app — no `_process_orb_signals` SO path)
- ✅ Log: **`PORTFOLIO_PROTECTION_ALERT_SENT | path=pre_0dte_execution_alert`**
- ✅ Tests: `tests/test_0dte_zero_ps_gate.py` (classification cases)
- ✅ Convex / Hard Gate-only 0-fill days → **no** §4 PP alert (unchanged pipeline reasons)

**Rev Jun 11 (June 11 — unified EOD layout)** *(mode line — see global pass above)*:
- ✅ **`END-OF-DAY ORB 0DTE`** header — mode on second line (`| DEMO Mode` / `| LIVE Mode`); demo **`🎮 🍒`** / live **`💰`** on title line
- ✅ **`📊 Path breakdown (TODAY):`** subsection removed from unified EOD Telegram
- ✅ **Code**: `send_options_end_of_day_report` in `prime_alert_manager.py`

**Rev Jun 11 (June 11 — Trendline execution-only Telegram policy + 0DTE ORB Capture alert)**:
- ✅ **`📏 Trendline Order Filled`** = **position-open execution** alert (not signal emit)
- ✅ **Alerts sent**: executor **fill** + **`post_executor`** reject (`Result: Filled • execution` / `Result: Rejected • execution`)
- ✅ **Alerts suppressed**: quality skip, emit-without-executor, capacity/cooldown/red-day, geometry, orchestrator, risk — grep **`execution_alert_suppressed`**
- ✅ **Code**: `_trendline_failed_row_may_send_execution_alert()`, `execution_attempted`, `_TRENDLINE_EXECUTION_REJECT_ALERT_STAGES` in `prime_trading_system.py`; formatter gate in `send_trendline_options_execution_alert()`
- ✅ **Tests**: `tests/test_trendline_execution_alert_policy.py`
- ✅ **0DTE ORB Capture Complete** — `| LIVE Mode` line; **`📅 Report Date:`** + **`Time:`** footer (§3.2)
- ✅ **`load_0dte_symbols()`** — header-safe CSV load; alert warns when captured count ≠ watchlist size
- ✅ Watchlist docs: **78 symbols including SPX**; Live / DEMO app `0dte_list.csv` must stay identical for count parity
- ✅ **Code**: `prime_alert_manager.py` (`send_orb_capture_complete_alert`), `prime_0dte_strategy_manager.py`, `prime_trading_system.py`

### **Latest Updates (June 9, 2026 - Rev Jun 9 Live)**

**Rev Jun 9 (June 9 — unified 0DTE account alerts)**:
- ✅ **One scheduled EOD** — `send_scheduled_unified_0dte_eod` → **`END-OF-DAY ORB 0DTE`**; `main.py` no longer sends separate ORB + Trendline EOD telegrams
- ✅ **Path-labeled intraday alerts unchanged** — **`Breakout Order Closed`** / **`🔮 Breakout Order Filled`** vs **`📏 Trendline Order Closed`** / **`📏 Trendline Order Filled`**
- ✅ **Path breakdown** subsection in unified EOD *(retired Jun 11 — was today ORB vs Trendline stats from `strategy_type` on closed ledger)*
- ✅ **ORB SO ETF alerts** remain retired in Live app scope
- ✅ **Code**: `eod_report_helpers.py` (`resolve_unified_0dte_eod_payload`, `build_today_path_breakdown`), `prime_alert_manager.py`, `main.py`

### **Latest Updates (June 5, 2026 - Rev 00381)**

**Rev Jun 8 (June 8 — deployed `00367-gsd` / BUILD `00383`)**:
- ✅ **`deployed (%)` denominator** — `account_equity_at_entry` = cash + open entry costs at fill time; applies to ORB 0DTE + Trendline closed + execution rows
- ✅ **Closed line order** — contract line (`@ {entry_premium} x 100 =`) → **`$____ deployed (___%)`** → **`Exit Premium • Net`** (ORB 0DTE + Trendline; `_format_option_closed_blocks`)
- ✅ **Durability follow-ups (§3a)** — `send_options_durability_update_alert`, `send_options_exit_grade_durable_alert`, `send_options_durability_terminal_alert` documented with examples
- ✅ **Trendline execution alerts (Jun 11)** — execution-only policy; capacity/pre-exec silent; **`post_executor`** reject Telegram retained
- ✅ **Trendline execution example** — fill row with Entry Premium → deployed → Trade ID + symbol diagnostics block
- ✅ **Code**: `prime_alert_manager.py` (`_deployed_capital_pct_of_equity`, `_resolve_entry_account_equity`), executors, `prime_trading_system.py`
- ✅ **Tests**: `test_deployed_capital_pct_equity.py`, `test_trendline_red_day_gate.py`
- Session: [Jun 8](doc_elements/Sessions/2026/Jun%208%20Session/SESSION_SUMMARY_JUN08_2026.md)

**Rev 00381 (Jun 5 — EOD layout + closed alert pass)**:
- ✅ **Scheduled EOD** (unified ORB + Trendline): **`Time:`** row moved to **bottom** after **`📅 Report Date:`**
- ✅ **EOD comma formatting** on all dollar rows; **Profit Factor** uses `_fmt_profit_factor` / `pf_str` for large values
- ✅ **Unified 0DTE EOD** header: **`END-OF-DAY ORB 0DTE`** (ORB + Trendline combined; retired separate ORB-only EOD header Jun 2026)
- ✅ **ORB 0DTE closed** header: **`Breakout Order Closed`** (single + aggregated; was **`ORB 0DTE Option Closed`**)
- ✅ **Trendline closed**: **Entry Premium**, **Exit Premium**, **`Delta: {entry} - {exit}`**, **`Entry $:`** / **`Exit $:`** (+ move %); premiums/underlying wired from stealth and EOD close paths
- ✅ **Code**: `send_end_of_day_report`, `send_options_end_of_day_report`, `send_scheduled_unified_0dte_eod`, `resolve_unified_0dte_eod_payload`, `_format_option_closed_blocks`, `send_trendline_position_exit_alert`

### **Latest Updates (June 16, 2026 - Rev Jun 16)**

**Rev Jun 16 (Jun 16 — deploy `easy-etrade-strategy-live-00017-x9n`)**:
- ✅ **Portfolio Protection §4** — `_portfolio_protection_alert_header()`: **`PORTFOLIO PROTECTION`** · **`| SKIP TRADING`** · **`| {mode} Mode`**; pool metrics **Avg MACD** / **Avg VWAP dist** on separate lines
- ✅ **ORB 0DTE execution** pipeline labels: **`Broker routing (selector)`**, **`Execution failed/rejected`**
- ✅ **Trendline** — Red Day tier gate when unified risk on; executor **`_last_execution_block_reason`** → specific **`Reason:`** on reject Telegram; pick/execute spread cap aligned @ 10%

### **Latest Updates (June 5, 2026 - Rev 00380)**

**Rev 00380 (Jun 5 — Execution / Closed alert format pass)**:
- ✅ **No header `Time:`** on ORB SO, Easy ORB 0DTE, and Easy Trendline 0DTE **Execution** and **Closed** trade alerts
- ✅ **Thousands separators** on dollar amounts via `_fmt_usd` / `_fmt_usd_signed`
- ✅ **ORB 0DTE lotto execution**: two-line premium + **`$X deployed (Y.Y%)`** layout; plain `Δ` and `Trade ID:`
- ✅ **ORB 0DTE closed alerts**: **`$X deployed (Y.Y%)`** capital row; **Entry Delta only** (removed **Exit Delta** and **Delta PnL** from Telegram)
- ✅ **Trendline close** title: **`Trendline Order Closed`** with deployed-% and comma USD

### **Latest Updates (June 1, 2026 - Rev 00375 alert hardening)**

**Rev 00375 (Jun 1 - Portfolio Protection alert dedupe + split-tape)**:
- ✅ **`protection_day_split_tape`** trigger documented in §8; median VWAP line in Telegram pool metrics
- ✅ Separate **`_portfolio_protection_alert_sent_today`** — §8 always fires @ 7:30 when Gate 3a/3b blocks (legacy §8b dedupe no longer suppresses)
- ✅ Log token **`PORTFOLIO_PROTECTION_ALERT_SENT`** on successful §8 Telegram
- ✅ Standard Order Execution **`NO TRADES`** reason prefers `portfolio_protection_trigger` from metrics (fixes Gate **3b** composite-risk label)
- ✅ Gate **3b** alert merges pool metrics from Gate **3a** assessment for richer §8 body

### **Latest Updates (May 27, 2026 - Rev 00369)**

**Rev 00369 (May 27 - Unified Portfolio Protection alerts)**:
- ✅ New **`send_portfolio_protection_alert`** — title **`PORTFOLIO PROTECTION — SKIP TRADING`** with `trigger_id`, pool metrics (incl. **% below VWAP**, **median volume_ratio**), ORB/0DTE pool counts
- ✅ Triggers: `red_day_pattern_1`–`3`, **`protection_day`**, **`enhanced_red_day_skip_execution`**
- ✅ GCS snapshot **`blocked_portfolio_protection`**; SO execution alert still sent with **`NO TRADES EXECUTED`** when blocked
- ✅ §5 expanded: BAD DAY / WEAK DAY thresholds, min positions/runtime, **May 27** operator example
- ✅ §8 split: Portfolio Protection (primary) vs legacy **`RED DAY DETECTED`** (collection path)
- ✅ Cross-link [PortfolioProtectionGuide.md](PortfolioProtectionGuide.md)

### **Latest Updates (May 19, 2026 - Rev 00333)**

**Rev 00333 (May 19 - ORB 0DTE zero-execution alert)**:
- ✅ **`send_options_execution_alert(..., no_trades_reason=...)`** renders **`⚠️ NO OPTIONS EXECUTED`** when the 7:30 batch has zero fills and zero pending durable orders
- ✅ Orchestrator **`_send_0dte_zero_execution_alert_if_needed()`**; log token **`0DTE_ZERO_EXEC_ALERT_SENT`**
- ✅ Distinct from SO **`NO TRADES`**; GCS pipeline metrics for cross-instance dedupe
- ✅ Red Day Convex ordering greps: **`0DTE_CONVEX_RED_DAY_PREVIEW`**, **`0DTE_CONVEX_RED_DAY_REFRESH`**

### **Latest Updates (June 3, 2026 - Rev 00379)**

**Rev 00379 (Jun 3 — ORB 0DTE alert UX + deploy **00360-9qn**)**:
- ✅ **Execution alert:** **`X.X% Of Account`** per row when `capital_allocated` missing — `_orb_0dte_execution_capital_pct()` uses deployed premium ÷ balance
- ✅ **Compact trade IDs** on new fills: **`D-SYM-strikep-seq`** / **`L-SYM-strikec-seq`** via `easy0DTE/modules/options_trade_id.py` (ledger retains prior `DEMO_*` IDs)
- ✅ **Exit alert layout:** title **`ORB 0DTE Option Closed`**; one field per line; **`Entry $:`** / **`Exit $:`**; bold on title, PnL, **Trade ID**, **Reason**, **Account Balance**
- ✅ **Delta on exit:** non-zero **Entry Delta** / **Exit Delta** when normalized chain had delta at fill (Exit Delta / Delta PnL removed in Rev **00380**)
- ✅ **Trendline / shared:** no Telegram format change on Trendline path this rev; Trendline execution-intent ledger fix is runtime-only (see [Jun 3 session](doc_elements/Sessions/2026/Jun%203%20Session/SESSION_SUMMARY_JUN03_2026.md))

### **Latest Updates (May 11, 2026 - Rev 00332)**

**Rev 00332 (May 11 - ORB 0DTE two-stage durability + quote_grade in execution alert)**:
- ✅ Options Execution HTML: **`execution_durable`** (Stage A), **`exit_grade_durable`** (Stage B), **`monitored_but_degraded`**
- ✅ Durable follow-up rows: **`quote_grade`**, **`exit_grade_ready`**; status copy when Stage A is satisfied but exit-grade marks are pending
- ✅ Ops grep extended for lifecycle / overextension / chain fallback / spread degraded-exit telemetry (see **Ops / logs** under Easy ORB 0DTE Options Execution)

### **Latest Updates (May 7, 2026 - Rev 00331)**

**Rev 00331 (May 7 - ORB 0DTE durability bucket alert refresh)**:
- ✅ ORB 0DTE Options Execution alert classifies orders into canonical buckets: `durable_executed`, `submitted_pending_confirmation`, `monitoring_active_not_yet_durable`, `chain_health_failed`, `selector_failed`, `durability_failed`, `broker_submit_failed`
- ✅ Pipeline section renames: **`Broker routing (selector)`**, **`Execution failed/rejected`**
- ✅ When orders submitted but not yet durable, alert renders `⏳ ORB 0DTE orders submitted; awaiting durable confirmation` (with a per-symbol pending block) instead of `NO ... TRADES EXECUTED`
- ✅ `🧪 Chain health diagnostics` block documented (retry / relaxed pass / extension rejects / per-failure-type histogram)
- ✅ New telemetry markers: `ORB_0DTE_DURABILITY_ALERT_BUCKETS`, `ORB_0DTE_DURABILITY_RECONCILE_START`, `ORB_0DTE_DURABILITY_RECONCILE_RESULT`
- ✅ Monitoring line in execution alert example aligned to actual code string `All positions tracked (fast loop ~2s + shared backup)`

**Rev doc pass (Jun 11, 2026 — ORB Capture alert + watchlist loader)**:
- ✅ **0DTE ORB Capture Complete** layout: mode line, **`Report Date`** + **`Time`** footer (§3.2 example)
- ✅ **`load_0dte_symbols()`** header-safe parse documented; count validation vs **78**-symbol watchlist
- ✅ Wording: **78 symbols including SPX** (not “78 + SPX”)

**Rev doc pass (Jun 9, 2026 — unified watchlist)**:
- ✅ Single **`0dte_list.csv`** (**78** symbols + header) for ORB capture + 0DTE execution — see [Data.md](Data.md#watchlist--symbol-management) · [WATCHLIST_REVIEW.md](../data/watchlist/WATCHLIST_REVIEW.md)
- ✅ **0DTE ORB Capture Complete** alert — one **0DTE Watchlist Capture** block (no dual core/0dte counts)

**Rev doc pass (Jun 2, 2026 — watchlist curation — superseded by Jun 9 unified list)**:
- ✅ Legacy dual-file policy archived; Live app uses **`0dte_list.csv`** only

**Rev 00330 (Apr 29 - Trendline close/exec format + dynamic watchlist count docs)**:
- ✅ Trendline execution title docs updated to **`📏 Trendline Order Filled`**
- ✅ Trendline close title/docs updated to **`Trendline Order Closed`** with enriched close payload fields (Rev **00380** refines deployed % and delta)
- ✅ `0dte_list.csv` watchlist examples switched from fixed number to dynamic count

**Rev 00329 (Apr 17 - Tier-1 Red Day + selector diagnostics)**:
- ✅ Red Day alert content updated to Tier-1-aware wording (non-Tier-1 CALL/LONG blocked, Tier-1 CALL/LONG continues, PUT/SHORT continues)
- ✅ 0DTE execution diagnostics expanded for ITM spread selector with leg-level stages (`long_leg_unavailable`, `short_leg_unavailable`, `long_leg_liquidity`, `short_leg_liquidity`)
- ✅ Ops grep hints updated to include `0DTE_SELECTOR_DIAG` and `ITM_SPREAD_SELECTOR`
- ✅ Watchlist examples aligned to unified **`0dte_list.csv`** (**78** symbols at Jun 9, 2026)

### **Latest Updates (April 13, 2026 - Rev 00327)**

**Rev 00327 (Apr 13 - Watchlist / doc parity)**:
- ✅ **0dte_list.csv** count in examples; ORB Capture alert reflects **single** watchlist size (historical dual-file examples retired Jun 9, 2026)

### **Latest Updates (April 7, 2026 - Rev 00319)**

**Rev 00319 (Apr 7 - Multi-ledger alerts + doc truth)**:
- ✅ Document **Easy ORB (ETF)**, **Easy ORB 0DTE**, **Easy Trendline 0DTE** headers and **three** Cloud Scheduler EOD reports
- ✅ ETF 15m health: actual **`send_telegram_alert`** titles (**Easy ORB (ETF) — …**); not generic “Portfolio Health: EMERGENCY”
- ✅ **Easy ORB 0DTE** execution/exit/EOD examples aligned with `prime_alert_manager` + **MockOptionsExecutor** system alerts
- ✅ New section **Easy Trendline 0DTE Alerts** (dedicated Trendline fill/exit senders + optional monitor heartbeat path)
- ✅ **send_options_health_check_alert**: removed Jun 25 (was never wired in Live app)

### **Latest Updates (March 25, 2026 - Rev 00318)**

**Rev 00318 (Mar 25 - 0DTE execution alert pipeline + ops hooks)**:
- ✅ **0DTE Options Execution** documents **0DTE pipeline (this run)** (Watchlist, collection underlyings, Convex-qualified, Hard gate queue, execution attempts, drops/rejects)
- ✅ Zero-trade path: grouped rejection reasons, per-symbol detail cap, optional “fewer names than Signal Collection” note
- ✅ Ops: grep **`SO_PIPELINE`**, **`0DTE_DEMO | synthetic_chain`**

### **Latest Updates (February 26, 2026 - Rev 00292)**

**Rev 00292 (Feb 26 - Convex filter 0-pass diagnosis)**:
- ✅ When 0DTE Convex filter rejects all signals: check-by-check failure counts (Volatility, ORB Range/ATR, Red Day, ORB Break, Volume, VWAP, Momentum, Market Regime, Score)
- ✅ Grep-friendly one-liner: `CONVEX_FILTER | 0_eligible | total=N | top_failures: ...`
- ✅ Top 5 per-symbol rejection details at INFO
- ✅ Input LONG/SHORT counts; eligible CALL/PUT breakdown; direction on logs

**Rev 00289 (Feb 26 - CRITICAL: Signal append bug)**:
- ✅ Fixed: Signal creation and append now run inside `if orb_result.should_trade` — symbols that passed all 3 rules now correctly appear in Signal Collection lists (ORB and 0DTE).

### **Previous Updates (February 9, 2026 - Rev 00259)**

**Rev 00259 (Feb 9 - Alerts doc alignment)**:
- ✅ Trade Signal Collection: clarified single alert for SO + 0DTE; 0-signals diagnostic reason documented
- ✅ 0DTE ORB Capture: clarified integrated into ORB Capture Complete (counts only; no separate alert)
- ✅ 0DTE Signal Collection: clarified no separate alert; included in Trade Signal Collection
- ✅ Revision history and footer date aligned

**Rev 00260 (EOD Single Source Consolidation)**:
- ✅ EOD reports consolidated to single source (Cloud Scheduler endpoint only)
- ✅ GCS-based deduplication active for both ORB and 0DTE EOD reports
- ✅ Timing: 12:55 PM PT (3:55 PM ET)

### **Previous Updates**

**Rev 00246 (Jan 19 - 0DTE Strategy Improvements)**:
- ✅ 0DTE Priority Score Formula v1.1
- ✅ Direction-Aware Red Day Filtering
- ✅ Expanded Delta Selection (0.15-0.35)

**Rev 00233 (Jan 8 - Performance Improvements & Data Quality Fixes)**:
- ✅ Secrets Management: All sensitive credentials moved to `secretsprivate/`
- ✅ Good Morning Alert Time Validation: Only sends 5:30-5:35 AM PT
- ✅ Good Morning Alert Deduplication: GCS-based (one alert per day maximum)

**Rev 00231 (Jan 6 - Trade ID Shortening & Alert Formatting)**:
- ✅ Trade ID Shortening: Shortened trade IDs for cleaner format
- ✅ Alert Formatting Enhancements: Bold formatting for key metrics

---

**Alert System User Guide - Complete and Ready for Use!** 🚀

*Last Updated: August 10, 2026*  
*Version: Rev **Jul 1** (ORB 0DTE closed — **`{rank} • {side} {sym} @ …`** contract line, **`Delta: {entry}`**, stealth diagnostic rows). Rev **Jun 15 Live PP** — Step 5 SKIP TRADING sync · Gate 3b @ Step 5 · DEMO parity. Rev **Jun 11 Live PP** — protection pool · informational PP. See revision history.*  
*Maintainer: Easy ORB Strategy Development Team*
