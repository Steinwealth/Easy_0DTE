# Easy 0DTE Frontend

**Last updated:** 2026-11-02  
**Owner:** Easy 0DTE desk  
**Canonical copies:** keep this file **identical** in:

- `0. Easy 0DTE Desk UI/docs/Frontend.md` (implementation home)
- `1. The Easy ORB Strategy RESEARCH APP/docs/Frontend.md` (contract copy)

FINAL APP `docs/Frontend.md` is retired (do not keep a live copy there).

---

## Purpose

Operator desk to **review Easy 0DTE performance** for RESEARCH Demo. On-screen brand: **€£$¥ 0DTE**. FINAL is retired.

- Visual language: FlySwarm-inspired black/lime terminal (`0. Repos Library/semkazz1/Easy FlySwarm/public/`) plus OAuth Token Management Portal tiled-dollar SVG background (`#336600`, `diagonalScroll`)
- Content: **0DTE options** (ORB / Trendline / Breakout) — not crypto
- **Telegram remains the primary pager.** This UI is a **ledger mirror + performance review** surface
- **Truth rule:** display **only** data from the app’s DEMO synthetic ledger or LIVE broker-confirmed ledger. **Never invent** balances, fills, P&L, arming flags, or reject counts

**Implementation home (edit the frontend here):**

`0. Strategies and Automations/0. Easy 0DTE Desk UI`

| Surface | URL |
|---------|-----|
| **Production (Firebase)** | **https://easy-0dte.web.app** |
| Local preview (local machine) | **http://127.0.0.1:PORT/** (`node scripts/preview-hosted.mjs` — binds `127.0.0.1` only; does **not** write production GCS) |
| Custom domain | not published |

RESEARCH does **not** contain Desk UI source. This `docs/Frontend.md` is the contract copy. Unlike `ETradeOAuth/` (nested, deployed via `cloudbuild-oauth.yaml`, excluded from the trading image by `.dockerignore`), the desk stays a **sibling folder**. Production UI is **Firebase Hosting** (static, like the OAuth webapp) — **not** a Cloud Run service.

| What | Where |
|------|--------|
| Desk UI (edit here) | `0. Strategies and Automations/0. Easy 0DTE Desk UI` (`public/`, `src/`, `scripts/preview-hosted.mjs`, `firebase.json`) |
| This contract | identical `docs/Frontend.md` in Desk UI and RESEARCH |
| OAuth renewal webapp | nested `ETradeOAuth/` in RESEARCH — Firebase Hosting + its own Cloud Run backend |

---

## Current status (accurate as of 2026-11-02)

The operator desk is **good enough for Demo review**. **Hosted live = v2.46.30** (Firebase https://easy-0dte.web.app, deployed 2026-10-30; Event Tape long names / Open Positions Path short names, top bar flicker fix, pulse ledger-path guard, 🔮 OR1 Breakout / ⛓️ OR2 Breakout / 📏 Trendline names; training data through 10/30). (preview http://127.0.0.1:PORT/; Background icon button with 51 icons, Theme button with 7 color themes). The hosted desk reads only Cloud Run / GCS objects. RESEARCH Cloud Run **`00619-xr6`** writes `snapshot.json` / `head.json` / `actions.json` / `discovery.json` via `desk_snapshot_pulse.py`. FINAL Live is retired. Demo ledger `starting_balance` is **$100,000** (Oct 24 reseed).

**Local until `deploy now` (Nov 2 calendar):** `public/js/market-calendar.mjs` matches RESEARCH `should_skip_trading`. A listed **Day Before …** skip does not mint a second `PRE_HOLIDAY`, so Training Calendar **2026-11-24 trades**. Hosted v2.46.30 still shows Tuesday 11-24 as pre-holiday until the desk JS is deployed. Full policy: [MarketCalendar.md](MarketCalendar.md).

| Item | Status |
|------|--------|
| Desk UI | **Done** — hosted https://easy-0dte.web.app **v2.46.30** (deployed 2026-10-30) (Background icon + Theme buttons). Node 20+, zero npm deps |
| Brand / chrome | **€£$¥ 0DTE**; slogan **Oppositions Liquidated**; tiled icon (chosen by the **Background icon** button) + `diagonalScroll`; **desk online** pulse; **E*Trade** data-source chip; DEMO; clock PT; version chip; **Background icon** button (shows the current tile icon; cycles in fixed order; saved in `easy0dte.bgIcon`); **Theme** button (cycles Themes 1–7; saved in `easy0dte.theme`); **Music**; **SFX on/off** (topbar order, left to right); footer **`DEMO Mode · Frontend does not mutate Backend.`** |
| Boot | **Press Enter** (start-screen blink, no pixel field) → loading SFX + `Loading . . .` → slam bars + **€£$¥** cycler (start/land `€£$¥`, CRT phosphor, half size, 0.7s fade, 1.5em cells so yen glow is not clipped) → desk + data + **open** SFX. Loading **loops** until open. `prefers-reduced-motion` skips slam motion. |
| Account P&L | KPI strip (Total P&L · Unrealized (open) · Net equity · **Day P&L**), **booked** balance history (**Y = $**, **X = concatenated RTH sessions** 06:30–13:00 PT), account details |
| Open book | Header **Open Positions / N**; `cur%` / mark / `$` = **true-to-live net** (Demo executable bid + fees, Live broker mark); **MFE%** from `metadata.mfe_pct` (peak, not a copy of current) |
| Data mode | **Ledger-truth** — no RNG mock book |
| DEMO source | GCS `research_app/mock_options_history.json` → local `data/research_demo_ledger.json` |
| Hosted DEMO feed | GCS `research_app/desk_public/` — **`head.json`**, **`snapshot.json`**, **`actions.json`**, **`discovery.json`**. **Cloud Run `00618-cvb` is the writer** (`desk_snapshot_pulse.py`). Local `127.0.0.1:PORT` is a **preview** of `public/` and does not publish production objects. `desk-config.js` `headUrl` + `snapshotUrl` + `actionsUrl` + `discoveryUrl`. CORS for `easy-0dte.web.app`. |
| LIVE source | GCS `{ns}/live_options_history.json` → local `data/final_live_ledger.json` (when present). Desk does **not** auto-flip when the trading app goes Live — set `DESK_MODE=live` (and URI) and redeploy hosting |
| Process arming / liq guard | **Blank (—)** on the risk strip — not in the ledger file; not faked. The **DISCOVERY** ticker is a separate scan feed (`discovery.json`), not this slot |
| Lane clock | **Removed (2026-09-18).** Capture windows print on the Event Tape only. |
| Open-book via `/api/options-positions` | **Not wired yet** — DEMO open rows come from ledger `active_positions` |
| Auto-refresh | **Local:** `npm start` pulls the fat ledger when Generation changes (`DESK_LEDGER_SYNC_MS`, default **2s**) + independent `actions.json` / `discovery.json` / `head.json` pull + SSE. **Hosted:** `pollMs` **1500** — HEAD/ETag `headUrl` first, then `actionsUrl` + `discoveryUrl` + `snapshotUrl`. First paint is **Press Enter**; fetch starts on that tap |
| Animation Lab | **Local only** — `0. Easy 0DTE Desk UI/animations/animation-lab/` (HUD + retro packs, collapsed by default). Firebase Hosting **ignores** `animations/**`. Not on https://easy-0dte.web.app |
| Production hosting | **Live** — Firebase Hosting site `easy-0dte` in project `YOUR_FIREBASE_PROJECT` → **https://easy-0dte.web.app**. **No** desk Cloud Run. OAuth site untouched. |

Example DEMO seed: **$100,000** (ledger `starting_balance`, 2026-10-24 reseed). KPI **Net equity** = persisted **`booked_equity`** (seed + realized closes − withdrawals), **not** live MTM cash. Total P&L = booked equity − seed. Subtitle is all-time `%` vs seed plus **Daily:** mean booked daily `%`.

### On-screen surfaces (v2.46.32)

Everything the operator sees. Details in [Frontend panels ↔ data](#frontend-panels--data).

| Layer | Surfaces |
|-------|----------|
| Boot | **Press Enter** gate → **Loading . . .** → **boss slam** (accent bars + `€£$¥` cycler, CRT phosphor, fade) → desk + **open** SFX |
| Chrome | Tiled icon BG (dollar by default); topbar (**€£$¥ 0DTE**, **Oppositions Liquidated**, desk online, **E*Trade** data-source chip with broker dropdown, DEMO, clock, version chip, **Training** button (opens the Training overlay), **Background icon** button (shows the current tile icon; cycles in fixed order; saved in `easy0dte.bgIcon`), **Theme** button (cycles Themes 1–7; saved in `easy0dte.theme`), Music, SFX — left to right); footer **`DEMO Mode · Frontend does not mutate Backend.`** (LIVE Mode when `DESK_MODE=live`) |
| Scan | **DISCOVERY** marquee — **WAITING** 06:30–06:45 PT after Market open; then RTH **SYMBOL·$price**; after hours / weekend **CLOSED**; NYSE closed / low-volume / **pre-holiday skip** **HOLIDAY** |
| ClosedBar | Full-width **Market Closed** overlay (sleeping mascot GIF) over every desk box, just below the 4 KPI cards. Shown exactly when the market itself is closed: after the close, overnight, weekends, NYSE holidays, and early-close afternoons. Never shown while the market is open, including low-volume / pre-holiday skip days (the HOLIDAY marquee explains the skip). See **Market calendar, HOLIDAY marquee & ClosedBar** |
| KPIs | Left → right: **Total P&L** (`$` + all-time `%` + **Daily:** avg daily booked `%`), **Unrealized (open)** (`$` + open-book `%` + open count), **Net equity** (`Account: Demo ledger` in Demo Mode; Live = deployed broker), **Day P&L** (today `$` after fees + signed **Today %** vs booked equity at the day’s open — same as EOD Telegram) |
| Account | **Balance history** (booked curve, RTH X-axis); **Account details** (header hint **DEMO Mode** / **LIVE Mode**; two columns, row-major: Total P&L · Unrealized (open) / Equity · Day P&L / *blank* · Withdrawals / Deployed / cap · Hit rate (all time) / Avg Win · Avg Loss / Best Trade · Worst Trade / Profit Factor · Source) |
| Review | **Signal** (EXIT/ENTRY); **Event tape** (hint **trade alerts**; fills, closes, session bells); **Open Positions / N** (hint `util {deployed ÷ Net equity}% · $X / $cap`, cap = RESEARCH sizing, see [Deployed / cap](#deployed--cap-research-sizing)); **Risk / process** (hint **process metrics**; em dashes); **EOD / day PnL** (hint **DEMO Mode** / **LIVE Mode**; rejects = today's trade-signal rejects from the RESEARCH daily marker (hover: stage / reason); **—** when unreadable) |
| Audio | **373** manifest clips from `soundeffects/` — loading 23 / waiting 1 / open 51 / start 1 / ORB 1 / music 59 / entry 16 / profit 123 / loss 93 / eod 5 (shuffle bag). `soundeffects/moon/` (16) is on disk but not in the manifest. Mute = topbar **SFX on/off**; background music = **Music** chip (off by default) |
| Local-only | Animation Lab (`animations/animation-lab/`) — not on Firebase |

---

## Principles

1. **Read-only** — no order placement or config mutation of trading apps from the browser  
2. **Never hang SSE on the trading asyncio loop** (same constraint as Cloud Run `/health` liveness — see `docs/Cloud.md`)  
3. **DEMO vs LIVE** clearly badged  
4. **Shared UI package**; thin adapters for RESEARCH Demo vs a future Live badge (GCS prefix, ledger file). FINAL is retired.  
5. **Formatting:** money and % use thousand separators (`$3,844.20`, `1,745.0%`); **IDs, titles, names** do not  
6. Missing fields render as **—** / unavailable — never fabricate ON/OFF or synthetic trades  

---

## Run / refresh

**Production:** https://easy-0dte.web.app — static Demo desk; polls the public DEMO ledger copy.

**Local machine (preview only):** http://127.0.0.1:PORT/ — `node scripts/preview-hosted.mjs` serves local `public/` and proxies Cloud Run's four `desk_public` files under `/gcs/`. It does **not** publish production `snapshot.json` / `head.json`. Cloud Run **`00618-cvb`** writes those objects.

```bash
cd "…/0. Easy 0DTE Desk UI"
npm start                   # http://127.0.0.1:PORT/
npm test
npm run pull:demo-ledger    # optional one-shot
npm run pull:live-ledger    # optional LIVE file
```

| Env | Meaning |
|-----|---------|
| `DESK_MODE=demo\|live` | Which ledger shape to load (default `demo`). **Independent** of Cloud Run `MODE`. Demo KPI subtitle is always **Account: Demo ledger** |
| `DESK_BROKER` | Broker data source for the topbar chip: `etrade` · `interactive_brokers` · `schwab` · `tastytrade` (default `etrade`). Hosted: `desk-config.js` `broker`. Only **E*Trade** is online/selectable in the dropdown today. Also used for the Live Net equity subtitle (`Account: E*Trade` / …). Demo KPI subtitle stays **Account: Demo ledger** |
| `DESK_LEDGER_PATH` | Absolute override path to a ledger JSON |
| `DESK_DEMO_LEDGER_FILE` | Filename under `data/` (default `research_demo_ledger.json`) |
| `DESK_LIVE_LEDGER_FILE` | Filename under `data/` (default `final_live_ledger.json`) |
| `DESK_APP_BADGE` | `RESEARCH` / `FINAL` |
| `DESK_DEPLOY_CAP` | **Retired in v2.45.1** (was a hardcoded `$600`). The cap now follows RESEARCH sizing — see [Deployed / cap](#deployed--cap-research-sizing). `desk-config.js` `deployCap` removed too |
| `DESK_RESEARCH_CONFIG_DIR` | RESEARCH `configs/` folder the sidecar reads (read-only) for sizing keys (default `../1. The Easy ORB Strategy RESEARCH APP/configs`) |
| `DESK_RESEARCH_SERVICE` / `DESK_RESEARCH_REGION` / `DESK_RESEARCH_PROJECT` | Cloud Run service whose env the sidecar reads with `gcloud run services describe` (read-only). Defaults `YOUR_SERVICE_NAME` / `us-central1` / `YOUR_GCP_PROJECT_ID` |
| `DESK_SIZING_REFRESH_MS` | Sizing re-read interval (default **120000** = 2 min, min 30s). Async; never blocks the ledger sync. Cache: `data/research_sizing.json` |
| `DESK_LEDGER_SYNC_MS` | Sidecar GCS poll (default **2000**) |
| `DESK_GCS_LIVE_URI` | Override live object (RESEARCH Live: `gs://…/research_app/live_options_history.json`) |

GCS bucket (from app configs): `YOUR_GCS_BUCKET`.

---

## Architecture

```text
RESEARCH Demo Cloud Run 00611-9zz
  DEMO ledger → GCS YOUR_GCS_BUCKET / research_app/
  Telegram EOD
  desk_snapshot_pulse.py → snapshot.json + head.json + actions.json + discovery.json
        │
        ├── local machine: preview-hosted.mjs (read-only proxy of public/ + /gcs/)
        │     → http://127.0.0.1:PORT/   (does not write production)
        └── Firebase Hosting site easy-0dte (static public/)  live v2.46.30
              → https://easy-0dte.web.app
              → HEAD/GET desk_public/head.json + actions.json + discovery.json + snapshot.json (~1.5s)
```

### DEMO vs LIVE (two switches)

Deploying the **trading app** in Live Mode only changes **which file Cloud Run writes**. The desk keeps reading whatever `DESK_MODE` / `desk-config.js` points at.

| Trading app | Writes |
|-------------|--------|
| RESEARCH Demo (current) | `research_app/mock_options_history.json` |
| RESEARCH Live | `research_app/live_options_history.json` |
| FINAL Live (retired) | `final_app/live_options_history.json` (archival) |

When ready for a Live desk: set `DESK_MODE=live`, point URI at the matching object, set `desk-config.js` `mode` + `ledgerUrl`, then say **`deploy now`** for **Firebase hosting**. Do not mix Demo and Live books in one view.

### Phases

| Phase | Scope | Status |
|------:|-------|--------|
| **0** | `Frontend.md` contract | Done |
| **1** | UI shell + account panels | Done |
| **2** | Ledger-truth DEMO/LIVE files | **Done** |
| **2b** | Auto-pull interval (local sidecar) | **Done** (`DESK_LEDGER_SYNC_MS` default **2s** + SSE). Optional `GET /api/account-summary` still todo |
| **2d** | Firebase Hosting site `easy-0dte` | **Done** — **https://easy-0dte.web.app**. Custom domain not published. **No Cloud Run.** |
| **2c** | Overlay `/api/options-positions` + health for LIVE open book / process status | Todo |
| **3** | Observability Postgres history (`EasyORB_0DTE` wiring) | Todo |

---

## Connections (data sources)

### A. Ledgers (desk source of truth today)

| Mode | GCS object | Local desk file | Writer |
|------|------------|-----------------|--------|
| DEMO | `{ns}/mock_options_history.json` | `data/research_demo_ledger.json` | `MockOptionsExecutor` / `persist_demo_close_*` |
| DEMO (hosted) | `{ns}/desk_public/snapshot.json` + `{ns}/desk_public/head.json` + slim `{ns}/desk_public/mock_options_history.json` | (Cloud Run, public-read) | RESEARCH Cloud Run **`00618-cvb`** `desk_snapshot_pulse.py` |
| DEMO (hosted pulse) | `{ns}/desk_public/actions.json` | (Cloud Run, public-read, no-cache) | `MockOptionsExecutor` at fill/close — same moment as Telegram |
| DEMO (hosted scan) | `{ns}/desk_public/discovery.json` | (Cloud Run, public-read, no-cache) | Unified discovery `_run()` after each 24/10s rotate — WAIT_BREAK / CONFIRM / READY, never the open book |
| LIVE | `{ns}/live_options_history.json` | `data/final_live_ledger.json` | `live_options_ledger.py` (broker-confirmed only) |

Namespace: `research_app/` or `final_app/` (`runtime_mode.gcs_namespace()`).

**DEMO seed:** `0DTE_DEMO_STARTING_BALANCE` (config; ledger may show a different persisted `starting_balance`, e.g. `$1,000,000`).  
**LIVE seed reference:** `0DTE_LIVE_STARTING_BALANCE` seeds an **empty** live ledger only. Later `_save_ledger` keeps the persisted `starting_balance` (env must not clobber a live book).

Key DEMO JSON fields used by the desk:

- `account_balance` (Demo cash / MTM — **not** the KPI), `starting_balance`
- `booked_equity`, `peak_equity`, `withdrawals_total` — compact realized book
- `equity_curve[]` — `{ t, equity, delta, kind }` (`seed` / `exit` / `withdraw` / `seed_adjust`); chart prefers this
- `daily_stats` / `weekly_stats` (`total_pnl`, `positions_closed`, `winning_trades`, `losing_trades`, …)
- `closed_positions[]` — **fat Event Tape** only (Demo: last **2 calendar days**, clamped 1–2, then newest **400**; Live: count **400**, no age prune)
- `active_positions[]` — open book (`unrealized_pnl` → Cur%; `metadata.mfe_pct` → peak MFE%)

### B. EOD Telegram (same numbers the desk should match)

- Trigger: Cloud Scheduler → `POST /api/end-of-day-report`
- Send: `send_scheduled_unified_0dte_eod`
- Build: `modules/eod_report_helpers.py` (`resolve_unified_0dte_eod_payload`, `build_ledger_eod_bundle`, `period_pnl_pct`)
- Spec: `docs/Alerts.md` § End-of-Day Reports

| EOD concept | Desk field |
|-------------|------------|
| Account Balances (All Time) | Desk KPI `account.equity` ← `booked_equity` (fallback last compact-curve point). Telegram EOD still prints running DEMO/LIVE **cash** from the trading ledger |
| Starting / seed | `account.seed` ← `starting_balance` |
| All-time P&L | `account.total_pnl` ← equity − seed |
| Today P&L | `account.day_pnl` ← `daily_stats.total_pnl` |
| Today % | `account.day_pnl_pct` = `day_pnl ÷ (equity − day_pnl)` (`period_pnl_pct` / `period_start_balance`; seed fallback when that base ≤ 0). `account.day_open_equity` = the base |
| Today W/L | `account.wins` / `losses` ← daily winning/losing trades |
| Exit alert `Account Balance: $…` | Trading-app running cash (Demo `account_balance` / Live broker). Desk KPI is booked equity |

Today/week **%** in Telegram = period P&L ÷ **balance at period open** (not vs seed). All-time **%** = cumulative ÷ seed. Telegram’s open balance is `account_balance − today_pnl` using Demo running cash `account_balance`; the desk uses the same formula with **booked** `equity` (identical when the book is flat — 2026-09-30: both $5,717.70, open $4,537.50).

### C. Trading HTTP (future desk overlay — not yet consumed)

| Endpoint | Use |
|----------|-----|
| `GET /api/options-positions` | LIVE/DEMO open options + stealth fields |
| `GET /api/positions` | Equity stealth book |
| `GET /api/pending-signals` | Signal queue |
| `GET /api/health`, `/status` | Process health |
| `GET /api/watchlist-status` | Session/universe |

Do **not** attach long-lived SSE to these on the trading event loop.

### D. `observability.py` / `Observability/`

Telemetry writer → Postgres; Grafana dashboards still empty; **EasyORB_0DTE not wired**. Analytics sibling — not the operator desk Phase 2 source.

### E. What is intentionally **not** shown as fact

| UI slot | Why blank |
|---------|-----------|
| Paths armed / liq guard | Not in ledger JSON; would be a live process read |
| Risk-strip “discovery” | Process occupancy — not the DISCOVERY ticker. Ticker uses `discovery.json` (and scan-kind `watch_fsm` only; EXECUTED/FILLED/OPEN dropped) |
| Reject counts | **v2.45.2:** counted read-only from the RESEARCH daily marker `{ns}/daily_markers/{PT date}.json` → `trendline_geometry.candidates` (already pulled each tick for Discovery). A candidate counts once (key PT date + trade_id, e.g. `BP:bear:trendline0`) when its `state_reason` (else `setup_payload.attempt_consumed_reason`) starts `trade_profile_reject:` (stage trade_profile), `exec_reject:` (execution), `quality_reject:`, `pc_oi_filter_reject:`, `dir_pct_filter_reject:` or `chain_skew_filter_reject:`, the codes behind each **Result: Rejected** Telegram. Unioned per PT date in `data/desk_rejects.json`; published as `metrics.rejects` + `metrics.rejects_breakdown` (`by_stage`, `by_reason`) in `desk_public/snapshot.json`. A ledger `daily_stats.rejects` integer still wins. Not counted: ORB / Breakout path rejects (not persisted, trade_id n/a) and capacity waits (`slot_unavailable_reject`, `slot_side_cooldown`). No marker or no geometry for today → **—** |
| Lane ORB1/TL/ORB2 clock boxes | **Removed.** ORB Capture 1/2 + Market open/close are Event Tape rows (`session.clock`) |

---

## Frontend panels ↔ data

| Panel | Behavior |
|-------|----------|
| Topbar | Brand **€£$¥ 0DTE**, slogan **Oppositions Liquidated**, **desk online** pulse, **data source** chip (`desk-config.js` `broker` / `DESK_BROKER`, currently **E*Trade**), DEMO/LIVE, clock PT, version chip, **Training** button (opens the Training overlay), **Background icon** button (shows the current tile icon; cycles in fixed order; saved in `easy0dte.bgIcon`) (see Background icon), **Theme** button (cycles Themes 1–7; saved in `easy0dte.theme`) (see Color themes), **Music**, **SFX on/off** — this is the exact left-to-right DOM order in `index.html` (`#version` → `#btn-training` → `#btn-bg-icon` → `#btn-theme` → `#btn-music` → `#btn-sfx`). On narrow screens the chips wrap onto extra rows; nothing overflows. Clicking the data-source chip opens **E*Trade** / **Interactive Brokers** / **Charles Schwab** / **TastyTrade**; only **E*Trade** is enabled. |
| Enter gate | First paint is BG + centered **Press Enter** (start-screen stepped blink; no scrolling-dot tile). Ledger fetch does not start until that tap (`pointerdown` + `click` retry) |
| Loading overlay | After Press Enter: `Loading . . .` until `account.equity` is populated; `pointer-events: none`; **loading** SFX **loops** through the slam |
| Boss slam | After Loading succeeds, before the desk un-gates: accent (`--lime`) close-bars + **€£$¥** symbol cycler (starts with the bars, lands on `€£$¥`). CRT phosphor glow on the glyphs: tight `0 0 2px` accent at 60% plus soft `0 0 14px` accent at 35% (`rgba(var(--accent-rgb), …)` on `.boss-entry .copy` and `.boss-cycler span`) so the `$` counters stay open and clip boxes do not bloom; reel cells **1.5em** so yen’s top halo is not sliced. Half size `clamp(1.3rem, 4.5vw, 3rem)`. Landed letters **fade 0.7s**, then the overlay drops. `prefers-reduced-motion` skips bar/cycler/fade motion |
| Footer | **`DEMO Mode · Frontend does not mutate Backend.`** (same `DEMO`/`LIVE` token as RESEARCH `MODE`) |
| ClosedBar | Full-width Market Closed overlay (sleeping mascot). After EOD / overnight / weekend / NYSE holiday / early-close afternoon. Hidden in RTH on low-volume and pre-holiday skip days |
| Lane strip | **Removed.** Do not restore the three ORB1/TL/ORB2 boxes |
| Discovery ticker | Scrolls with the page (not sticky). After Market open **06:30–06:45 PT**: **WAITING** marquee (`The U.S. Stock Market has Opened!!!` … Trade Discovery begins 6:45am PST). Then RTH chips = **SYMBOL·$price** from GCS `desk_public/discovery.json` (Cloud Run after each unified 24/10s rotate). **EXECUTED / FILLED / OPEN are dropped**. After hours / weekend: **CLOSED** marquee (`public/js/market-calendar.mjs`); NYSE closed / low-volume / **pre-holiday skip**: **HOLIDAY** marquee at **4×** the closed-note speed. Edge fade is a **same-RGB** panel-color alpha gradient (not `-webkit-mask-image`, no `isolation`) so mobile Chrome does not paint a black bar. CLOSED / WAITING / HOLIDAY clock text updates in place so the track does not restart. Hosted `discovery.json` is **live** on **`00618-cvb`**. **v2.45.9:** Cloud Run is the only writer of `discovery.json`. The page never steps back to an older scan. Colors = scan state (accent READY · `--entry` CONFIRM · muted WAITING_FOR_BREAK). A priceless chip may show the last real price seen today with its PT time (`@07:07`), dimmed after 2 min |
| KPI: Net equity | `account.equity` ← **`booked_equity`** (not Demo `account_balance` MTM). Color `--equity` (Theme 1 `#89CFF0`; see Color themes). Subtitle is **`Account: Demo ledger`** in Demo Mode; Live uses the deployed broker (`Account: E*Trade` / `Interactive Brokers` / `Charles Schwab` / `TastyTrade`). Seed stays in Account details. |
| KPI: Total P&L | `account.total_pnl` (`$`) + `account.total_pnl_pct` (all-time vs seed) + **Daily:** `account.daily_avg_pct` (mean booked daily `%` from the equity curve, America/Los_Angeles dates; seed-only and trailing same-equity “now” holds skipped). Not peak equity. |
| KPI: Unrealized (open) | `account.unrealized_pnl` (`$`, `--unreal-up` gains / `--red` losses, Courgette script font) + open-book `%` + `N open` |
| KPI: Day P&L | Value = `account.day_pnl` (same `$` as Account details Day P&L, all-inclusive after fees). Subtext = signed **Today %** `account.day_pnl_pct` (client recomputes `day_pnl ÷ (equity − day_pnl)` when a snapshot lacks it) — matches EOD Telegram `period_pnl_pct`. `--profit` / `--red`. Missing → **—**. **Day rollover (v2.45.6):** on a trading day, from 00:00 PT, day fields dated before today (or stamped today without any closes today) show **$0.00 / 0.00% Today** until RESEARCH writes today's `daily_stats` (its reset fires at the first loop after the 6:30 PT open). On weekends and holidays the last session's value stays, dated (`-19.40% Oct 1`; Account details `… · Oct 1`; chart hint `Oct 1 …`). Today hit rate moved off the strip; all-time hit rate stays in Account details |
| Balance history | Prefer stored `equity_curve` + `booked_equity`. Fallback: remaining fat closes (seed + realized net − withdrawals). **Not** live MTM. **Y = $**, **X = concatenated RTH sessions** (06:30–13:00 PT, no overnight gap) |
| Account details | Header hint **DEMO Mode** / **LIVE Mode** (same `DEMO`/`LIVE` token as RESEARCH/FINAL `MODE=demo|live` and the topbar chip). Two columns, row-major: **Total P&L** · **Unrealized (open)** / **Equity** (`--equity`) · **Day P&L** / *blank* · **Withdrawals** (`account.withdrawals`, `--withdrawals`, Theme 1 `#85bb65`) / **Deployed / cap** (`account.deployed` / `account.deploy_cap`: per-position cap: sum cap **off** → Net equity × `0DTE_TRADING_CAPITAL_PCT` ÷ `0DTE_SLOT_COUNT`, e.g. `$0 / $735.13`; sum cap **on** → min(that, sum cap); RESEARCH sizing unknown → `$0 / —`; hover shows the formula) · **Hit rate (all time)** (`lifetime_hit_rate` + lifetime W/L) / **Avg Win** · **Avg Loss** / **Best Trade** · **Worst Trade** / **Profit Factor** (`X.XX` / `∞`) · **Source** (`E*Trade, DEMO Mode`). **All-time (v2.45.7):** Hit rate / Avg Win / Avg Loss / Best / Worst / PF are from account open → now (`equity_curve` exit deltas, which survive RESEARCH's 2-day `closed_positions` prune; else the full close list; hosted keeps the published life). Day W/L stays on the EOD / day panel only. Missing → **—**. |
| Signal | Latest ledger **exit** and **entry** (or empty). Kicker is **`EXIT Last ledger close`** / **`ENTRY Last ledger fill`** on one line. EXIT value is money at EXIT size then ticker (`-$303.00 · MRVL`). ENTRY value is contracts × premium at ticker size (`10 × $0.59 · MRVL`), not deployed `$590.00`. ENTRY status + fill detail use `--equity-info` (= Net Equity color, except Theme 2 `#0369a1`). New entries play **entry** SFX; new exits play **profit** / **loss** SFX by realized % |
| Event tape | Header hint **trade alerts**. Fills (`entry_time`) + remaining fat closes (`closed_at`) + **session bells** merged on the **client clock** (newest first). Bells: emoji **Market open/close** on **trading days only** (same weekend/holiday/`should_skip_trading` gate as the app, Telegram, and Discovery). **ORB Capture 1/2** only when real capture stats exist — never clock-invented 133 / 3.0s. Demo fat rows: **2 days** + **400**. Entry and exit row headers use `--text` |
| Open Positions / N | `active_positions`, sorted **high → low `cur%`**. Columns: **trade_id**, **path**, **symbol**, **side**, **premium**, **cur%**, **MFE%**, **deployed**. Mark = Demo live bid (Live = broker); Cur% / `$` = same true-to-live net as the Event Tape (bid fill minus $0.65/contract/side fees); MFE% = peak `metadata.mfe_pct`. Header hint `util {X.XX}% · $deployed / $cap`: util % = total open deployed ÷ **Net equity** (booked, `metrics.util_pct`, 2 decimals; **—** without equity). `$cap` = `metrics.deploy_cap`, same as Account details **Deployed / cap** (per-position cap: sum cap off → Net equity × 90% ÷ 7; on → min(that, sum cap); unknown → **—**). Never a hardcoded `$600` |
| Risk / process | Header hint **process metrics**. **Paths armed**, **Discovery** (process occupancy, not the ticker), **Liq guard** — all **—** unless a live process overlay exists. Note + em dashes. Not faked from the ledger |
| EOD / day PnL | Header hint **DEMO Mode** / **LIVE Mode** (same token as the topbar chip). Today PnL + W/L from ledger daily stats. **rejects** = `metrics.rejects` (else `eod.rejects`): today's unique trade-signal rejects from the RESEARCH daily marker (see Reject counts) or a ledger integer; hover shows the stage / reason breakdown; **—** when no source is readable (never derived from closes). After **12:50 PT** flatten (0 open, page already watching): **eod** SFX profit / loss / perfect |

### Market calendar, HOLIDAY marquee & ClosedBar

Full calendar policy (skip kinds, pre-holiday rule, observed holidays, early closes, clocks, Telegram): [MarketCalendar.md](MarketCalendar.md). This section specifies the frontend side.

**Module:** `public/js/market-calendar.mjs` computes the calendar in the browser (no calendar data is fetched). It mirrors RESEARCH `modules/dynamic_holiday_calculator.py` `should_skip_trading`: same holiday rules (Easter-based Good Friday, NYSE Rule 7.2 so New Year's moves Sunday → Monday only; Juneteenth / July 4 / Christmas observe Saturday → Friday and Sunday → Monday), same `LOW_VOLUME` list, same names, and the same `PRE_HOLIDAY` rule (the previous **market session** before a bank holiday or a standalone low-volume day, so a Friday is disabled with a Monday skip; a listed **Day Before …** skip is already that session and does not mint a second pre-holiday). **2026 checks:** 11-24 trades; 11-10 before Veterans Day; 10-09 before Columbus Day; 12-22 trades; 12-23 / 12-24 / 12-25 skip; 12-28 trades; 12-30 / 12-31 skip. Dates are PT calendar days; RTH is 06:30–13:00 PT (≡ 09:30–16:00 ET year-round), early close 10:00 PT. Training Calendar, Discovery HOLIDAY marquee, session bells, and Day P&L rollover all use this same `marketDayStatus`. Local until `deploy now`.

| Function | Spec |
|----------|------|
| `holidaysForYear(year)` / `holidayOnYmd(ymd)` | Map of skip days `{name, reason}`; `PRE_HOLIDAY` entries carry `upcoming = {ymd, name, reason}` of the skip day they precede. A listed name starting **Day Before** is not a `PRE_HOLIDAY` source |
| `isEarlyCloseYmd(ymd)` | Black Friday; July 3 when July 4 is Tue–Fri; Dec 24 when Christmas is Tue–Fri |
| `marketDayStatus(ymd)` | Single day status: `{weekend, nyseClosed, tradingDisabled, reason, name, earlyClose}`. `nyseClosed` = weekend or `MARKET_CLOSED`; `tradingDisabled` = weekend or any skip reason |
| `isMarketClosedAt(nowMs)` | True on weekends and `MARKET_CLOSED` days, before 06:30 PT, at/after 13:00 PT, and at/after 10:00 PT on early-close days. `LOW_VOLUME` / `PRE_HOLIDAY` days follow normal RTH |
| `resolveMarketClosed(nowMs, state)` | Uses a Cloud Run flag from the snapshot (`market_session.market_open` + `as_of`, or top-level `market_open` + `updated_at`) when ≤ 5 min old (`MARKET_FLAG_MAX_AGE_MS`); otherwise `isMarketClosedAt` |
| `isSessionBellYmd(ymd)` | False on weekends and all three skip kinds: no Market open/close bells, Day P&L stays on the last real session |
| `discoverySession(nowMs, capture)` | Marquee mode in priority order: **HOLIDAY** (any skip day, all day) → **CLOSED** (weekend / outside RTH / after an early close) → **WAITING** (06:30 PT until today's ORB#1 capture) → **DISCOVERY** |

**HOLIDAY marquee** (`formatHolidayMarquee`), label `HOLIDAY`, scrolls at 4× the CLOSED-note speed:

| Kind | Emoji | Subject | Why line |
|------|-------|---------|----------|
| `MARKET_CLOSED` | 🏖️ | `It's {name}` | U.S. stock market is closed today. Trading disabled to preserve capital quality. |
| `LOW_VOLUME` | 🎃 | `It's {name}` | Market is open, but volume is typically low on this holiday. Trading disabled to preserve capital quality. |
| `PRE_HOLIDAY` | 🏖️ | `Day before {upcoming name}` | Trading disabled today: day before {upcoming name} ({its date}), pre-holiday low-volume risk session. |

Text: `{emoji} Today is a Holiday! - {subject}! {emoji} {Weekday, Month DD, YYYY} 🎭 No Trading Today! ☁️🏖️🏝️⛱️🌤️☁️☁️ 🚫 Status: Trading DISABLED today. 💡 Why?: {why} ✅ System Status: Normal 🔍 Next Trading: System will resume at next normal trading day`. If `upcoming` is missing, the name comes from the `Trading Session Before …` entry and no date is shown. (Telegram uses 🛡️ `Pre-Holiday Risk Session!` for the same day; see MarketCalendar.md §9.)

**CLOSED marquee:** `🌓 The U.S. Stock Market is Closed! 🛄 It is {h:mm AM/PM} on {Weekday, Month DD, YYYY} 🌐 System will resume trading after next market open ✅ System Status: Normal`. **WAITING marquee:** `⏰ The U.S. Stock Market has Opened!!! …` until ORB#1 capture.

**ClosedBar** (`#market-closed-banner` in `index.html`, `paintMarketClosedBanner` in `app.js`):

- **Visibility:** shown exactly when `resolveMarketClosed(now, lastDeskState).closed` is true; hidden otherwise. Checked every 30 s and on every marquee paint; the DOM is touched only when the state flips.
- **Content:** lime strip `.mcb-strip` with **"The Market is Closed!"** (`.mcb-text`, monospace, `clamp(1rem, 2.6vw, 1.9rem)`) and the mascot `img.mcb-gif` on the right.
- **Image:** one constant, `CLOSED_BAR_IMAGE = '/img/closedbar/sleeping/mascot-sleeping.gif?v=…'` in `public/app.js`. Variants live under `public/img/closedbar/<name>/`; change the constant to switch.
- **Lazy load:** the `<img>` has no `src` in HTML. `src` is set only the first time the bar shows, so the GIF never downloads while the market is open.
- **Layout:** `position: absolute`, `z-index: 900`, spans the window width with a small left/right margin (`clamp(0.35rem, 1vw, 0.75rem)`), anchored just below the 4 KPI cards and layered over every desk box (Balance history, Account details, …); scrolls with the page.
- **Sizing:** GIF `width: 200px; height: auto` (HTML `width=200 height=210`; v2.46.6, was 300px), `max-width: calc(100% - 2 × inset)` on narrow screens; right inset `--mcb-inset: clamp(6px, 1.1vw, 14px)`. **v2.46.7 proportional position:** GIF `margin-right` (and strip right padding = 200px + gap + 0.75rem) use `--mcb-gif-right: max(var(--mcb-inset), calc(25% - 100px))` (% of bar width): centers the 200px GIF at 75% of the bar (~70% left / ~20% right on very wide bars); on narrow bars the inset wins (crossover ≈ 424–456px), so mobile keeps the edge-hugging look. Strip height `clamp(44px, 5vw, 72px)`, placed so the GIF overhangs **2 : 1** above : below. Strip right padding reserves the GIF width (reduced at ≤ 640px).

**Cross-check:** `scripts/test-calendar-crosscheck.mjs` (in `npm test`) runs the RESEARCH calculator read-only via `python3` for every day 2026–2036 and fails on any mismatch with `marketDayStatus` (skip, reason, name) or `isEarlyCloseYmd`. The trading loop and morning Telegram both use `should_skip_trading`, so a pass means desk, trading gate and Telegram agree. Skips with exit 0 if the RESEARCH folder or `python3` is missing. Also: `test-session-marquee.mjs` (marquee copy per kind), `test-market-closed-banner.mjs` (ClosedBar visibility).

### Sound effects (desk UI only)

**Music shuffle bag (v2.45.5).** Background music walks a persisted shuffle (`localStorage.desk_music_bag`) over every `manifest.music` track: each track plays once per cycle and loops until **Music off → on**, which advances to the next one. Reloads and revisits continue the same cycle. Interruptions (lock, waiting window, ORB hold, retries) resume the current track. A new cycle never opens with the track that just played. Clearing `desk_music_bag` starts a fresh order.

**Gesture retry (v2.45.4).** A click / key / tap retries only a cue whose `play()` was blocked and never played: loading / waiting loops while unplayed, a oneshot (entry, exit, open) once, and only within 10s. Background music `play()` failures are tracked separately (`musicBlocked`) and never trigger an SFX retry; a music load still in flight is not restarted by clicks.

Clips are **desk-frontend only**. Trading Cloud Run never plays audio. Telegram is still the pager.

**Source of truth:** desk `0. Easy 0DTE Desk UI/soundeffects/` (~19 MiB, `.wav` / `.ogg`). RESEARCH and FINAL do **not** keep a copy — trading Cloud Run never plays audio. Add or replace files in the desk library, then sync into `public/sounds/` for hosting.

**Copy into the desk** (and rewrite `public/sounds/manifest.json`):

```bash
cd "…/0. Easy 0DTE Desk UI"
npm run sfx:sync
```

`scripts/deploy_desk_hosting.sh` runs that sync before Firebase upload. Served **same-origin** from https://easy-0dte.web.app/sounds (long cache). Do **not** put the library on the 1GB .com host.

Player: `public/js/sfx.mjs`. Mute is **only** the topbar **SFX on/off** chip (`localStorage` key `desk_sfx`).

#### Cue map (counts as of 2026-09-30, desk `soundeffects/`)

| Cue | When | Folder | Files |
|-----|------|--------|------:|
| loading | After **Press Enter**, while overlay + slam are up (**loops** until open) | `loading/` | 23 |
| waiting | After **Market open** posts on the Event Tape (06:30 PT) until **ORB Capture 1** posts, or 06:45 PT | `waiting/` | 1 |
| open | Desk + data appear after the slam fade (`holdOpenForBoot` / `releaseOpenForBoot`) | `open/` | 52 |
| start | **Market open** bell posts on the Event Tape | `start/` | 1 |
| ORB | **ORB Capture 1** / **ORB Capture 2** bells post | `ORB/` | 1 |
| music | Background music when the **Music** chip is on: shuffle bag, one track loops until Music off → on (v2.45.5); pauses only for the waiting loop | `music/` | 44 |
| entry | New ledger **fill** after the desk is already open | `entry/` | 16 |
| profit | New Signal **EXIT** with realized % **> 0** | `profit/{bucket}/` | 123 |
| loss | New Signal **EXIT** with realized % **≤ 0** | `loss/{bucket}/` | 93 |
| eod | After **12:50 PT**, 0 open, tab was already watching | `eod/{kind}/` | 5 |

**373** manifest clips total (profit 24 / 35 / 27 / 10 / 16 / 6 / 5 by bucket; loss 22 / 37 / 24 / 10; eod profit 2 / loss 1 / perfect 2). `soundeffects/moon/` has 16 more files that `write_sfx_manifest.mjs` does not list (389 audio files on disk). `public/sounds/manifest.json` matches `soundeffects/` after each `npm run sfx:sync` (the deploy script runs it). Loading **loops** until **open**. Waiting **loops** from Market open (06:30 PT) until ORB Capture 1 (or 06:45 PT). Entry / profit / loss / eod are **one-shot**. Open is held through the slam and plays when the desk un-gates. Historical last close on first paint does **not** play profit/loss. Historical open fills do **not** play entry. Only a **new** fill or exit after the desk is already open.

#### Profit / loss / EOD buckets

Realized `%` for SFX buckets = **true-to-live net** dollars / (**premium + opening fees**) — `trueToLiveCostBasis` / stamped `trade_economics_entry.total_cost_basis`. Same % as the Event Tape, Signal, and Telegram.

| Profit folder | When |
|---------------|------|
| `profit/0_29` | `0 < pct < 30` |
| `profit/30_99` | `30 ≤ pct < 99` |
| `profit/99_199` | `99 ≤ pct < 200` |
| `profit/200_249` | `200 ≤ pct < 250` |
| `profit/250_499` | `250 ≤ pct < 500` |
| `profit/500_999` | `500 ≤ pct < 1000` |
| `profit/1000_above` | `pct ≥ 1000` |

| Loss folder | When (`mag = abs(pct)`) |
|-------------|-------------------------|
| `loss/0_19` | `0 ≤ mag < 20` |
| `loss/20_39` | `20 ≤ mag < 40` |
| `loss/40_49` | `40 ≤ mag < 50` |
| `loss/50_100` | `mag ≥ 50` |

| EOD folder | When |
|------------|------|
| `eod/perfect` | Day P&L **> 0** and **0** losers |
| `eod/profit` | Day P&L **> 0** and at least one loser |
| `eod/loss` | Day P&L **≤ 0** |

Each cue picks a **random** file in that folder (shuffle bag; skip clips that fail to decode). Equal chance across files in the folder.

#### Enter unlock and browsers

Chrome/Safari/iOS block audible media until a real tap. First paint is **BG + Press Enter** (same size/shape as the loading chip). Ledger fetch does **not** start until that tap.

- Desktop and phone: **pointerdown** on **Press Enter** starts loading audio in that tap; **click** retries if the first play was blocked (keyboard Enter uses click)
- Do **not** `preventDefault` on `touchend` — that swallowed the click retry and forced a second tap on the page
- While the Enter gate is up, the desk **pre-selects** one loading clip and one open clip and **downloads both** into memory (HTTP preload + blob). Press Enter plays loading from cache; desk reveal plays open from cache. Randomization still happens (new pair each page load / after open)
- That tap plays **loading** first (the only HTML `play()` in the gesture). Web Audio resume + silent keep-alive stay in-graph so iOS/Safari do not suspend during the ledger wait. A second HTML `play()` (silent keep-alive) in the same tap was stealing the unlock
- Loading **continues through the boss slam**. **open** is held until `revealDeskShell` (`holdOpenForBoot` / `releaseOpenForBoot`)
- **open / entry / profit / loss / eod** play through that already-running graph (no extra screen tap)

Supported: Chrome, Safari, Edge, Firefox (desktop + Android); all iOS browsers (WebKit). **Not** Internet Explorer (`nomodule` message).

#### Adding clips

1. Drop `.wav` / `.ogg` into the matching desk `soundeffects/…` folder (names can be anything; the manifest lists whatever is on disk)
2. `npm run sfx:sync` in the desk folder
3. Say **`deploy now`** for Firebase hosting so https://easy-0dte.web.app picks them up

Do not edit `public/sounds/` by hand as the source — `sfx:sync` rsyncs `--delete` from desk `soundeffects/`.

### Color themes (Theme button)

The desk has **7 color themes**, chosen with the topbar **Theme** button. Themes change colors only: layout, data, fonts (Courgette for Unrealized, mono elsewhere), sounds and the mascot GIF are the same in every theme, and the desk stays read-only.

#### Theme button and selection

| Item | Spec |
|---|---|
| Button | `#btn-theme` (`.chip.theme`) in the topbar, right after the **Background icon** button and immediately left of **Music** and **SFX** (order: version chip → Training → Background icon → Theme → Music → SFX). Cycles Themes 1 → 7 → 1; saved in `localStorage` `easy0dte.theme`. Label is always **`Theme`** (no number). Tooltip `Theme: <name>, click for next`; `aria-label` `Color theme: Theme N, <name>`. |
| Click | Advances to the next theme in registry order and wraps: **Theme 1 → 2 → 3 → 4 → 5 → 6 → 7 → 1** (`nextThemeId`). Applies at once (no reload), saves the choice, and redraws the balance chart. Same cycling model as the Music button. |
| Persistence | `localStorage` key **`easy0dte.theme`** (`THEME_STORAGE_KEY`), value = theme id (`theme-1` … `theme-7`). Per browser/device; not synced between devices. |
| Fallback | Missing, unknown, or unreadable (storage blocked) value → **Theme 1 Green** (`DEFAULT_THEME_ID`, `readSavedThemeId`). An unknown id is never applied. |
| First paint | An inline script in `<head>` of `index.html`, before the stylesheet, reads `easy0dte.theme`; for `theme-2`…`theme-7` it sets `<html data-theme="theme-N">` and the `<meta name="theme-color">` color from a small id → color map, so a saved theme paints with no Theme 1 flash. Theme 1 = no `data-theme` attribute. `initThemes()` then re-applies the same theme and wires the button. |
| Apply | `applyTheme(id)` sets/removes `data-theme`, sets the meta color, and updates the button label, tooltip and `aria-label`. |
| Chart | `drawEquityChart` (`app.js`) reads `--accent-rgb` (grid), `--muted` (axis labels), `--chart-line` (line) and `--chart-rgb` (fill) at draw time via `themeColor()`; `initThemes({ onChange })` redraws it on every theme change. `--chart-line`/`--chart-rgb` equal the accent in all 7 themes. The live dot uses `--chart-line`; the BALANCE callout text uses `--lime`. |

#### Themes

| Id | Name (tooltip) | Look | Accent | Profit | Loss | Net Equity | Tile color @ opacity | Min contrast on panels |
|---|---|---|---|---|---|---|---|---|
| `theme-1` | Green (default) | Lime on near-black (default) (dark) | `#c8ff00` | `#c8ff00` | `#ff594d` | `#89CFF0` | `#336600` @ 0.28 | 4.8:1 (`--put`) |
| `theme-2` | PlayStation Blue (light) | Ultima Bot light PlayStation Blue: light grey page, white panels (light) | `#0070f3` | `#2B42FF` | `#dc2626` | `#00B1B8` (exempt from 3:1) | `#3b82f6` @ 0.12 | 4.6:1 (`--lime`) |
| `theme-3` | Royal Purple & Gold | Gold on deep aubergine (dark) | `#f5c542` | `#3ddc84` | `#ff5c6c` | `#8fd3ff` | `#6b3fa0` @ 0.3 | 5.7:1 (`--put`) |
| `theme-4` | Midnight Cyan | Electric cyan on deep navy (dark) | `#22e1ff` | `#3cf08f` | `#ff5a6e` | `#b49cff` | `#1fb6d6` @ 0.28 | 5.6:1 (`--put`) |
| `theme-5` | Sunset Amber | Sunset orange on warm charcoal (dark) | `#ff8c32` | `#4be38a` | `#ff5c7a` | `#7fd4ff` | `#c2611f` @ 0.14 | 5.8:1 (`--red`) |
| `theme-6` | Red & Silver | Bold red on graphite/gunmetal, silver text and borders (dark) | `#ff2e3f` | `#3ee08a` | `#990033` dark crimson (Eric; exempt from 3:1, ~2.0:1) | `#b8c7dc` | `#8a9099` @ 0.1 | 4.4:1 (`--lime`) |
| `theme-7` | Hot Pink | Hot pink on plum-black (dark) | `#ff2fa8` | `#4ade80` | `#ff6a3d` orange-red | `#7cc8ff` | `#c2187a` @ 0.12 | 5.2:1 (`--lime`) |

**P&L meaning (policy):** profit and loss must always be distinct colors and loss must never equal the accent. Profit is green in every theme except **Theme 2**, where profit is blue `#2B42FF` (owner's choice). **Positive Unrealized (Open) is the Theme 1 gold `--unreal-up` `#fed41e` in every theme except Theme 2, which uses the darker gold `#ff9900` for its light panels** (Eric). Loss is red in Themes 1–5 (Theme 2 uses darker red-600 `#dc2626` for white panels); **Theme 6 uses Eric's dark crimson `#990033`** (v2.46.20; was magenta `#ff5ccf`) and **Theme 7 orange-red `#ff6a3d`**. Theme 6 loss is now close in hue to its red accent `#ff2e3f` but much darker; the test still requires the two to differ. In Themes 6 and 7 the tape PUT side uses the same color as loss. **Open Positions Cur% gain uses `--profit` (not the accent) in Themes 6 and 7** (Theme 7: green `#4ade80` instead of the pink accent; Eric); Themes 1–5 keep the accent there (Theme 1's accent is its profit green). **Theme 6 profit is its own `--profit` green `#3ee08a`** (the Avg Win / Best Trade green; Eric), everywhere a gain shows: Total/Day P&L, Open Positions Cur% (Theme 6 overrides `.book td.cur-pct.up` from the accent to `--profit`), profitable Event Tape and Signal exits; the red accent is never used for profit.

**Theme 2 specifics:** palette from Ultima Bot's light PlayStation theme (`--logo-color #0070f3`, tailwind `light.background #F2F2F2`, `light.text #111111`, `light.accent #E5E5E5`, `light.primary #003791`, `bg-white` panels, `text-gray-500` muted; Welcome SVG fill `#3b82f6`). Data colors are darkened Tailwind 700/800 shades for white panels. Owner overrides: profit **`#2B42FF`** (unrealized gain uses the darker gold `#ff9900`); Net Equity **`#00B1B8`** (KPI and Account details value only; ~2.6:1 on white, exempt from the 3:1 rule by request); ENTRY signal and closed discovery chips keep **`#0369a1`** via `--equity-info`; balance chart stays the accent `#0070f3`.

#### Themed vs fixed

- **Themed (every theme sets it):** every CSS custom property in the table below. Every color in `styles.css` outside the `:root` and `html[data-theme]` blocks is a `var(--…)` (enforced by test: no hard-coded hex or `rgba(` with literal numbers in the stylesheet body). The canvas chart reads variables too.
- **Tile layer:** `.bg-scroll` is a fixed, `pointer-events: none` layer behind `.desk-shell` (z-index 0). It paints `background-color: var(--tile-color)` through `mask-image` / `-webkit-mask-image: var(--bg-icon, url("/svg/bg-currency-mask.svg"))` (60 × 60 px, repeat; the icon is chosen by the Background icon button, independent of the theme) at `opacity: var(--tile-opacity)`, and drifts diagonally with `diagonalScroll` (transform 0 → 180px, 180px every 2.8 s, linear, infinite).
- **Fixed (same in all themes):** fonts, layout and sizes, the tile size, spacing and motion (the icon shape comes from the Background icon button; only its color and opacity are themed), the ClosedBar mascot GIF, sound effects and music, emoji in the Event Tape. JS fallbacks in `themeColor()` (`#c8ff00`, `#7a8470`, `200, 255, 0`) are only used if a variable is missing.

#### Contrast rule

Every text and P&L color (`--text`, `--muted`, `--lime`, `--profit`, `--red`, `--equity`, `--entry`, `--ticker`, `--withdrawals`, `--unreal-up`, `--watch`, `--call`, `--put`, `--closed-text`) in every non-default theme must reach **≥ 3:1** WCAG contrast on both `--bg-panel` and `--bg-elev`. The exemptions (owner's choice) are **Theme 2 `--equity` `#00B1B8`** (~2.6:1 on white), **Theme 2 `--unreal-up` gold `#ff9900`** (~2.1:1 on white) and **Theme 6 `--red` / `--put` `#990033`** (~2.01:1 on `#17191d` panels, ~1.87:1 on `#1d2025` elev; v2.46.20). Theme 1 is the original palette and is not gated (it measures ≥ 4.8:1).

#### Per-theme palette (variables by theme)

Generated from `public/js/themes.mjs`; `:root` in `styles.css` holds Theme 1.

| Variable | Used for | Theme 1 Green | Theme 2 PlayStation Blue | Theme 3 Purple & Gold | Theme 4 Midnight Cyan | Theme 5 Sunset Amber | Theme 6 Red & Silver | Theme 7 Hot Pink |
|---|---|---|---|---|---|---|---|---|
| `--accent-rgb` | Accent as `r, g, b` (glows, chart grid, `--lime-dim`, `--lime-glow`, `--flash-line`) | `200, 255, 0` | `0, 112, 243` | `245, 197, 66` | `34, 225, 255` | `255, 140, 50` | `255, 46, 63` | `255, 47, 168` |
| `--lime` | Accent color: chips, pulse, headings, Enter, boss-slam bars, discovery READY, chart line | `#c8ff00` | `#0070f3` | `#f5c542` | `#22e1ff` | `#ff8c32` | `#ff2e3f` | `#ff2fa8` |
| `--line` | Hairline borders | `rgba(var(--accent-rgb), 0.12)` | `rgba(0, 0, 0, 0.12)` | `rgba(245, 197, 66, 0.16)` | `rgba(34, 225, 255, 0.16)` | `rgba(255, 140, 50, 0.16)` | `rgba(192, 198, 206, 0.18)` | `rgba(255, 47, 168, 0.16)` |
| `--on-accent` | Text on a solid accent fill | `#081000` | `#ffffff` | `#1a0f2e` | `#04111f` | `#1a0e05` | `#ffffff` | `#1a0612` |
| `--closed-rgb` | ClosedBar border and glow | `212, 255, 0` | `0, 112, 243` | `245, 197, 66` | `34, 225, 255` | `255, 140, 50` | `255, 46, 63` | `255, 47, 168` |
| `--closed-text` | ClosedBar headline | `#d4ff00` | `#003791` | `#ffd76a` | `#7deeff` | `#ffb36b` | `#ff6b76` | `#ff8acb` |
| `--closed-bg-a-rgb` | ClosedBar gradient start | `10, 13, 6` | `255, 255, 255` | `20, 12, 36` | `6, 14, 30` | `20, 15, 12` | `14, 15, 18` | `18, 10, 16` |
| `--closed-bg-b-rgb` | ClosedBar gradient end | `28, 36, 8` | `219, 234, 254` | `58, 30, 96` | `12, 42, 74` | `58, 32, 14` | `48, 20, 24` | `62, 16, 44` |
| `--bg` | Page | `#080905` | `#F2F2F2` | `#0d0817` | `#060d1a` | `#120e0b` | `#0e0f12` | `#120a10` |
| `--bg-elev` | Raised chips, menus, overlays | `#0e110a` | `#ffffff` | `#170f26` | `#0e1a2e` | `#221a14` | `#1d2025` | `#22141f` |
| `--bg-panel` | Panels | `#0c0f08` | `#ffffff` | `#140c22` | `#0b1526` | `#1c1611` | `#17191d` | `#1c1019` |
| `--bg-glow` | Top-left page glow | `#12180c` | `#E5E5E5` | `#2a1645` | `#0f2d4a` | `#3a2210` | `#2a2d33` | `#3a1530` |
| `--bg-rgb` | Page as `r, g, b` (fades, text halos) | `8, 9, 5` | `242, 242, 242` | `13, 8, 23` | `6, 13, 26` | `18, 14, 11` | `14, 15, 18` | `18, 10, 16` |
| `--panel-rgb` | Panel as `r, g, b` (marquee edge fades, overlays) | `12, 15, 8` | `255, 255, 255` | `20, 12, 34` | `11, 21, 38` | `28, 22, 17` | `23, 25, 29` | `28, 16, 25` |
| `--panel-hi-rgb` | Raised panel as `r, g, b` | `14, 17, 10` | `255, 255, 255` | `26, 16, 44` | `14, 26, 46` | `34, 26, 20` | `29, 32, 37` | `34, 20, 31` |
| `--text` | Body text; Event Tape entry/exit headers | `#e8f0d8` | `#111111` | `#efe7ff` | `#e6f4ff` | `#f6ece2` | `#e4e7eb` | `#fbe9f4` |
| `--muted` | Secondary text, chart axis labels, unrealized flat | `#7a8470` | `#6b7280` | `#9a8cb8` | `#8aa3bf` | `#b09c88` | `#9aa1ab` | `#b897ab` |
| `--tile-color` | Tiled currency background color (fills the shared mask) | `#336600` | `#3b82f6` | `#6b3fa0` | `#1fb6d6` | `#c2611f` | `#8a9099` | `#c2187a` |
| `--tile-opacity` | Tile layer opacity | `0.28` | `0.12` | `0.3` | `0.28` | `0.14` | `0.1` | `0.12` |
| `--shadow-rgb` | Drop shadows (menus, mascot) | `0, 0, 0` | `17, 24, 39` | `0, 0, 0` | `0, 0, 0` | `0, 0, 0` | `0, 0, 0` | `0, 0, 0` |
| `--profit` | Profit values (Total/Day P&L, wins, tape gains) | `#c8ff00` | `#2B42FF` | `#3ddc84` | `#3cf08f` | `#4be38a` | `#3ee08a` | `#4ade80` |
| `--profit-glow` | Profit glow | `rgba(200, 255, 0, 0.18)` | `rgba(43, 66, 255, 0.12)` | `rgba(61, 220, 132, 0.18)` | `rgba(60, 240, 143, 0.18)` | `rgba(75, 227, 138, 0.18)` | `rgba(62, 224, 138, 0.18)` | `rgba(74, 222, 128, 0.18)` |
| `--red` | Loss values | `#ff594d` | `#dc2626` | `#ff5c6c` | `#ff5a6e` | `#ff5c7a` | `#990033` | `#ff6a3d` |
| `--red-rgb` | Loss as `r, g, b` | `255, 89, 77` | `220, 38, 38` | `255, 92, 108` | `255, 90, 110` | `255, 92, 122` | `255, 92, 207` | `255, 106, 61` |
| `--red-dim` | Dimmed loss | `rgba(255, 89, 77, 0.65)` | `rgba(220, 38, 38, 0.7)` | `rgba(255, 92, 108, 0.65)` | `rgba(255, 90, 110, 0.65)` | `rgba(255, 92, 122, 0.65)` | `rgba(255, 92, 207, 0.65)` | `rgba(255, 106, 61, 0.65)` |
| `--equity` | Net Equity KPI and Account details Equity value only | `#89CFF0` | `#00B1B8` | `#8fd3ff` | `#b49cff` | `#7fd4ff` | `#b8c7dc` | `#7cc8ff` |
| `--equity-rgb` | Net Equity as `r, g, b` | `137, 207, 240` | `0, 177, 184` | `143, 211, 255` | `180, 156, 255` | `127, 212, 255` | `184, 199, 220` | `124, 200, 255` |
| `--equity-info` | ENTRY signal headline/fill/detail, closed discovery chips | `var(--equity)` | `#0369a1` | `var(--equity)` | `var(--equity)` | `var(--equity)` | `var(--equity)` | `var(--equity)` |
| `--equity-info-rgb` | ENTRY signal glow | `var(--equity-rgb)` | `3, 105, 161` | `var(--equity-rgb)` | `var(--equity-rgb)` | `var(--equity-rgb)` | `var(--equity-rgb)` | `var(--equity-rgb)` |
| `--entry` | Discovery CONFIRM, entry/confirm blue | `#0096FF` | `#1d4ed8` | `#5aa9ff` | `#6fb6ff` | `#8ab8ff` | `#8ab4ff` | `#8ab4ff` |
| `--ticker` | Ticker symbols, HOLIDAY marquee | `#FFD700` | `#b45309` | `#ffe08a` | `#9ff3ff` | `#ffd27a` | `#ffd27a` | `#ffd27a` |
| `--withdrawals` | Withdrawals value, discovery open chips | `#85bb65` | `#4d7c0f` | `#9bd17a` | `#a3e37a` | `#b5dd6e` | `#b5dd6e` | `#b5dd6e` |
| `--unreal-up` | Unrealized gain (KPI, subline, Account details) | `#fed41e` | `#ff9900` | `#fed41e` | `#fed41e` | `#fed41e` | `#fed41e` | `#fed41e` |
| `--watch` | Signal WATCH | `#f0d878` | `#854d0e` | `#f0d878` | `#f6c86b` | `#f2c879` | `#f2c879` | `#f2c879` |
| `--call` | Tape CALL side | `#00a86b` | `#047857` | `#2fd39a` | `#2fe0b0` | `#3fd9a4` | `#34d6b0` | `#34d6b0` |
| `--put` | Tape PUT side | `#FF0000` | `#dc2626` | `#ff4d5e` | `#ff5470` | `#ff5c7a` | `#990033` | `#ff6a3d` |
| `--chart-line` | Balance chart line, live dot and dot glow | `var(--lime)` | `var(--lime)` | `var(--lime)` | `var(--lime)` | `var(--lime)` | `var(--lime)` | `var(--lime)` |
| `--chart-rgb` | Balance chart area fill | `var(--accent-rgb)` | `var(--accent-rgb)` | `var(--accent-rgb)` | `var(--accent-rgb)` | `var(--accent-rgb)` | `var(--accent-rgb)` | `var(--accent-rgb)` |
| `<meta name="theme-color">` | Browser chrome | `#080905` | `#F2F2F2` | `#0d0817` | `#060d1a` | `#120e0b` | `#0e0f12` | `#120a10` |

#### Files

| File | Role |
|---|---|
| `public/js/themes.mjs` | Registry `THEMES` (id, label, name, `vars`, `meta`), `THEME_STORAGE_KEY`, `DEFAULT_THEME_ID`, `themeById`, `nextThemeId`, `readSavedThemeId`, `themeCssBlock`, `applyTheme`, `themeColor`, `initThemes` |
| `public/styles.css` | `:root` = Theme 1 values; one generated `html[data-theme="theme-N"]` block per other theme (exactly `themeCssBlock(theme)`) |
| `public/index.html` | Inline first-paint script + meta map; `#btn-theme` button |
| `public/app.js` | `initThemes({ onChange })`; chart reads theme variables |
| `public/svg/*.svg` | Tile icons (see Background icon); themes color whichever icon is selected via `--tile-color` / `--tile-opacity`, never via the file. |
| `scripts/test-themes.mjs` | Theme gate (in `npm test`) |

#### Adding a theme

1. **Registry:** append an entry to `THEMES` in `themes.mjs` with the next id (`theme-8`), `label` `Theme 8`, a `name`, a `meta` page color, and **every** key that Theme 1 has (same key set; `*-rgb` values are `r, g, b`).
2. **CSS:** paste `themeCssBlock(theme)` output into `styles.css` after the last theme block (byte-exact; do not hand-edit it).
3. **Tile:** no new file. Set `--tile-color` (any hex) and a subtle `--tile-opacity` (≤ 0.15 for new themes) in the registry entry.
4. **First paint:** add `'theme-8': '<meta color>'` to the id → color map in the `index.html` head script.
5. **Test:** extend `scripts/test-themes.mjs` (cycle `theme-7 → theme-8 → theme-1`, name, tile file, first-paint entry); the generic checks (same keys, CSS block in sync, P&L distinct, loss ≠ accent, 3:1 contrast) cover the rest. Run `npm test`.
6. **Docs:** add the theme to the tables here, bump the version (chip, `snapshot.mjs` `VERSION`, `package.json`, `?v=`), add a change-log row, sync this file to RESEARCH.

#### Test gate (`scripts/test-themes.mjs`)

Checks: unique ids; identical key sets; `:root` equals Theme 1 and the Theme 1 meta; every other theme's CSS block is present verbatim; profit green (r/g/b test, Theme 2 exempt), profit never red and ≠ loss; loss red-dominant (R the highest channel: true for the reds, crimson and orange-red); loss ≠ accent (Themes 6–7; Theme 6 `#990033` vs `#ff2e3f` are close in hue but not identical); Theme 6 loss/PUT/`--red-rgb` pinned to `#990033` / `153, 0, 51`; the 3:1 contrast rule with the Theme 2 equity and Theme 6 loss/PUT exemptions; Theme 2 owner values (`#0070f3`, `#F2F2F2`, `#2B42FF`, `#dc2626`, `#00B1B8`, `#0369a1`); chart vars equal the accent in all themes; no hard-coded colors in the stylesheet body or chart; cycle and wrap; storage fallback; first-paint script precedes the stylesheet and lists every theme; button label is `Theme`; exactly one tile file (`bg-currency-mask.svg`, monochrome) exists, every theme sets `--tile-color` (matching the former per-theme fills) and `--tile-opacity`, the layer uses the mask with the `-webkit-` prefix plus `diagonalScroll`, and no `--bg-tile` or per-color `bg-currency-*.svg` reference remains.

### Background icon (topbar button)

The tiled background shows **one icon at a time**, chosen with the topbar **Background icon** button. The icon is independent of the color theme: the theme sets the tile color and opacity, the button sets the shape. Read-only, per browser.

#### Button and selection

| Item | Spec |
|---|---|
| Button | `#btn-bg-icon` (`.chip.bgicon`) in the topbar, right after the **Training** button and before **Theme** (order: version chip → Training → Background icon → Theme → Music → SFX). Shows the current tile icon, cycles in fixed manifest order, saved in `easy0dte.bgIcon`. Same height as the other chips. It shows the current icon as a masked `.bgicon-glyph` span filled with `currentColor` (the accent `--lime`), so it follows the theme. Tooltip `Background: <icon name>, click for next`; `aria-label` `Background icon: <icon name>`. |
| Click | Moves to the **next icon in manifest order** and wraps from the last back to the first (`nextBgIcon`). The order is **fixed and deterministic**, like Themes 1 → 7 → 1: the pinned icons first (`PINNED_BG_ICONS`: the dollar default, then Ultima Bot's box), then the rest alphabetically by file name. Never random. The button glyph and the background tile change together, at once (no reload). |
| Persistence | `localStorage` key **`easy0dte.bgIcon`** (`BG_ICON_STORAGE_KEY`), value = file name (e.g. `currency_bitcoin_24dp_….svg`). Not synced between devices. |
| Fallback | Missing, unknown (not in the manifest) or unreadable value → the first icon, `bg-currency-mask.svg` (`DEFAULT_BG_ICON`). If the manifest cannot load, only the default is available. |
| First paint | The inline `<head>` script (same one as themes, before the stylesheet) reads `easy0dte.bgIcon` and, for a safe file name other than the default, sets `--bg-icon: url("/svg/<file>")` on `<html>`, so a saved icon paints without a dollar flash. `initBgIcons()` then loads the manifest, validates the saved value and wires the button. |
| Rendering | `.bg-scroll` and `.bgicon-glyph` both use `mask-image` / `-webkit-mask-image: var(--bg-icon, url("/svg/bg-currency-mask.svg"))`. The tile is 60 × 60 px, repeat, `--tile-color` at `--tile-opacity`, `diagonalScroll` unchanged. Masks use the alpha channel only, so the icons' own fill colors are ignored. Default = no `--bg-icon` set. |

#### Folder and manifest

- All icons live in **`public/svg/`** (deployed with Firebase Hosting as `/svg/…`). The original dollar tile is `public/svg/bg-currency-mask.svg` (black on transparent).
- The manifest order is `PINNED_BG_ICONS` (in `public/js/bg-icons.mjs`) then alphabetical; to pin another icon, add its file name to that list.
- A static host cannot list a folder, so **`public/svg/manifest.json`** lists the icons: `{ default, icons: [{ file, name }], skipped: [{ file, reason }] }`. It is written by **`scripts/write_svg_manifest.mjs`** (`npm run svg:manifest`), which also runs in `scripts/deploy_desk_hosting.sh` right after the sound-effects sync.
- **Ultima Bot pair:** Ultima Bot's frontend used two background tiles (Bootstrap Icons, 16 × 16), inline in `Welcome.jsx` (Ultima Bot 6 lines 65–66; Ultima Bot 2026 lines 107–108), `Dashboard.jsx` and `IndexScreen.jsx`: the **dark-mode box** (`box-seam-fill`, fill `#00ff88`) and the **light-mode dollar coin** (`coin`, fill `#3b82f6`). The dollar is our `bg-currency-mask.svg` (same paths); the box is `bg-box-seam-mask.svg`. Both are pinned at the head of the cycle so they sit together.
- Display name = file name without the Material suffix (`_24dp_…`), underscores as spaces, first letter capitalized; the default is "Currency (default)".

#### Icon rules

An icon is listed only if it passes `validateSvg` (otherwise it goes to `skipped` with the reason, and is never shown): it is an SVG with a `viewBox`; at most 50 KB; at least one shape; filled (an SVG with `fill="none"` and only strokes is rejected, because a stroke-only icon is nearly invisible as a 60 px mask); no embedded raster `<image>`/`data:image`; no `<script>`/`foreignObject`. The icon fills its own viewBox; Material icons (viewBox `0 -960 960 960`) already include their built-in padding, so all icons use the same 60 px mask size and do not touch.

#### Adding an icon

1. Drop a filled SVG (with a `viewBox`) into `public/svg/`. Any color; only its shape matters.
2. Run `npm run svg:manifest` (the deploy script also runs it). Check the output for skipped files.
3. Run `npm test` (`test-bg-icons` fails if the manifest is stale). Bump the version and add a change-log row if it ships.

#### Current icons (51, cycle order)

1. Currency (default), 2. Box (Ultima dark), 3. Award star, 4. Blur on, 5. Bomb, 6. Borg, 7. Cannabis, 8. Crossword, 9. Cruelty free, 10. Currency 1exchange, 11. Currency bitcoin, 12. Currency euro, 13. Currency pound, 14. Currency q, 15. Currency yen, 16. Deceased, 17. Dialogs, 18. Diamond, 19. Format quote, 20. Grain, 21. Health cross, 22. Heart plus, 23. Hive, 24. Hub, 25. Layers, 26. Linked services, 27. Local atm, 28. Local hospital, 29. Location searching, 30. Memory, 31. Park, 32. Payments, 33. Pill, 34. Potted plant, 35. Public, 36. Savings, 37. Shopping cart, 38. Skull, 39. Spa, 40. Special character, 41. Stack, 42. Stacks, 43. Stadia controller, 44. Star rate, 45. Stars, 46. Swords, 47. Tag, 48. Texture, 49. Thermostat carbon, 50. Trophy, 51. Universal currency alt.

#### Test gate (`scripts/test-bg-icons.mjs`)

Manifest equals a fresh build and covers every SVG in the folder; dollar then Ultima box pinned first (first click = box), the rest sorted, box tile is mask-ready, no duplicates, stable across rebuilds; each click goes to the next icon in manifest order and the last wraps to the first, with no randomness (`Math.random` absent); storage key and fallback (missing, unknown, blocked); validation rejects missing viewBox, raster, stroke-only and oversize SVGs; a fake DOM click updates the glyph variable, tooltip and storage; the button sits between version and Theme; the first-paint read precedes the stylesheet; no stale `/bg-currency-mask.svg` path at the old location.

### Strategy path naming

Source of truth for a trade's path is the **ledger** (`snapshot.json` positions / tape `path`), never the trigger text (`below_orb_low` contains "orb" on every Breakout).

| Ledger `path` | Full name (docs) | Event Tape (long) | Open Positions Path (short) |
|---|---|---|---|
| `ORB1` | ORB1 Breakout (06:45 window, ORB#1 box) | 🔮 OR1 Breakout | 🔮 ORB1 |
| `ORB2` | ORB2 Breakout (≥07:30, ORB#2 box) | ⛓️ OR2 Breakout | ⛓️ ORB2 |
| `TL` | Trendline, slots TL0 / TL1 / TL2+ (slot ≥2 = TL2+) | 📏 Trendline | 📏 TL |

Display names live in `PATH_DISPLAY` (`public/js/snapshot.mjs`). Tape entry/exit headers (`tapeHeaderFor`), the signal path tag and the positions Path column are rebuilt from the ledger `path` at render, so a published header with an old name still shows the current one. Discovery ticker chips keep the short codes (ORB1 / ORB2 / TL0…).

The `actions.json` fast pulse paints a fill before the snapshot. Its `path` is **never displayed**: a pulse row takes the ledger path when the snapshot already has that trade (always for exits), otherwise it shows a neutral **Fill** header until the snapshot row replaces it (`public/js/action-pulse.mjs`, test `scripts/test-action-pulse.mjs`).

**Open item (RESEARCH backend):** the Cloud Run desk `actions.json` writer stamped HOOD-82p-211 (10/30) as `path: ORB1` while the ledger said `ORB2`. Fix belongs in the RESEARCH writer: take `path` from the ledger strategy path, not from trigger text. The desk guard above hides the mismatch until then.

Display rule: the Event Tape uses long names on every entry and exit row (📏 Trendline / 🔮 OR1 Breakout / ⛓️ OR2 Breakout + Fill or Closed). When a row has no ledger path, the path is read from its header so the names stay consistent. The Open Positions Path column uses short names: 📏 TL, 🔮 ORB1, ⛓️ ORB2.

### Event tape row format

**Exit** (ledger `closed_at` / `exit_time`):

```text
{+/-}{pct}% · {+/-}${money} · {CALL|PUT} · {position_id}
```

Example: `+745.0% · +$3,844.20 · CALL · IBIT-44c-328`

- `$` = **true-to-live net**: Demo STC at executable bid (Live at broker fill) minus modeled $0.65/contract/side fees. Protect floors are exit *decisions*, not fills. Reconstruct bid from `premium_snapshot.executable_exit_price` / `bid` when the stamped `exit_price` is a floor.
- `%` = net dollars / (**premium + opening fees**). Fallback if no economics stamp: `entry_price × quantity × 100` (credits skip opening fees).
- **Open Positions** uses the same mark + net (GLD PUT `0.20→0.07` qty 30 = **−71.5% · −$429.00**, not the −60% / −$360 stop-floor stamp).
- **Hosted Firebase v2.46.30** (2026-10-30). **Telegram** uses the same true-to-live formula; RESEARCH Cloud Run **`00618-cvb`** is live.
- The theme's `--profit` color for profit and `--red` for loss on the signed **%** and the signed **$** (including the `+`/`−` before each). CALL/PUT stay white; the ticker inside the trade id stays gold.
- Money/% use `,` thousands separators. Tape omits the word **`id`**. Stored ledger keys may still start with legacy **`D-`/`L-`**; the desk **displays** `GOOGL-347.5p-774`. New fills omit the mode prefix. Demo vs Live remains separate ledgers / apps, not the id.

**Entry** (ledger `entry_time` / `opened_at` on closed + open rows):

Header (path · ticker) uses `--text`. Body stays white; ticker in the id stays gold.

```text
{qty} × ${entry} · ${deployed} · {CALL|PUT} · {position_id}
```

**Market open / close / ORB Capture** are **not** ledger events. They are printed from the 0DTE session clock on each `metadata.session_date` (fallback: PT date of `entry_time` / `closed_at`, plus `stats_trading_date`, plus the client’s PT date). A row is shown only after that wall-clock has passed, and only if that YMD is a **trading day** (`isSessionBellYmd` / RESEARCH `should_skip_trading`: skip weekends and all three skip kinds — `MARKET_CLOSED`, `LOW_VOLUME`, `PRE_HOLIDAY`). **ORB Capture 1/2** additionally require real capture stats for that date — the same truth as Telegram **0DTE ORB Capture Complete**. Do not invent `[Captured: 133] [Duration: 3.0 seconds]`. Flatten **12:50 PT** is a separate close reason on exits, not the tape’s market-close bell.

| Bell | Clock | Header |
|------|-------|--------|
| Market open | 09:30 ET / **06:30 PT** | `Market open` + cloud/sun/dove emoji (`SESSION_OPEN_DECO` `☁️ ☁️ ☁️ ☁️ ☁️ 🌤️ ☁️ ☁️ 🕊️ ☁️ ☁️`) and tail `🌻 🛎️ ✍️ ⏰ 🎾 📰 🗞️` |
| ORB Capture 1 | 09:30–09:45 ET / **06:30–06:45 PT** | `🔮 ORB Capture 1 · [15min] 9:30-9:45 AM ET` |
| ORB Capture 2 | 09:30–10:30 ET / **06:30–07:30 PT** | `🔮 ORB Capture 2 · [1hr] 9:30-10:30 AM ET` |
| Market close | 16:00 ET / **13:00 PT** | `Market close` + night deco (`SESSION_CLOSE_DECO` `✨ ✨ ✨ ✨ ☁️ ✨ 🌖 ✨ ☁️ ✨ ✨`) and beach tail `🌊 🌊 🌊 🏖️ 🌊 🏝️ ⛱️` |

The desk **merges these bells on the client clock** so a hosted snapshot frozen at the 12:50 flatten still prints **Market close** after 13:00 PT. Animated SVG open/close scenes were **removed**.


### Positions sort

`cur_pct` descending (best mark on top).

---

### Training overlay (topbar button)

The **Training** button opens the **Training Calendar**: the Trade Profile training replay (P&L calendar) plus the forward Pre trained / Post trained log. Training uses every Easy 0DTE session to re-optimize the Trade Profile path.side key levels for maximum net. **Pre trained** = that session replayed on the profile as it stood *before* that day's training (model effectiveness for Live deployment); **Post trained** = the same session on the profile *after* training (optimized expected result). Full process spec: RESEARCH `training/README.md`.

| Item | Spec |
|------|------|
| Button | `#btn-training` (`.chip.training`, same height/style as the other chips), label **Training**, in the topbar right after the **version** chip (order: version chip → Training → Background icon → Theme → Music → SFX). Click toggles the overlay; `aria-pressed` reflects open/closed. |
| Overlay | `#training-overlay` (`role="dialog"`), a direct child of `<body>`; `position: fixed; inset: 0`, **z-index 10000** — the highest on the page, above the ClosedBar (**The Market is Closed!**, z 900), the enter gate and the loading overlay. Closes with **Close ✕**, **Esc** or a click on the backdrop. Page scroll is locked while open (`html.training-open`). Theme-aware: panels `--bg-panel`/`--bg-elev`, text `--text`/`--muted`, title `--lime`, profit `--profit`, loss `--red`; no hard-coded colors. Backdrop is translucent so the desk shows behind it: `rgba(var(--bg-rgb), 0.35)` + `backdrop-filter: blur(2px)`. Content panel is opaque `--bg-panel`, max-width 1100px, centered. |
| Layout (top → bottom) | Head (**Training Calendar** · Close ✕) → `.training-top`: **model summary** (`.training-summary`, from the JSON so it follows new days) with the mini year calendar to its right → month title + **‹ Prev / Next ›** + calendar grid → **Month stats row** under the grid. Summary lines: `Trade Profile <profile.version> In-Sample replay`; `<first data day> → <last data day>` as **MM-DD-YYYY** (e.g. `08-19-2026 → 10-28-2026`); `<N> trades · <W>W/<L>L · net $X` (net in `--profit` / `--red`); then two lines **`Pre trained profit streak <streak>/<days> · 60/X to certify for Live deployment!`** and **`Pre trained session success defines timeline for Live deployment.`** (streak = consecutive latest log days with Pre > 0, reset to 0 by any Pre loss; days = log entries with both Pre and Post, skip days included; **60/X** is a fixed label: the goal is 60 profitable Pre trained sessions over however many days it takes). |
| Calendar | Header **P&L <Month YYYY>** (e.g. **P&L October 2026**). Grid **Mon–Fri only** (5 columns, no Week column); weekends are never cells. **Every trading-day cell with data shows the day number and two lines, `Pre trained: $ <W>W/<L>L` and `Post trained: $ <W>W/<L>L`** (W first; `—` when W/L is null), colored `--profit` / `--red`; cell tint follows Post (or Pre if Post is null). Days with a log entry (≥ `track_start` 2026-10-23) use the log's Pre/Post. Replay days without a log entry (all days before 10/23) show **Pre trained $0.00 0W/0L** and Post trained = the replay day P&L and W/L. A trading day ≥ `track_start` and ≤ today (PT) with no log entry and no replay data shows `Pre trained: —` / `Post trained: —`. Other open days without data show only the day number. NYSE-closed weekdays (`marketDayStatus` in `public/js/market-calendar.mjs`, e.g. **Labor Day Sep 7**) show dimmed with the holiday name. **Skip days and missing days:** every weekday is classified by `marketDayStatus` (`public/js/market-calendar.mjs`, the Desk mirror of RESEARCH `should_skip_trading`, kept equal by `test-calendar-crosscheck`; static, no Mac dependency). Same enable/disable as the trading loop: [MarketCalendar.md](MarketCalendar.md). `MARKET_CLOSED` days always render as a muted closed cell `🏖️ <name>` + `Market closed`; `LOW_VOLUME` / `PRE_HOLIDAY` days with no book row and no log values render muted as `🎃 <name>` + `Low volume · trading disabled` or `🛡️ Day before <holiday>` + `Pre-holiday · trading disabled`. **2026 Training cells:** Tue 11-24 is a normal day (not “Day before Day Before Thanksgiving”); Wed 11-25 / Thu 11-26 / Fri 11-27 skip; Dec 22 trades; 12-23 / 12-24 / 12-25 skip; 12-28 trades; 12-30 / 12-31 skip. Hosted v2.46.30 still labels 11-24 as pre-holiday until `deploy now`. A skip day that has a replay book row or log values shows and counts its data (ledger truth). A past weekday inside the book range with no book row, no log values and no calendar reason renders muted **No data**. Closed and No data cells never count toward the Month tiles, totals or the Pre trained profit streak; a skip day with a log entry or book row (e.g. 2026-11-03 Election Day) counts as a normal traded day in the tiles, totals and streak; the mini calendar shows them as closed squares with the label in the tooltip. |
| Month stats row | Boxed tiles (1px `--line` border, 8px radius, theme-aware checkerboard `conic-gradient` of `color-mix(in srgb, var(--text) 6%, var(--bg-panel))` over `--bg-panel`, 11px squares): **Monthly P&L**, **Month Trading days** (`Nd · N green · N red`), **Month Trades** (`N total · NW · NL`), **Month Best Day**, **Month Worst Day** (`Mon D · $`). Computed in the browser at render time (`buildMonth`, no stored monthly totals) over **every trading day** of the month: every day uses **Post trained = the current replay book day** (P&L, W/L; $0.00 0W/0L for a book-range day with no trades; the log's `post_*` only for days beyond the book end). Pre trained stays the logged value. Calendar totals therefore equal the book summary. Green/red, trades, W/L, best and worst day all use that value. Day cells and mini-calendar squares on tracked days are colored by Post. Missing values show **—**. `SynchTradeProfileResults` recomputes the same per-month and overall totals, prints them, and fails if day sums ≠ summary totals, W+L ≠ trades, trades[] ≠ day rows, calendar totals ≠ book summary, or a logged day beyond the book end has null Post W/L (v2.46.25). |
| Mini year calendar | `.training-mini`, right of the summary inside `.training-top` (flex, wraps; stacks below on narrow screens). One small month from **August 2026 through the current month (PT)**, label (`Aug`) above a **Mon–Fri** grid of 11px squares. Square color = the day's result: **Post trained** on logged days (else Pre), else the replay day P&L — `--profit` if > 0, `--red` if < 0, neutral otherwise; NYSE holidays fainter neutral. Hover `title` = `MM-DD-YYYY · $P&L` (or holiday / no trades). Clicking a month jumps the big calendar to it; the shown month is outlined. Leading blanks use `.tm-blank` (never the desk-wide `.empty`). |
| Months | **‹ Prev / Next ›** from **August 2026** through the current month (PT); opens on the latest month that has data. |
| Data (static, no Mac dependency) | **`/training/trade_profile_replay.json`** (`public/training/`; not `public/data/`, which Firebase ignores), fetched on first open: `profile` (version, label, json, sha256), `source` (book path, sha256, rows, pnl column, rule), `range`, `generated_at`, `totals` (trades, wins, losses, win_pnl, loss_pnl, net, days), `days[]` (`date, pnl, trades, wins, losses`), `trades[]`. **`/training/model_training_log.json`**: `track_start` (`2026-10-23`), `entries[]` sorted by date = `{date, pre_trained, pre_wins, pre_losses, post_trained, post_wins, post_losses, profile, profile_label, profile_sha, pre_profile_sha, engine_sha, pre_win_sum, pre_loss_sum, post_win_sum, post_loss_sum, note}` (older entries may carry `notes`; unknown = `null`; the overlay reads only date/Pre/Post/W/L). If the replay fails to load, the calendar shows the empty (—) state and the meta line says **Training data unavailable**. Both files are generated — never edit them in `public/training/`. |
| Single source | **RESEARCH `training/`** is the only source: `trade_profile_replay_book.csv` + `model_training_log.json` (+ `README.md`, the process spec). The Desk copies in `public/training/` are rebuilt from it by the sync. |
| Sync (`npm run training:sync`) | `scripts/SynchTradeProfileResults.sh` (env `RESEARCH_DIR`, `PNL_COL`, `TP_VERSION` default `10.23`, `NO_AUTOPICK=1`, `SYNC_CHECK_ONLY=1` + `SYNC_SUMMARY_OUT`). (1) **Newest-fixture auto-pick:** copies the newest RESEARCH `tests/fixtures/tp_10_23_replay_book_8.19-<MM.DD>.csv` (by MM.DD, year 2026) to `training/trade_profile_replay_book.csv` when it differs, printing which file it used (its only RESEARCH write). (2) Checks the folder, book, log (`entries[]`) and `easy0DTE/configs/trade_profile_10.23.json` exist and are valid. (3) **Book format from the header:** `admitted` flag column (Total Results book; admitted rows only, keyed date+trade_id), `keys` (RESEARCH evaluator: `evaluate_trade_profile` admits, closed or open), or `tp_admit` (Profit Capture admitted book, with `--meta-book`). P&L column `pnl_pc_v2` > `pnl` > `pnl_step17` > highest `pnl_step*` (fee-inclusive; win = P&L > 0). (4) Runs `scripts/write_training_data.py` to a temp file, prints mode/column, profile sha, range, trades · W/L · win · loss · net, log entries and last date, warns if the last entry's `profile_sha` ≠ the live profile, then copies both JSONs to `public/training/`. |
| Deploy gate | The sync stops (and so `deploy_desk_hosting.sh` aborts with **Training data sync failed — deploy aborted**) if any source file is missing/invalid, the book has no usable P&L column or format, or **the book's last date has no log entry** (it names the date and the `training:day` command). `Gate: ✅ <date> has a Pre/Post log entry` confirms. |
| Daily publish (`npm run training:day`) | `scripts/training_day.mjs`, run by the 10.23 Trade Profile Bot as the last step of each training: `npm run training:day -- <YYYY-MM-DD> --pre <pnl> <W> <L> [--pre-win-sum X --pre-loss-sum Y] [--pre-sha S] --post <pnl> <W> <L> [--post-win-sum X --post-loss-sum Y] --profile-label L --profile-sha SHA --engine-sha E [--note N] [--expect-trades N --expect-net X]`. Required: `--pre`, `--post`, `--profile-label`, `--profile-sha` (hex), `--engine-sha`. Upserts that date in RESEARCH `training/model_training_log.json` (sorted; re-running a date replaces it, keeping unspecified fields) with `profile_label`, `profile_sha`, `pre_profile_sha` (from `--pre-sha`), `engine_sha`, `pre_win_sum`, `pre_loss_sum`, `post_win_sum`, `post_loss_sum`, `note`. Validates counts and that win sum ≥ 0, loss sum ≤ 0 and win + loss = pnl; backs up the old log to the temp dir. **`--expect-trades` / `--expect-net`** first run the sync's book summary (`SYNC_CHECK_ONLY`) and refuse to write unless cumulative trades and net match, the book ends on that date and the live profile sha matches `--profile-sha`. Never touches the Trade Profile JSON or docs. |
| Daily process | 1) Save the session and review the replay. 2) Record Pre trained (pre-training profile). 3) Optimize the key levels and confirm the 8/19→session TP replay. 4) Record Post trained. 5) Save the book as `tests/fixtures/tp_10_23_replay_book_8.19-<MM.DD>.csv` and run `npm run training:day` with `--expect-*`. 6) Verify with `npm run training:sync` (book file, profile sha, range ending on the session, totals, last log date, `Gate: ✅`). 7) Deploy: `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` runs, in order, sounds sync → training sync → SVG manifest → `firebase target:apply` → `firebase deploy`. |
| Current data | Book `8.19-11.06` (7,996 rows, `pnl_pc_v2`, 11/6 added on the newest engine `32a80291` / `5e447ecd` with the 20% Save stall + UL-retrace exits on; fixture `tests/fixtures/tp_10_23_replay_book_8.19-11.06.csv`); profile **10.23** sha `a449ab0d…` (`A+P1+N2+Lean+L2+H1+W2+X_ALL+F105+R2_A+SV_lean1+A_g7+P4_rel9+S1_Q2cap+T1_E2+A99+N3+N100+P2_AMZN+C0_maxws+S0_maxnet`, accepted 11/6; previous C0_maxws `46f4305d…`, P2_AMZN `d480f7c1…`, N100 `a3c4cc86…`, N3 `0ef7a804…`, A99 `90f98504…`, T1_E2 `b0f1489c…`, S1_Q2cap `5a8a0dd9…`, P4_rel9 `f8c41a59…`, A_g7 `0f296670…`, SV_lean1 `ff0255c7…`; engine `path_v2_exit 32a80291 / Shared.env 5e447ecd`, 20% Save stall + UL-retrace on). TP 8/19–11/6: **542 trades, 436W/106L**, +$166,360.74 / −$6,960.35, net **+$159,400.39**; red days 10/09 only; 11/6 9W/3L +$111.70 (original Pre P2_AMZN 6W/6L −$461.10; intermediate C0 8W/3L +$87.40). Total Results 7,996 / 1,868W/6,128L / **−$365,395.21**; missed winners 1,432 +$127,566.56. Log entries: 10/23, 10/26–10/30, 11/2, 11/3, 11/4, 11/5, 11/6. Post on every day is the current book day. |
| Tests | `scripts/test-training.mjs` (in `npm test`): topbar order, overlay hidden by default and highest z-index, Mon–Fri layout (Aug 2026 starts Mon 3rd; Sep 1 is Tue; Labor Day closed), month range Aug 2026 → current, default month, aggregation, empty state —, overlay and month order, summary text and range, pinned totals, and JSON reconciliation (days = trades = totals). |

## Snapshot shape (`GET /api/state` / SSE)

Important keys (ledger mode):

```json
{
  "mode": "DEMO",
  "broker": "etrade",
  "badge": "RESEARCH",
  "data_quality": "ledger",
  "disclaimer": "Values from DEMO synthetic ledger only…",
  "ledger": { "path": "…", "mtime": "…", "source": "research_app/mock_options_history.json", "closed_count": 0, "active_count": 0 },
  "account": {
    "seed": 0, "equity": 0, "total_pnl": 0, "total_pnl_pct": 0,
    "daily_avg_pct": 0, "peak_equity": 0, "withdrawals": 0, "day_pnl": 0, "unrealized_pnl": 0,
    "day_pnl_pct": 0, "day_open_equity": 0,
    "deployed": 0, "deploy_cap": 5145.93, "deploy_cap_mode": "off", "deploy_cap_basis": "tradable_equity",
    "deploy_tradable": 5145.93, "deploy_slot_cap": 735.13, "deploy_slot_count": 7, "deploy_capital_pct": 90,
    "deploy_sum_cap": 0, "deploy_cap_formula": "Net equity × 90% (0DTE_TRADING_CAPITAL_PCT) · per slot ÷ 7",
    "deploy_cap_source": "Cloud Run YOUR_SERVICE_NAME env (…) + RESEARCH configs/*.env", "deploy_cap_as_of": "…",
    "open_positions": 0,
    "wins": 0, "losses": 0, "hit_rate": 0,
    "lifetime_wins": 0, "lifetime_losses": 0, "lifetime_hit_rate": 0,
    "avg_win": 0, "avg_loss": 0, "best_trade": 0, "worst_trade": 0, "profit_factor": "0.00",
    "history": [{ "t": "…", "equity": 0 }],
    "source": "E*Trade, DEMO Mode",
    "venue": "Demo ledger"
  },
  "tape": [{ "body": "+745.0% · +$3,844.20 · CALL · IBIT-44c-328", "pnl": 0, "pnl_pct": 0, "source": "ledger.closed_positions" }],
  "positions": [{ "cur_pct": 0, "mfe_pct": 0, "source": "ledger.active_positions" }],
  "risk": { "paths_armed": null, "discovery": null, "liq_guard": null },
  "metrics": { "open_positions": 0, "day_pnl": 0, "rejects": null, "deployed": 0, "deploy_cap": 5145.93, "deploy_cap_mode": "off", "deploy_slot_cap": 735.13, "util_pct": 0, "util_base": "net_equity" },
  "sizing": { "kind": "research_sizing", "service": "YOUR_SERVICE_NAME", "revision": "…", "fetched_at": "…", "sum_cap_raw": "0", "sum_cap_usd": 0, "sum_cap_on": false, "trading_capital_pct": 90, "buffer_pct": 10, "slot_count": 7, "slot_count_key": "0DTE_SLOT_COUNT", "keys": { "0DTE_MAX_POSITION_SIZE_SUM": { "value": "0", "from": "cloud_run_env" } } }
}
```

### Deployed / cap (RESEARCH sizing)

The cap next to **Deployed** mirrors RESEARCH `compute_unified_0dte_capital_snapshot` (`modules/unified_0dte_account.py` + `easyTrendline/trendline_capital_allocation.py`). It is never a hardcoded `$600`.

| Piece | RESEARCH key / rule | Today (Demo) |
|-------|---------------------|--------------|
| Position-sum cap | `0DTE_MAX_POSITION_SIZE_SUM` — Cloud Run env (set by `deploy_demo_service.sh`, wins over files) else `configs/Account0DTE.env`. `0` / `off` / `na` / `n/a` / `none` / `false` / blank = **off** | `0` → **off** |
| Deployable % | `0DTE_TRADING_CAPITAL_PCT` (sacred buffer = 100 − pct) | **90%** (buffer 10%) |
| Slots | `0DTE_SLOT_COUNT`; `TOTAL_0DTE_MAX_POSITIONS` wins on mismatch | **7** |
| Equity basis | cash + open entry costs = booked **Net equity** | ledger `booked_equity` |

- **Sum cap off:** `deploy_cap` = `deploy_slot_cap` = the per-position cap = Net equity × pct% ÷ slots. `deploy_tradable` = Net equity × pct% (usable across all slots). Example $5,717.70 × 90% = **$5,145.93** usable ÷ 7 = **$735.13** per position → UI `$0 / $735.13`.
- **Sum cap on:** `deploy_cap` = `deploy_slot_cap` = min(Net equity × pct% ÷ slots, sum cap) (RESEARCH applies the sum cap per open). `deploy_sum_cap` = the configured sum cap `$`.
- **Unknown** (no Cloud Run read yet, or LIVE mode): `deploy_cap: null`, `deploy_cap_mode: "unknown"` → UI **—**.
- Caveat: RESEARCH locks a session buffer floor at 7:30 PT (not published). The desk uses Net equity × pct%, which is exact unless equity fell below its 7:30 level that day.
- **Source:** the local sidecar reads `configs/*.env` (config_loader order) + Cloud Run env (`gcloud run services describe`, read-only) every `DESK_SIZING_REFRESH_MS` (2 min), caches `data/research_sizing.json`, embeds `sizing` in `/api/state` + `snapshot.json`, and publishes sidecar-only **`desk_public/sizing.json`**. Hosted polls `sizingUrl` every 30s and keeps the last known sizing (localStorage), so Cloud Run `desk_snapshot_pulse.py` snapshots (hardcoded `deploy_cap: 600`, no `sizing`) never flip it back to $600.

If the ledger file is missing: `data_quality: "unavailable"`, `account: null`, empty tape/positions — **no filler numbers**.

---

## RESEARCH vs FINAL

| Concern | RESEARCH | FINAL |
|---------|----------|-------|
| Badge | `RESEARCH` | `FINAL` |
| DEMO GCS | `research_app/mock_options_history.json` | `final_app/mock_options_history.json` |
| LIVE GCS | `research_app/live_options_history.json` | `final_app/live_options_history.json` |
| Cloud Run | `YOUR_SERVICE_NAME` (Demo active) | `YOUR_SERVICE_NAME-live` |
| Alert markers | `research_app/alert_markers/` | `final_app/alert_markers/` |
| This contract | identical `docs/Frontend.md` | identical `docs/Frontend.md` |

Desk default live pull URI is **FINAL** (`final_app/live_options_history.json`). RESEARCH Live needs `DESK_GCS_LIVE_URI` override.

---

## Formatting rules

| Kind | Rule | Example |
|------|------|---------|
| Money | `,` every three digits left of decimal | `$3,844.20`, `$939,803.99` |
| Percent | `,` on integer part | `+745.0%`, `1,745.0%` |
| IDs / symbols / titles | **No** inserted commas | `IBIT-44c-328`, `MARA` |

---

## Non-goals

- Replacing Telegram  
- Live order entry from the desk  
- Invented DEMO fills for UX demos  
- Hanging desk SSE on Cloud Run trading loop  
- Treating Grafana/Observability as the operator desk before 0DTE wiring  

---

## Related docs

- `docs/Alerts.md` — Telegram + EOD contract  
- `docs/ARCHITECTURE.md`, `docs/Cloud.md`, `docs/ProcessFlow.md`, `docs/PositionMonitoring.md`  
- `LiveModeReadiness.md` — Live cutover (FINAL authoritative)  
- `modules/eod_report_helpers.py`, `modules/live_options_ledger.py`, `modules/demo_sim_ledger_seed.py`  
- `Observability/README.md`  
- Easy FlySwarm `public/` + `server.mjs` — visual/SSE reference only  
- **`0. Strategies and Automations/0. Easy 0DTE Desk UI`** — open this folder to change the frontend (not RESEARCH/FINAL source)  
- **`0. Easy 0DTE Desk UI/animations/animation-lab/`** — local Animation Lab (Firebase ignored)  
- `docs/Firebase.md` — Firebase project `YOUR_FIREBASE_PROJECT` hosts **two** sites (OAuth + this desk); do not mix deploys  
- `docs/OAuth.md` — token renewal web app ([https://YOUR-OAUTH-APP.web.app](https://YOUR-OAUTH-APP.web.app)); not this desk  

---

## Hosting (static — no desk Cloud Run)

Firebase project **`YOUR_FIREBASE_PROJECT`** hosts **two** web apps. This desk is Hosting site **`easy-0dte`**. OAuth token renewal is the **other** site (`YOUR_FIREBASE_PROJECT` → [https://YOUR-OAUTH-APP.web.app](https://YOUR-OAUTH-APP.web.app)) — see [OAuth.md](OAuth.md) and [Firebase.md](Firebase.md). Never `firebase deploy` the desk onto the OAuth default site.

**Do not** bake the desk into `YOUR_SERVICE_NAME` / `YOUR_SERVICE_NAME-live`. **Do not** run a second Cloud Run just for this UI. Telegram stays on the trading service. The desk only **reads** ledgers.

**Production ($0 extra):** Firebase Hosting site **`easy-0dte`** in project **`YOUR_FIREBASE_PROJECT`** → **https://easy-0dte.web.app**. Second Hosting site so a desk deploy **cannot** overwrite OAuth ([https://YOUR-OAUTH-APP.web.app](https://YOUR-OAUTH-APP.web.app)). Custom domain is not published.

**Animation Lab** stays off hosting: `firebase.json` ignores `animations/**`. Open `0. Easy 0DTE Desk UI/animations/animation-lab/` locally.

**Local preview ($0):** local machine — `node scripts/preview-hosted.mjs` → **http://127.0.0.1:PORT/** serves local `public/` and proxies Cloud Run's four `desk_public` files same-origin under `/gcs/` (bucket CORS allows only https://easy-0dte.web.app). Preview only; it writes nothing to GCS. The hosted desk has no Mac dependency.

### Why not a paid frontend service

| Choice | Monthly cost | Use |
|--------|----------------|-----|
| **This plan:** Firebase Hosting static; Cloud Run publishes `desk_public` to GCS | **$0** (Spark/Blaze free tier for this traffic) | Bookmarkable UI; hosted page polls public DEMO copy |
| Your **.com 1GB** host | Already paid | Fine for the same small `public/` files (~hundreds of KB). **Do not** copy the ~30MB ledger onto that quota |
| Desk Cloud Run (even scale-to-zero) | Small, but a **second** billed service | Not worth it for a ledger mirror |
| HTTPS load balancer + IAP | **~$18/mo** | Too much for this UI |
| Serve from trading Cloud Run | — | Couples desk traffic to the trading loop — forbidden |

OAuth **keeps** its Cloud Run backend because token renewal must write Secret Manager. The desk does not need that.

### Access

| When | URL |
|------|-----|
| **Production desk** | **https://easy-0dte.web.app** |
| Local preview (local machine, preview only) | **http://127.0.0.1:PORT/** |
| After Firebase custom domain | **not published** (pending) |

Link previews (iMessage / Slack / X): Open Graph + Twitter `summary_large_image` on `index.html` point at **https://easy-0dte.web.app/easy0dte.png** (`public/easy0dte.png`, 1672×940). Square app icon is **`public/easy.png`** (black bar-chart mark; favicon / Apple touch / `site.webmanifest` 192 + 512). Previous square mark is archived as `easytwo.png` (desk root). Crawlers only see these files after a Firebase Hosting deploy.

### Deploy sequence (Firebase only — does not restart trading)

Say **`deploy now`** for **Firebase hosting**, then from `0. Easy 0DTE Desk UI`:

```bash
DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh
```

The script runs, in order: `scripts/sync_soundeffects.sh` (copies `soundeffects/` → `public/sounds/` and rewrites `public/sounds/manifest.json`), `scripts/SynchTradeProfileResults.sh` (rebuilds `public/training/*.json` from RESEARCH `training/`; deploy aborts if it fails), `node scripts/write_svg_manifest.mjs` (rewrites `public/svg/manifest.json`), `firebase target:apply hosting desk easy-0dte`, then `firebase deploy --only hosting:desk --project YOUR_FIREBASE_PROJECT --non-interactive`. Deploys to Hosting site **`easy-0dte`** on Firebase project **`YOUR_FIREBASE_PROJECT`**. **Requires firebase-tools ≥ 15.34.1**: 14.6.0 under Node 25 failed uploads with `TypeError: Converting circular structure to JSON`. Large new audio files can time out on a slow upload link; re-run the same command (add `--debug` to the `firebase deploy` line to map a failing file hash to its path). Do not attach a custom domain on the OAuth site.

Hosted `public/desk-config.js` (Demo today): `mode: 'demo'`, `pollMs: 1500`, `headUrl` → `research_app/desk_public/head.json`, `actionsUrl` → `actions.json`, `discoveryUrl` → `discovery.json`, `snapshotUrl` → `snapshot.json`. No `ledgerUrl` / `sizingUrl`. CORS origins include `https://easy-0dte.web.app` (see `scripts/gcs-cors-desk.json`).

### How this fits existing Easy fronts

| Surface | How we host it today | Role vs 0DTE desk |
|---------|----------------------|-------------------|
| Trading apps | GCP **Cloud Run** | Ledger **writers**; desk stays **off** this event loop |
| OAuth token renewal | **Firebase Hosting** + OAuth Cloud Run | PIN/token UX — **do not** deploy desk onto this default site |
| Ultima Bot | Firebase / operator custom domain (not published) | Bookmarkable operator URL pattern |
| 0DTE desk | **Firebase Hosting** site `easy-0dte` | Static UI; reads Cloud Run's `desk_public` objects from GCS |

### Target architecture

```text
Operator browser
    → https://easy-0dte.web.app        (Firebase Hosting, static public/)
    → DESK_CONFIG.headUrl             (GCS desk_public head.json, ~200B)
    → DESK_CONFIG.snapshotUrl         (GCS desk_public snapshot.json, CORS + public-read)
    → DESK_CONFIG.actionsUrl          (GCS desk_public actions.json)
    → DESK_CONFIG.discoveryUrl        (GCS desk_public discovery.json)
RESEARCH Cloud Run (YOUR_SERVICE_NAME)
    → writes gs://YOUR_GCS_BUCKET/research_app/desk_public/{head,snapshot,actions,discovery}.json
Local machine (optional, preview only)
    → http://127.0.0.1:PORT/          (preview-hosted.mjs: local public/ + read-only /gcs/ proxy)
```

### Explicitly deferred

| Option | Why not |
|--------|---------|
| Desk Cloud Run `easy-0dte-desk` | Extra billed service for a mirror UI |
| HTTPS load balancer | ~$18/mo |
| Firebase rewrite `/api` to trading Cloud Run | Hangs desk traffic on the trading app |
| Store full ledger on 1GB .com hosting | File is ~30MB; hosting is for HTML/CSS/JS only |

### Live freshness

- **Hosted desk** polls **`head.json`**, **`actions.json`**, **`discovery.json`**, and **`snapshot.json`** every **1.5s** (HEAD/ETag). RESEARCH Cloud Run **`00618-cvb`** writes all four via `desk_snapshot_pulse.py`. Discovery + Event Tape on https://easy-0dte.web.app **are live**. The DISCOVERY bar prints scan **SYMBOL·$price** in RTH; after hours **CLOSED**; skip days **HOLIDAY** (closed / low-volume / pre-holiday).
- **Local preview** (`127.0.0.1:PORT`) serves `public/` and proxies those GCS objects. It does **not** publish production snapshot/head.
- **Hosted refresh never goes backwards (v2.45.3).** The hosted client runs one poll at a time and passes each snapshot through `guardDeskSnapshot` (`public/js/snapshot.mjs`). A failed or blank fetch keeps the last desk. A new trading day or a DEMO reseed is always accepted. Trade SFX dedupe by row id.
- **Hosted** page: **Press Enter** → `Loading . . .` (loading SFX loops) → boss slam cycler → fade → desk + **open** SFX. HEAD `headUrl` then `snapshotUrl` / `actionsUrl` / `discoveryUrl` every **1.5s**, GET only when ETag changes; does not download the 33MB private ledger
- **Hosted Firebase is v2.46.30** (2026-10-30). Cloud Run **`00618-cvb`**.

```bash
cd "…/0. Easy 0DTE Desk UI"
node scripts/preview-hosted.mjs   # http://127.0.0.1:PORT/
```


## Change log

| Date | Change |
|------|--------|
| 2026-09-14 | Initial Frontend contract (connections, snapshot, FlySwarm→0DTE panels, phases) |
| 2026-09-14 | Account P&L front page; EOD/ledger field map |
| 2026-09-14 | **Truth mode:** ledger-only DEMO/LIVE; removed invented mock book; full-session tape; position sort; tape `% · $` format; thousand separators; docs rewritten to match live desk |
| 2026-09-14 | **Hosting plan:** separate Cloud Run `easy-0dte-desk` + custom domain + IAP; not in trading service; Firebase OAuth / Ultima noted as existing patterns |
| 2026-09-14 | Local desk auto-syncs GCS ledger (~30s) + SSE push; Refresh/Reset force pull — no browser reload required for new trades |
| 2026-09-15 | Desk Cloud Run packaging (`Dockerfile`, `scripts/deploy_desk_service.sh`); local access http://127.0.0.1:PORT/; public URL after desk `deploy now` + DNS + IAP |
| 2026-09-15 | RESEARCH/FINAL `Frontend.md` point at sibling `0. Easy 0DTE Desk UI`; desk is not nested like `ETradeOAuth/` |
| 2026-09-15 | Affordable hosting: same GCP project, scale-to-zero, direct IAP (no LB); custom domain not published |
| 2026-09-15 | **No desk Cloud Run.** Production UI = Firebase Hosting (like OAuth webapp). Live tape = local `npm start`. Custom domain on Firebase. |
| 2026-09-15 | Firebase hosting **deferred** — finish desk UI later; RESEARCH/FINAL `Frontend.md` stay identical pointers |
| 2026-09-16 | Firebase Hosting site created: `easy-0dte` (`easy-0dte.web.app`) in `YOUR_FIREBASE_PROJECT` |
| 2026-09-16 | **Shipped Demo desk** to https://easy-0dte.web.app: SVG bg, $ / date chart axes, Open Positions / N, brand €£$¥ 0DTE, MFE from `metadata.mfe_pct`, Loading overlay, `desk_public` ledger feed. Custom domain still pending. Trading app remains Demo; Live desk is a later `DESK_MODE` + hosting redeploy. |
| 2026-09-16 | Desk SFX: loading / open / profit% / loss% / eod (profit·loss·perfect). Clips in desk `soundeffects/` → `public/sounds/` (~19 MiB, Firebase same-origin). |
| 2026-09-16 | Enter gate: first paint is BG + **Enter**; click unlocks autoplay, starts ledger fetch, plays loading then open. |
| 2026-09-16 | SFX: one-shot loading/open; iOS/Safari Web Audio keep-alive; Enter is `click`/`touchend`. Desk `soundeffects/` is the clip library. |
| 2026-09-16 | Frontend.md (desk + RESEARCH + FINAL) documents the live web.app, Enter gate, and full SFX map. |
| 2026-09-16 | Moved clip library from RESEARCH `soundeffects/` → desk `soundeffects/` (trading apps have no audio). |
| 2026-09-17 | Hosted desk polls **snapshot.json** (~253KB) every **2s** with ETag/HEAD; sidecar publishes slim ledger (not the 33MB book) + snapshot. |
| 2026-09-17 | **Trade-action cadence:** Cloud Run writes tiny `desk_public/actions.json` at fill/close (Telegram analog). Hosted polls it every **2s** (HEAD/ETag) and paints before the fat ledger lands. SFX queues **one clip per tape event**. Firebase webhooks need Cloud Functions — not used. |
| 2026-09-17 | Discovery marquee under topbar: `watch_fsm` hot names (CONFIRM/READY/FILLED + qg_admit). Sidecar pulls `research_app/daily_markers/{PT date}.json` off-loop. |
| 2026-09-17 | SFX **entry/**: random clip on each new ledger fill after the desk is open. Clip library rearranged; `sfx:sync` counts loading 32 / open 37 / entry **5** / profit **77** / loss 32 / eod 7. |
| 2026-09-17 | DISCOVERY bar is the live scan window, not Open Positions. Cloud Run writes `desk_public/discovery.json` after each unified 24/10s rotate (WATCH / CONFIRM / READY). Desk drops ledger/FILLED fallback. |
| 2026-09-17 | Enter tap plays **loading** in the same gesture (`pointerdown` + `click` retry). Silent HTML keep-alive `play()` no longer steals iOS/Safari unlock. Open still plays from the Web Audio graph when ledger populates. |
| 2026-09-17 | Exit tape / Signal print **true-to-live net** (Demo STC = live bid, Live = broker fill, minus $0.65/contract/side fees). Protect floors are exit decisions, not fills. DELL `2.48→2.22` qty 2 = **−$54.60**. Telegram uses the same formula in source; live alerts wait on the EOD RESEARCH Cloud Run deploy. |
| 2026-09-17 | Firebase Hosting **v2.43.3** on https://easy-0dte.web.app (OAuth site untouched). Sidecar republished `desk_public/snapshot.json`. |
| 2026-09-17 | Open Positions `cur%` / mark / `$` use the same true-to-live net as the tape (Demo live bid + fees). GLD `D-GLD-398p-542` stop-floor `$0.08` / −$360 is not the fill; bid `$0.07` + $39 fees = **−$429 / −71.5%**. Desk **v2.43.5** hosted on https://easy-0dte.web.app. RESEARCH Cloud Run **`00546-m6c`** shipped 2026-09-17 EOD. |
| 2026-09-17 | Signal ENTRY + Event Tape entry-fill headers use Equity blue **`#89CFF0`** (same as Net equity / Equity). Discovery CONFIRM stays `#0096FF`. |
| 2026-09-17 | RESEARCH Cloud Run **`YOUR_SERVICE_NAME-00546-m6c`** (Demo, 100%) EOD deploy for 9.18. True-to-live Telegram / desk `actions.json` / `discovery.json` now live on Cloud Run. |
| 2026-09-17 | Firebase Hosting **v2.43.5** on https://easy-0dte.web.app (OAuth site untouched): Equity blue, tape P&L colors, Signal/entry blue, Market open/close SVG scenes. |
| 2026-09-17 | Event Tape **Market close** is a session-clock bell, not a ledger event. Hosted `snapshot.json` froze at 12:50 flatten without `session-close-2026-09-17`. Desk **v2.43.6** merges open/close on the client clock. |
| 2026-09-18 | Firebase Hosting **v2.44.25**: Discovery marquee unstuck + **SYMBOL·$price** chips; CLOSED/HOLIDAY calendars; lane ORB1/TL/ORB2 boxes **removed**; Event Tape emoji Market open/close + **ORB Capture 1/2**; hosted `head.json` / 1.5s poll. |
| 2026-09-18 | Local sidecar **v2.44.27** booked-equity KPI/chart (`equity_curve` / `booked_equity`), not Demo MTM cash. Fat Event Tape stays **2 days + 400**. Compact curve persist is local RESEARCH — Cloud Run still **`00546-m6c`**. Hosted later the same evening. |
| 2026-09-18 | Firebase Hosting **v2.44.27** on https://easy-0dte.web.app (OAuth site untouched): booked-equity KPI/chart client. Cloud Run persist still **`00546-m6c`**. |
| 2026-09-19 | Local sidecar **v2.44.28**: Event Tape Market open/close skip weekends and holiday/low-volume days (`isSessionBellYmd` / `should_skip_trading`). ORB Capture 1/2 only with real capture stats — no 133 / 3.0s invent. Hosted Firebase still **v2.44.27** until `deploy now`. |
| 2026-09-19 | Local sidecar **v2.44.29**: Event Tape drops the word **`id`**. New fills use compact **`GOOGL-347.5p-774`** (no `D-`/`L-`). Demo vs Live stays separate ledgers. Hosted Firebase still **v2.44.27** until `deploy now`. |
| 2026-09-19 | Local sidecar **v2.44.30**: hydrate also strips leftover **`id`** / **`D-`** from older snapshot tape bodies (GCS snapshot still carries them). Hosted Firebase still **v2.44.27** until `deploy now`. |
| 2026-09-19 | Firebase Hosting **v2.44.30** on https://easy-0dte.web.app (OAuth site untouched). Event Tape compact ids, no `id` / `D-`/`L-`. Cloud Run still **`00546-m6c`**. |
| 2026-09-19 | RESEARCH Cloud Run **`YOUR_SERVICE_NAME-00547-f4t`** (Demo, 100%) after-hours for next session. Compact trade ids, session-bell gates, true-best PC, booked-equity persist. Replaced **`00546-m6c`**. FINAL Live not deployed. `$1M` demo ledger preserved. |
| 2026-09-20 | Local sidecar **v2.44.31**: Event Tape Market open/close emoji — no autumn leaves; open tail `📚` → `⏰`. Hosted Firebase still **v2.44.30** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.32**: Discovery marquee — drop `-webkit-mask-image` (mobile Chrome black-out) and stop rebuilding the CLOSED track every clock tick. Hosted Firebase still **v2.44.30** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.33**: Signal EXIT/ENTRY — `EXIT Last ledger close` on one kicker line; money at EXIT size then ticker (`$-303.00 · MRVL`). Same for ENTRY. Hosted Firebase still **v2.44.30** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.34**: Signal ENTRY line is contracts × premium at ticker size (`10 × $0.59 · MRVL`), not deployed `$590.00`. EXIT unchanged. Hosted Firebase still **v2.44.30** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.35**: Event Tape Market close deco `✨ ✨ ✨ ✨ ☁️ ✨ 🌖 ✨ ☁️ ✨ ✨`. Beach tail unchanged. Hosted Firebase still **v2.44.30** until `deploy now`. |
| 2026-09-20 | Local `npm run sfx:sync`: `soundeffects/` → `public/sounds` + manifest (313 clips). Moved profit/loss files now in the live shuffle bags. Hosted Firebase still **v2.44.30** until `deploy now`. |
| 2026-09-20 | Firebase Hosting **v2.44.35** on https://easy-0dte.web.app (OAuth site untouched). Signal EXIT/ENTRY layout, Market close deco, Discovery marquee fix, 313-clip SFX library. Cloud Run still **`00547-f4t`**. |
| 2026-09-20 | Local sidecar **v2.44.36**: Discovery marquee — do not rewrite CLOSED chips every clock tick (mobile Chrome black bar); fade overlays use same-RGB alpha, no `isolation`. Hosted Firebase still **v2.44.35** until `deploy now`. |
| 2026-09-20 | Firebase Hosting **v2.44.36** on https://easy-0dte.web.app (OAuth site untouched). Mobile Discovery fade fix; SFX manifest 313 clips (`ok1_22.wav` in entry). Cloud Run still **`00547-f4t`**. |
| 2026-09-20 | Local sidecar **v2.44.37**: Event Tape Market close beach tail `🌊 🌊 🌊 🏖️ 🌊 🏝️ ⛱️`. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.38**: Enter → Loading → boss-battle slam (`€£$¥`, not Ready) → desk with ledger painted. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.39**: Enter gate label **Press Enter** with start-screen stepped blink + pixel field; same lime card size. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.40**: Press Enter blink only — scrolling dot tile removed. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.41**: Open SFX waits until after the boss slam, when the desk with data appears. Loading SFX still plays during Loading . . . Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.42**: Loading SFX plays through the boss slam and stops only when open SFX starts as the desk appears. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.43**: Boss slam €£$¥ uses the symbol-cycler reel (starts and lands on €£$¥, holds until desk+open). Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.44**: Boss slam cycler starts with the bars; landed €£$¥ fades, then desk+open. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.45**: Landed €£$¥ fade after the cycler is longer (0.7s). Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.46**: Boss slam €£$¥ is half size. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.47**: Account details Source is ledger origin only (`DEMO synthetic ledger, GCS` / `LIVE broker ledger, GCS`). Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.48**: Header subtitle is slogan **Easy same-day options** — drop `ledger mirror · live sync`. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.49**: Header slogan **You will pay**. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.50**: Boss slam €£$¥ cycler uses CRT EASY phosphor glow. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.51**: Boss slam CRT glow is tighter so cycler clip boxes do not show. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.52**: Boss slam cycler cells leave room so yen phosphor is not clipped. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.53**: Header slogan **Opposition Liquidated**. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.54**: Header slogan **Oppositions Liquidated**. Hosted Firebase still **v2.44.36** until `deploy now`. |
| 2026-09-20 | Firebase Hosting **v2.44.54** on https://easy-0dte.web.app (OAuth site untouched). Boot slam/cycler, SFX hold-until-open, slogan **Oppositions Liquidated**. Cloud Run still **`00547-f4t`**. |
| 2026-09-20 | Docs: Desk + RESEARCH + FINAL `Frontend.md` identical at **v2.44.54**; README matches hosted boot / slogan / SFX / Cloud Run **`00547-f4t`**. |
| 2026-09-20 | Docs accuracy: **On-screen surfaces** inventory; Account details / Open Positions / Risk / boot CRT / Discovery fade / snapshot tape example match the live desk. |
| 2026-09-20 | Local sidecar **v2.44.56**: Open Graph / Twitter card for https://easy-0dte.web.app uses `public/easy.png` (16:9). Hosted Firebase still **v2.44.54** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.57**: 16:9 link card is `public/tooeasy.png`; square web-app icon is `public/easy.png` (favicon, Apple touch, manifest 192/512). Hosted Firebase still **v2.44.54** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.58**: Square icon is the black bar-chart `public/easy.png` (old mark archived as `easytwo.png`). Link card is `public/easy0dte.png`. Hosted Firebase still **v2.44.54** until `deploy now`. |
| 2026-09-20 | Firebase Hosting **v2.44.58** on https://easy-0dte.web.app (OAuth site untouched). Black bar-chart icon `easy.png`, link card `easy0dte.png`. Cloud Run still **`00547-f4t`**. |
| 2026-09-20 | Local sidecar **v2.44.59**: Net equity subtitle is **Account: Demo ledger** (Demo Mode). Live uses deployed broker (`E*Trade` / `Interactive Brokers` / `Charles Schwab` / `TastyTrade`). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.60**: Total P&L subtitle is **+0.00% · Daily: +0.00%** (all-time `%` vs seed + mean booked daily `%`). Peak equity stays on the snapshot, not on this KPI. Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.61**: Account details header hint is **DEMO Mode** / **LIVE Mode** (matches the topbar `DEMO`/`LIVE` chip). Dropped “paper · read-only”. Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.62**: Topbar RESEARCH chip is now the broker **data source** (wired to `DESK_BROKER` / `desk-config.js` `broker`, currently **E*Trade**). Dropdown lists E*Trade / Interactive Brokers / Charles Schwab / TastyTrade; only E*Trade is selectable. Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.63**: Account details Source is **E*Trade Data, DEMO Mode** (same broker as the header chip + `DEMO`/`LIVE` as RESEARCH/FINAL `MODE`). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.64**: Account details Source is **E*Trade, DEMO Mode** (broker + mode only). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.65**: Account details adds **Avg Win** / **Avg Loss** / **Best Trade** / **Worst Trade** (Telegram EOD P&L TODAY: `stats_trading_date` closes, else `daily_stats`). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.66**: Account details **Profit Factor** is last (`X.XX` / `∞`, Telegram EOD today `wins_sum / losses_sum`). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.67**: Profit Factor sits on the same last Account details row as Source (right column). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.68**: Event tape hint is **Trade alerts** (dropped “Telegram-shaped · RESEARCH”). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.69**: Account details Avg Win / Avg Loss / Best / Worst / Profit Factor recompute live from today’s booked closes (hosted: Event tape exits). Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.70**: Panel hints **process metrics** / **trade alerts** / **DEMO Mode**; footer **`DEMO Mode · Frontend does not mutate Backend.`**. Hosted Firebase still **v2.44.58** until `deploy now`. |
| 2026-09-20 | Firebase Hosting **v2.44.70** on https://easy-0dte.web.app (OAuth site untouched). Account details EOD stats, Source/Profit Factor row, mode copy, Event tape **trade alerts**. Cloud Run still **`00547-f4t`**. |
| 2026-09-20 | Local sidecar **v2.44.71**: Waiting SFX loops from Market open (06:30 PT) until ORB Capture 1 posts, or 06:45 PT. Hosted Firebase still **v2.44.70** until `deploy now`. |
| 2026-09-20 | Local sidecar **v2.44.72**: Discovery marquee **WAITING** from Market open (06:30 PT) until ORB Capture 1 / 06:45 PT. Hosted Firebase still **v2.44.70** until `deploy now`. |
| 2026-09-20 | Firebase Hosting **v2.44.72** on https://easy-0dte.web.app (OAuth site untouched). Waiting SFX + WAITING Discovery marquee 06:30–06:45 PT. Cloud Run still **`00547-f4t`**. |
| 2026-09-20 | Local sidecar **v2.44.73**: Open SFX plays through when the desk appears; waiting loop no longer stops the shared buffer. Hosted Firebase still **v2.44.72** until `deploy now`. |
| 2026-09-20 | Firebase Hosting **v2.44.73** on https://easy-0dte.web.app (OAuth site untouched). Open SFX no longer cut by waiting loop. Cloud Run still **`00547-f4t`**. |
| 2026-09-30 | Local sidecar **v2.44.105**: KPI 4 is **Day P&L** (`$` after fees + signed Today % = day P&L ÷ booked equity at the day’s open, same as EOD Telegram `period_pnl_pct`); Account details grid blank · Withdrawals / Deployed · Hit rate (all time) / … / Profit Factor · Source; Open Positions util % = open deployed ÷ Net equity (2 decimals); EOD **rejects** shows a backend count when published, else **—**; SFX counts corrected (359 manifest clips). Version is .105 (not .74) because the chip was already v2.44.95 and cache-busters reached 2.44.104 without change-log rows. Hosted Firebase still **v2.44.95** until `deploy now`. |
| 2026-09-30 | Local sidecar **v2.44.106**: Day P&L KPI subtext reads **`+26.01% Today`** (signed day % then the word **Today**, uncolored). Hosted Firebase still **v2.44.95** until `deploy now`. |
| 2026-09-30 | Local sidecar **v2.44.107**: KPI strip order is **Total P&L · Unrealized (open) · Day P&L · Net equity** (Day P&L and Net equity swapped). Account details **Equity** renamed **Net equity** and swapped with **Day P&L** (row 2 is now Day P&L | Net equity). Hosted Firebase still **v2.44.95** until `deploy now`. |
| 2026-09-30 | Local sidecar **v2.44.108**: Signed money everywhere a P&L result shows — sign before the `$` (`+$1,180.20` / `-$301.50`): Day P&L KPI, Balance history `day` hint, Account details **Day P&L** + **Avg Win**, EOD / day PnL today figure, and Signal EXIT (was `$-301.50`, now `-$301.50`; hosted also rewrites a published `$-` string). Hosted Firebase still **v2.44.95** until `deploy now`. |
| 2026-10-04 | **Deployed v2.45.6** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh`; hosted now matches local source (includes v2.45.2–v2.45.6: reject count, stale-snapshot guard, entry-SFX replay fix, music shuffle bag, Day P&L rollover). |
| 2026-10-05 | **Deployed v2.45.7** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh`; Account details all-time stats live. |
| 2026-11-02 | Local calendar (not deployed): `market-calendar.mjs` matches RESEARCH — `PRE_HOLIDAY` is the previous **market session** before a bank holiday or standalone low-volume day. A listed **Day Before …** skip does not mint a second pre-holiday, so **2026-11-24 and 2026-12-22 trade**. Veterans Day still takes Tue 11-10; Columbus Day Fri 10-09; Christmas Eve Wed 12-23 + Thu 12-24 + Fri 12-25; NYE Wed 12-30 + Thu 12-31; Mon 12-28 trades. Training Calendar, HOLIDAY marquee, and session bells use the same `marketDayStatus`. Hosted v2.46.30 still has the old Tuesday-before-Thanksgiving cell. |
| 2026-11-02 | Docs only (local, not deployed): Training **Current data** row set to book `8.19-11.02` (7,135 rows) and TP 10.23 F105 sha `29805ada…` (8/19–11/2 492 / 391W/101L / net +$150,038.05; 11/2 5W/4L +$1,591.20); previous X_ALL `2c0f0578…`. |
| 2026-11-02 | Docs only (local, not deployed): Training **Current data** row set to TP 10.23 R2_A sha `495b3bcc…` (8/19–11/2 492 / 392W/100L / net +$150,509.87; 11/2 6W/4L +$2,171.70); previous F105 `29805ada…`. |
| 2026-11-03 | Docs only (local, not deployed): Training **Current data** row set to TP 10.23 SV_lean1 sha `ff0255c7…` (new engine `eb812a6f` / `b0065e9d`, 20% Save on; 8/19–11/2 499 / 400W/99L / net +$151,727.43; 11/2 7W/3L +$2,251.20); previous R2_A `495b3bcc…`. |
| 2026-11-03 | Docs only (local, not deployed): Training **Current data** row set to TP 10.23 A_g7 sha `0f296670…` (5 flat TL0 CALL / TL1 PUT bounds on SV_lean1; newest engine `32a80291` / `5e447ecd`, 20% Save stall + UL-retrace on; book rebuilt; 8/19–11/2 500 / 401W/99L / net +$151,770.53; 11/2 7W/3L +$2,251.20); previous SV_lean1 `ff0255c7…` (499 / 400W/99L / +$151,607.43 on this engine). |
| 2026-11-03 | Docs only (local, not deployed): Training **Current data** row set to TP 10.23 P4_rel9 sha `f8c41a59…` (24 flat ORB1 CALL / TL2+ PUT / Breakout PUT bounds on A_g7, pt21u; engine `32a80291` / `5e447ecd`; 8/19–11/2 502 / 401W/101L / net +$152,485.29; 11/2 7W/3L +$2,251.20); previous A_g7 `0f296670…` (500 / 401W/99L / +$151,770.53). |
| 2026-11-03 | Docs only (local, not deployed): Training **Current data** row set to TP 10.23 S1_Q2cap sha `5a8a0dd9…` (51 flat bounds on P4_rel9 under a loss-sum cap of −$6,468.27, pt27u; engine `32a80291` / `5e447ecd`; 8/19–11/2 507 / 406W/101L / net +$153,986.03; 11/2 7W/3L +$2,768.10); previous P4_rel9 `f8c41a59…` (502 / 401W/101L / +$152,485.29). |
| 2026-11-03 | Docs only (local, not deployed): Training **Current data** row set to book `8.19-11.03` (7,387 rows, 11/3 added) and TP 10.23 T1_E2 sha `b0f1489c…` (41 flat bounds on S1_Q2cap under a loss-sum cap of −$6,468.16; engine `32a80291` / `5e447ecd`; 8/19–11/3 536 / 424W/112L / net +$154,273.89; 11/3 5W/2L +$134.70); previous S1_Q2cap `5a8a0dd9…` (8/19–11/3 510 / 406W/104L / +$153,697.03; 11/3 0W/3L −$289.00). |
| 2026-11-03 | Docs only (local, not deployed): Training **Current data** row set to TP 10.23 A99 sha `90f98504…` (77 flat bounds on T1_E2, near-miss A of pt34u, pt40u; engine `32a80291` / `5e447ecd`; 8/19–11/3 523 / 424W/99L / net +$154,117.94, red days 8/24 + 10/09; 11/3 4W/2L +$45.30); previous T1_E2 `b0f1489c…` (536 / 424W/112L / +$154,273.89). |
| 2026-11-03 | Docs only (local, not deployed): Training **Current data** row set to TP 10.23 N3 sha `0ef7a804…` (14 flat bounds on A99, t48u; engine `32a80291` / `5e447ecd`; 8/19–11/3 519 / 420W/99L / net +$154,050.14, 8/24 +$20.10, red days 10/09 only; 11/3 4W/2L +$45.30); previous A99 `90f98504…` (523 / 424W/99L / +$154,117.94). |
| 2026-11-06 | Local S0_maxnet amendment (Eric pt81u; not deployed): installed sha `a449ab0d…` (candidate `6d4682b3…`), 14 flat bounds on C0; 8/19–11/6 542 / 436W/106L / +$166,360.74 / −$6,960.35 / net +$159,400.39. 11/6 Post 9W/3L +$111.70; original Pre P2 6W/6L −$461.10 and intermediate C0 Post 8W/3L +$87.40 retained. No newly admitted losing IDs; red 10/09 only. |
| 2026-11-06 | Docs only (local, not deployed): Training **Current data** row set to book `8.19-11.06` (7,996 rows, 11/6 added) and TP 10.23 C0_maxws sha `46f4305d…` (21 flat bounds on P2_AMZN, pt79u; candidate `696a3184…`; engine `32a80291` / `5e447ecd`; 8/19–11/6 542 / 435W/107L / net +$158,957.89, red days 10/09 only; 11/6 8W/3L +$87.40, Pre P2_AMZN 6W/6L −$461.10); previous P2_AMZN `d480f7c1…` (8/19–11/6 540 / 431W/109L / +$158,816.99).
| 2026-11-05 | Docs only (local, not deployed): Training **Current data** row set to book `8.19-11.05` (7,770 rows, 11/5 added) and TP 10.23 P2_AMZN sha `d480f7c1…` (9 flat bounds on N100, t74u; engine `32a80291` / `5e447ecd`; 8/19–11/5 528 / 425W/103L / net +$159,278.09, red days 09/04 + 10/09; 11/5 5W/0L +$2,477.70, Pre N100 0W/0L $0); previous N100 `a3c4cc86…` (8/19–11/5 520 / 420W/100L / +$157,205.79). |
| 2026-11-04 | Docs only (local, not deployed): Training **Current data** row set to book `8.19-11.04` (7,606 rows, 11/4 added) and TP 10.23 N100 sha `a3c4cc86…` (16 flat bounds on N3, t65 / Eric t65s315; engine `32a80291` / `5e447ecd`; 8/19–11/4 520 / 420W/100L / net +$157,175.79, red days 09/04 + 10/09; 11/4 3W/0L +$2,814.90, Pre N3 3W/4L −$29.90); previous N3 `0ef7a804…` (8/19–11/4 526 / 423W/103L / +$154,020.24). |
| 2026-10-30 | Docs only (local v2.46.29, hosted v2.46.28): second review (Eric t181u) — Current status, Done table, Live freshness and hosting lines set to hosted v2.46.28 / local v2.46.29; Strategy path naming table split into Event Tape long / Open Positions Path short columns. RESEARCH `docs/Firebase.md` hosted version v2.46.21 → v2.46.28. |
| 2026-10-30 | Docs only (local v2.46.28, hosted v2.46.26): Current status, On-screen surfaces, Live freshness and hosting lines updated to hosted v2.46.26 / local v2.46.28; Cloud Run revision `00611-9zz` → `00618-cvb` (per ARCHITECTURE.md); Training Current data refreshed to book 8.19-10.29, profile sha `52468772` (472 trades, 375W/97L, +$143,562.43); open item for the `actions.json` path stamp added under Strategy path naming. |
| 2026-10-30 | **v2.46.28**: **Path display names** (Eric t177u): 🔮 **OR1 Breakout**, ⛓️ **OR2 Breakout**, 📏 **Trendline** (was ORB1 / Breakout). `PATH_DISPLAY` + `pathHeader` / `tapeHeaderFor` in `snapshot.mjs`; tape headers, signal tag and positions Path column render from the ledger path. |
| 2026-10-30 | **v2.46.29** (Eric t180u): Event Tape always uses long names on Fill/Closed rows (📏 Trendline, 🔮 OR1 Breakout, ⛓️ OR2 Breakout); rows without a ledger path take the path from their header (fixes "⛓️ Breakout Closed"). Open Positions Path column uses short names 📏 TL, 🔮 ORB1, ⛓️ ORB2 (`pathShort`). |
| 2026-10-30 | Docs only: v2.46.29 deployed to easy-0dte; hosted/local version lines updated (Current status, Done table, topology, Live freshness, hosting). |
| 2026-10-30 | **v2.46.30** (Eric t185u, local, not deployed): Training Calendar labels weekdays without data from the in-app market calendar (`marketDayStatus`): NYSE holidays `🏖️`, low-volume `🎃`, pre-holiday `🛡️` with the reason; other past book-range weekdays without data show **No data**. Skip days excluded from Month tiles, totals and the Pre trained streak; skip days with replay data keep showing it. |
| 2026-10-30 | Docs only: v2.46.30 deployed to easy-0dte (training TP 10.23 sha 2c0f0578, 488 trades, net +$148,636.26); version lines updated. |
| 2026-11-04 | **v2.46.32** (Eric t197u, local, not deployed): Pre trained profit streak counts every log entry with Pre and Post, including skip days that traded (11-03 Election Day, Pre −$289.00). Streak resets to 0 on a Pre loss: 1/7 → 0/8. Month tiles/totals already counted logged skip days. |
| 2026-11-03 | **v2.46.31** (Eric t191u/t192u, local, not deployed): Boss slam €£$¥ glow is option (b) tight: `text-shadow: 0 0 2px rgba(var(--accent-rgb), .6), 0 0 14px rgba(var(--accent-rgb), .35)` on `.boss-entry .copy` and `.boss-cycler span` (was `0 0 0.04em` + `0 0 0.08em` full `--lime`), so the glow no longer fills the `$` counters. Font unchanged (`--mono`). Follows the theme accent. |
| 2026-10-30 | **v2.46.27**: **Pulse path guard** (Eric t173u–t175u). HOOD-82p-211 showed "ORB1 Fill" then "⛓️ Breakout Fill": Cloud Run `actions.json` stamped `path: ORB1` while the ledger snapshot had `ORB2`. The pulse path is no longer shown; the ledger path is used, else a neutral "Fill" until the snapshot arrives. Added **Strategy path naming** spec (ORB1 Breakout / ORB2 Breakout shown as Breakout / Trendline TL0, TL1, TL2+). Backend fix for the `actions.json` path stamp is pending in RESEARCH. |
| 2026-10-30 | **v2.46.26**: **Top bar flicker fix**. Removed `.topbar` `backdrop-filter: blur(10px)` (re-blurred every frame over the animated `.bg-scroll` tiles, Discovery ticker and pulse dot, causing flashes); bar promoted to its own compositor layer (`translateZ(0)`). Clock chip uses `tabular-nums` + `nowrap`; `.top-meta` no longer wraps at ≥1100px so ticking time can't re-flow the buttons. |
| 2026-10-28 | Local **v2.46.25** (not deployed): **Post trained = current replay book** (Eric t161u). Tracked-day cells, tiles, colors and mini calendar show Post from the current book day (log `post_*` kept as history, used only beyond the book end); Pre stays logged. Oct 2026: **19d, 16 green / 3 red, 162 trades 128W/34L, +$37,715.60**; 10/23 Post $2,707.00 14W/0L (log $2,990 16W/0L), 10/26 Post $1,405.10 5W/0L (log $1,349, W/L null). Calendar totals = book 476 / 378W/98L / +$143,054.49; sync check enforces it. |
| 2026-10-28 | Local **v2.46.25** (not deployed; hosted v2.46.24): **Month tiles include tracked days** (Eric t159u). `buildMonth` stats now count every trading day using Post trained on tracked days (10/26 Post W/L null → book 5W/0L), computed at render time. Oct 2026: **19d, 16 green / 3 red (10/9, 10/27, 10/28), 164 trades 130W/34L, +$37,942.50**, best Oct 2 +$6,694.60, worst Oct 9 −$762.70 (was 15d / 14G 1R / 131 / $33,720.10). 10/23 cell already renders `tracked up` (Post +$2,990) — test added. `SynchTradeProfileResults` adds a consistency check printing per-month/overall calendar totals. |
| 2026-10-28 | Docs only (local **v2.46.24** pending deploy; hosted v2.46.23): Training overlay section rewritten as a spec against `training.mjs`, `SynchTradeProfileResults.sh`, `training_day.mjs` and `deploy_desk_hosting.sh` (title **Training Calendar**, **P&L <Month YYYY>**, Pre/Post on every data cell, Month stats tiles, two-line streak, single source, auto-pick, formats, gate, daily process, current data 8/19–10/28). Removed stale "Pre / Post trained (N days)" stats tile, "10/27 pending", hand-edit instructions and the old P&L column order. |
| 2026-10-28 | Local **v2.46.24** (not deployed; hosted v2.46.23 deployed 2026-10-27 14:39 PT): streamlined training publish (Eric t151u + 10.23 Trade Profile Bot). **SynchTradeProfileResults** auto-picks the newest `tests/fixtures/tp_10_23_replay_book_8.19-<MM.DD>.csv`, supports `admitted`-flag books (date+trade_id, P&L `pnl_pc_v2` > `pnl` > `pnl_step17` > `pnl_step*`), and **gates the deploy** when the book's last date has no Pre/Post log entry. New **`npm run training:day`** (`scripts/training_day.mjs`) upserts one day with profile/engine shas and win/loss sums. Published 10/28: Pre −$238.10 3W/6L (f024dfe9), Post −$65.90 5W/4L (82e8f1d9); book 8/19–10/28 = 476 / 378W/98L / +$143,054.49; streak 0/4. |
| 2026-10-27 | Local **v2.46.22** (not deployed; hosted v2.46.21): new **Training** button `#btn-training` between the version chip and Background icon (Eric: "create a new button, between the version and the SVG button … Training … an overlay that appears over the desk site, over even the Market Closed banner"). Full-viewport overlay (z 10000, above the ClosedBar), Close/Esc, theme-aware. **P&L Calendar** modeled on Eric's reference: month header stats, Mon–Fri grid + Week column, holidays from `market-calendar.mjs`, months Aug 2026 → current. Data `public/training/trade_profile_replay.json` from RESEARCH TP 10.23 (P3, sha `a4dd0222…`) on the 8/19–10/26 replay book via new `scripts/write_training_data.py` (read-only): **468 trades, 373W/95L, net +$142,800.19** (Aug +$31,128.60 / 101; Sep +$73,765.19 / 214; Oct 1–26 +$37,906.40 / 153). New `public/js/training.mjs`, `scripts/test-training.mjs` (in `npm test`). 10/27 to be appended after today's session. **Theme 2 `--unreal-up` is `#ff9900`** (was `#fed41e`; Eric: darker gold for the light theme), ~2.1:1 on white, still below 3:1 so the Theme 2 exemption stays; all other themes stay `#fed41e`. **Training data source → Profit Capture 10.23 SGUL book (`pnl_step17`, sha `b3618a54…`)**: net +$142,800.19 (STEP 7A) → **+$143,376.19**; same 468 trades / 373W / 95L; 5 trades differ (+$576): D-MARA-10.5c-430 9/02 +$34→+$74, D-MRVL-235c-391 9/14 +$37.40→+$71.40, D-QCOM-182.5c-344 9/14 +$45.40→+$231.40, D-CRWV-86c-298 9/16 +$47.10→+$35.10, ORCL-132c-668 10/15 +$58.80→+$386.80. `write_training_data.py` gains `--admitted-book/--pnl-col/--meta-book`. **Training layout (Eric t115u):** top = model summary (`Trade Profile 10.23 In-Sample replay` / first → last data day / `N trades · W/L · net $X` with net in profit/loss color, all from the JSON), then month title + Prev/Next + calendar grid, then the monthly stats row (Monthly P&L, Trading days, Trades, Best day, Worst day) **under** the calendar. **Training backdrop (Eric t116u):** solid 0.92 dim + 6px blur → translucent `rgba(var(--bg-rgb), 0.35)` + 2px blur so the desk shows behind; panel stays opaque `--bg-panel` with margin; backdrop click closes. Summary date range shown as **MM-DD-YYYY** (`08-19-2026 → 10-26-2026`; Eric t117u). **Forward model training tracking (Eric t118u):** new `public/training/model_training_log.json` (10/23 Pre −$749.00 / Post +$2,990.00; 10/26 Pre +$111.00 / Post +$1,349.00; 10/27 pending); day cells from 10/23 show Pre/Post trained after the replay P&L, Week and month stats sum them, summary adds a Pre trained streak line. **Week cells checkerboard (Eric t119u):** subtle theme-aware 11px checker (`--text` 6% over `--bg-panel`) separates the Week column from the daily cells. **Tracked-day cells (Eric t122u):** days with a training-log entry show only `Pre trained: $ W/L` / `Post trained: $ W/L` (replay lines dropped); log gains `pre_wins/pre_losses/post_wins/post_losses` (10/23 0W/2L → 16W/0L; 10/26 null); Week/month replay totals now exclude tracked days, which are summed as Pre/Post; summary line is now only `Pre trained profit streak 1/2` (Eric t123u; the Forward tracking line was removed, no certification line). Log vs replay: 10/23 Post $2,990.00 16W/0L = replay; 10/26 Post $1,349.00 vs replay $1,367.30 5W/1L (−$18.30). **Mini year calendar (Eric t124u):** Aug→current month mini grids right of the summary (green/red days by displayed result, Post on tracked days), hover date+P&L, click jumps the big calendar; stacks below on mobile. **Mini calendar fix (Eric t126u):** first partial week floated above a gap because leading blank squares used class `empty`, which the desk's global `.empty` rule pads 1.1rem; now `.tm-blank`, `.tm-d` padding 0 and `grid-auto-rows: 11px`. **Week column removed (Eric t127u):** big calendar is Mon–Fri only (5 columns); Week cells and their checker CSS removed (week sums stay in the month model, unrendered). **Stats boxes (Eric t128u):** the monthly stats row under the calendar is now boxed cells with the same 6% / 11px theme-aware checker. |
| 2026-10-27 | Docs only (no version bump; hosted/local v2.46.21): Frontend.md reviewed against code and live site (Eric: "Review the docs and ensure they are correct"). Status/hosted lines updated to v2.46.21 deployed 08:22 PT; sound counts recounted from `public/sounds/manifest.json` (373 clips: open 51, music 59; moon 16 not listed; 389 files on disk); deploy sequence lists the script steps and the firebase-tools ≥ 15.34.1 requirement; Event tape profit color described as theme `--profit`. |
| 2026-10-27 | **Deployed v2.46.21** to Firebase Hosting site `easy-0dte` (project `YOUR_FIREBASE_PROJECT`, OAuth site untouched) at **08:22 PT** (Eric: "deploy"). Earlier attempts 07:52–08:19 PT timed out uploading `/sounds/music/05 Sand Storm.ogg` (new 1.5 MB file from the sound sync); firebase-tools updated 14.6.0 → 15.34.1; succeeded on a plain re-run. Verified live: chip v2.46.21, Theme 6/7 Cur% `--profit` rule, Theme 6 `--profit` `#3ee08a`, `--unreal-up` `#fed41e` in all 7 themes, `/sounds/manifest.json` identical to local. |
| 2026-10-27 | Local **v2.46.21**: **Theme 6 Red & Silver: every profit number uses the Avg Win / Best Trade green `--profit` `#3ee08a`** (Eric: "we already have a green on Theme 6 that is in Avg Win and Best Trade. Lets use that green on the profit numbers instead of the Theme1 green"). Open Positions Cur% gain, which used the red accent `--lime`, now uses `--profit` in Theme 6 via a scoped CSS rule. Total/Day P&L, Event Tape and Signal exits already used `--profit`. Contrast ~10.3:1 panel / ~9.5:1 elev. **Theme 7 Hot Pink:** Open Positions Cur% gain also uses its `--profit` green `#4ade80` instead of the pink accent (Eric: "the cur% accent needs to be the Profit color on Theme 7 too"); same scoped rule. Themes 1–5 unchanged. Loss `#990033`, accent and other colors unchanged. **All themes: positive Unrealized (Open) `--unreal-up` is the Theme 1 gold `#fed41e`** (Eric: "Lets get each Theme to use the same gold color on the Unrealized (Open) in profit on each Theme"); was T2 `#2B42FF`, T3 `#ffd23f`, T4 `#ffd447`, T5 `#ffe14d`, T6 `#ffd447`, T7 `#ffe14d`. Theme 2 gold on white ~1.4:1, exempt from 3:1 (Eric's choice). |
| 2026-10-27 | Local **v2.46.23** (not deployed): **SynchTradeProfileResults** (`scripts/SynchTradeProfileResults.sh`, `npm run training:sync`) refreshes `public/training/trade_profile_replay.json` + `model_training_log.json` from the one source copy in RESEARCH `training/` (seeded with the 8/19–10/27 book, profile `f024dfe9`, 470 trades 375W/95L net +$142,853.39, and the Pre/Post log incl. 10/27); runs automatically in `deploy_desk_hosting.sh` after the sound sync (Eric t145u). `write_training_data.py` evaluator mode gains `--pnl-col` / `--include-open`. Tests re-pinned (streak 0/3). |
| 2026-10-26 | **Redeployed v2.46.20** to Firebase Hosting site `easy-0dte` (OAuth site untouched) at **11:53 PT** (Eric: "Deploy once more to Firebase"). Changed since 11:46: svg folder only (Instant mix, Motion mode, Science removed; manifest regenerated, 51 icons; `public/sounds/manifest.json` rewritten by the 11:46 deploy's sfx sync). No app code changed, version stays v2.46.20 (manifest fetched with `?v=` and `no-cache`). Verified live: chip v2.46.20; manifest 51 icons; dollar, box, Award star, Universal currency alt SVGs 200; topbar order version → Background icon → Theme → Music → SFX; Theme 1→2 and dollar→box cycle and save; data loads (Net Equity $89,196.70). npm test: only the 2 known failures. |
| 2026-10-26 | **Deployed v2.46.20** to Firebase Hosting site `easy-0dte` (project `YOUR_FIREBASE_PROJECT`, OAuth site untouched) at **11:46 PT** (Eric: "Deploy the Frontend Desk UI to Firebase"). Ships v2.46.8–v2.46.20: Theme button (7 themes, Theme 6 loss `#990033`), single mask tile, Background icon button. Verified live: chip v2.46.20; topbar order version → Background icon → Theme → Music → SFX; `/svg/manifest.json` (54 icons), `/svg/bg-currency-mask.svg`, `/svg/bg-box-seam-mask.svg` 200; `/preview-hosted.mjs` 404; Theme click 1→2 saved; Background icon click dollar→box saved; data loads (Demo Net Equity $89,279.10). `desk-config.js` unchanged. npm test: only the 2 known failures. |
| 2026-10-26 | Docs only (local **v2.46.20**, no code or version change; hosted still v2.46.7): topbar audit (Eric: "Make sure a note on the background svg icon button before the version tag and the Theme button is noted"). Verified `index.html` order is version chip → **Background icon** → **Theme** → Music → SFX (the icon button sits **after** the version chip, not before it). Chrome rows, Topbar row, Background icon and Color themes specs now list the controls in that order with one-line notes (Background icon: shows the current tile icon, fixed-order cycle, `easy0dte.bgIcon`; Theme: cycles 1–7, `easy0dte.theme`). Stale "DEMO/RESEARCH chips" replaced by the data-source chip. Icon list refreshed to 54 (Eric's folder edits; manifest regenerated). |
| 2026-10-26 | Local **v2.46.20** (not deployed; hosted still v2.46.7): **Theme 6 Red & Silver loss is `#990033`** (Eric: "theme 6, the loss (-) is purple, but it should be #990033"): `--red` `#990033`, `--red-rgb` `153, 0, 51`, `--red-dim` `rgba(153, 0, 51, 0.65)`, tape `--put` `#990033` (PUT stays the loss color). Contrast ~2.01:1 on panel `#17191d`, ~1.87:1 on elev `#1d2025`, below 3:1, so `test-themes` exempts Theme 6 `--red`/`--put` as Eric's choice. Loss is now close in hue to the accent `#ff2e3f`; test still asserts they differ. `public/svg/manifest.json` regenerated (Eric's folder now has 59 icons; Paid and Trending up removed during this build). Nothing else changed. |
| 2026-10-26 | Local **v2.46.19** (not deployed; hosted still v2.46.7): added Ultima Bot's second background tile, the dark-mode **box** (Bootstrap `box-seam-fill`, from Ultima `Welcome.jsx` line 65), as `public/svg/bg-box-seam-mask.svg` (black, 16 × 16 viewBox, like the dollar tile, which is Ultima's light-mode tile). New `PINNED_BG_ICONS` keeps the pair together: dollar, box, then the rest alphabetically (63 icons; Eric's folder edits during this build: removed Attach money, Bar chart, Euro, Heart check, Owl, Sports esports; Currency exchange → `currency_1exchange`; added Currency euro, Currency q). Test updated. |
| 2026-10-26 | Local **v2.46.18** (not deployed; hosted still v2.46.7): new **Background icon** button (`#btn-bg-icon`, between version and Theme) shows the current tile icon and cycles, in fixed manifest order with wrap, through the icons in Eric's new **`public/svg/`** folder (66: the dollar tile, moved there from `public/` and kept first as the default, then 65 Material icons alphabetically). Choice saved in `easy0dte.bgIcon` (fallback dollar), applied at first paint; independent of the theme (theme still colors the tile). New `public/js/bg-icons.mjs`, `public/svg/manifest.json`, `scripts/write_svg_manifest.mjs` (`npm run svg:manifest`, run by the deploy script), `scripts/test-bg-icons.mjs` (in `npm test`). |
| 2026-10-26 | Local **v2.46.17** (not deployed; hosted still v2.46.7): **one tile for all themes**. The 7 per-color tiles (`bg-currency.svg`, `-blue`, `-purple`, `-cyan`, `-amber`, `-silver`, `-pink`) are replaced by one monochrome `bg-currency-mask.svg` used as `mask-image` (+ `-webkit-mask-image`) on `.bg-scroll`, colored by new `--tile-color` (replaces `--bg-tile`) at the unchanged `--tile-opacity`. Colors equal the old fills (#336600, #3b82f6, #6b3fa0, #1fb6d6, #c2611f, #8a9099, #c2187a), so every theme looks the same; size, spacing and `diagonalScroll` motion unchanged. A new theme now needs only a color, no SVG. Test updated (one mask file, every theme has tile color + opacity, no per-color references); stale `#00F6FF` test comment fixed. |
| 2026-10-26 | **Docs only, no code change, no version bump** (local then v2.46.16, hosted v2.46.7): Frontend.md audited against `themes.mjs`, `styles.css`, `index.html`, `app.js`, tiles and `test-themes.mjs`. **Color themes** rewritten as a spec (button and selection, theme summary, P&L policy, themed vs fixed, contrast rule with the Theme 2 equity exemption, full generated variable table for all 7 themes, files, how to add a theme, test gate) and moved next to **Sound effects**. Removed stale text: "cycle 1 → 2 → 3 → 1", the sync-chip history, per-theme narratives, "Themes 1–3" in status, the misaligned variable table. Panels text now names variables instead of Theme 1 hex values (Net equity, Unrealized, Day P&L, Withdrawals, ENTRY, boss slam, discovery colors); Event Tape headers corrected to `--text` (they were documented as Equity blue). |
| 2026-10-26 | Local **v2.46.16** (not deployed): Arctic Slate and `/bg-currency-slate.svg` removed (Eric: "Theme 6 is too similar to the others. Try a Hot Pink or a Red and Silver theme instead"). New **Theme 6 Red & Silver** (graphite/gunmetal, silver text and borders, red accent `#ff2e3f`, profit `#3ee08a`, loss **magenta `#ff5ccf`** to differ from the accent, tile `/bg-currency-silver.svg` 0.1, meta `#0e0f12`) and **Theme 7 Hot Pink** (plum-black, pink accent `#ff2fa8`, profit `#4ade80`, loss **orange-red `#ff6a3d`**, tile `/bg-currency-pink.svg` 0.12, meta `#120a10`). Cycle 1→…→7→1; label stays **Theme**. Contrast ≥4.4:1 (Theme 6) and ≥5.2:1 (Theme 7). Themes 1–5 unchanged. |
| 2026-10-26 | Local **v2.46.15** (not deployed): new **Theme 6 Arctic Slate** (light; Eric: "Next one, Theme 6"): icy slate page, white panels, teal accent `#0f766e`, profit `#15803d`, loss `#dc2626`, Net Equity `#4338ca`; registry entry, generated CSS block, faint `/bg-currency-slate.svg` tile (0.08), first-paint meta `#e9eef4`. Cycle is 1→…→6→1; label stays **Theme**. All text/P&L colors ≥4.6:1. Themes 1–5 unchanged. |
| 2026-10-26 | Local **v2.46.14** (not deployed): new **Theme 5 Sunset Amber** (Eric: "Great job. Now to Theme 5."): charcoal panels, orange accent `#ff8c32`, profit `#4be38a`, loss `#ff5c7a`, Net Equity `#7fd4ff`; registry entry, generated CSS block, subtle `/bg-currency-amber.svg` tile (0.14), first-paint meta `#120e0b`. Cycle is 1→2→3→4→5→1; label stays **Theme**. All text/P&L colors ≥5.8:1. Themes 1–4 unchanged. |
| 2026-10-26 | Local **v2.46.13** (not deployed): new **Theme 4 Midnight Cyan** (Eric: "Theme 1, 2, 3, is approved for now. Now make a new Theme 4"): navy panels, cyan accent `#22e1ff`, profit `#3cf08f`, loss `#ff5a6e`, Net Equity `#b49cff`; registry entry, generated CSS block, `/bg-currency-cyan.svg` tile, first-paint meta `#060d1a`. Cycle is 1→2→3→4→1; label stays **Theme**. All text/P&L colors ≥5.6:1. Themes 1–3 unchanged (approved). |
| 2026-10-26 | Local **v2.46.12** (not deployed): Theme 2 Net Equity (`--equity`, KPI and account value) set to **`#00B1B8`** (Eric: "We only wanted the Net Equity color to change, not the balance chart ... try the Theme 2 Net Equity color to be 00B1B8"). Balance chart line, dot, glow and fill reverted to the accent `#0070f3` (v2.46.10 look; `--chart-line`/`--chart-rgb` now accent in all themes). New `--equity-info` keeps the ENTRY signal and closed discovery chips on the v2.46.10 colors (Theme 2 `#0369a1`; Themes 1 and 3 alias `--equity`, unchanged). Profit/unrealized `#2B42FF` kept. `#00B1B8` is ~2.6:1 on white; test exempts it by request. |
| 2026-10-26 | Local **v2.46.11** (not deployed): Theme 2 profit (`--profit`, glow `rgba(43, 66, 255, 0.12)`) and unrealized gain (`--unreal-up`) set to **`#2B42FF`** (Eric: "the unrealized + profit color should be 2B42FF. Loss is the same"). Theme 2 Net Equity (`--equity`) and the equity chart line, live dot, glow and fill (new `--chart-line` / `--chart-rgb`, which equal the accent in Themes 1 and 3) set to **`#00F6FF`** (Eric, 10:06 PT). `#00F6FF` is about 1.5:1 on white, so the test exempts it from the 3:1 rule by request. This covers the KPI, account, positions, tape, EOD, exit and hint profit values and the unrealized-up values. Loss red `#dc2626` and Themes 1 and 3 unchanged. Pixel-identical in Themes 1 and 3. |
| 2026-10-26 | Local **v2.46.10** (not deployed): Theme button label is always **Theme** (no number); tooltip `Theme: <name>, click for next`; still cycles and saves. New **Theme 3 Royal Purple & Gold** (dark aubergine `#0d0817` / panels `#140c22`, gold accent `#f5c542`, text `#efe7ff`, muted `#9a8cb8`, profit `#3ddc84`, loss `#ff5c6c`, tile `bg-currency-purple.svg` fill `#6b3fa0`), an original palette. Themes 1 and 2 unchanged. `test-themes.mjs` now checks 3:1 contrast for every non-default theme on `--bg-panel` and `--bg-elev`. |
| 2026-10-26 | Local **v2.46.9** (not deployed): **Theme 2 is now Ultima Bot's light PlayStation Blue** (Eric: "the UltimaBot Frontend Playstation theme is light grey not dark black … use the Playstation Blue light theme"). Palette from Ultima `index.css`, `tailwind.config.js` `light.*`, component light classes and `Welcome.jsx` light SVG fill: page `#F2F2F2`, panels white, text `#111111`, muted `#6b7280`, accent `#0070f3`, ClosedBar headline `#003791`, tile fill `#3b82f6`, profit `#16a34a`. Darker loss red, equity, entry, ticker, unrealized, WATCH, withdrawals and CALL/PUT colors chosen for contrast (documented in Color themes). All remaining hardcoded colors and dark shadows in `styles.css` moved to variables (Theme 1 values unchanged). Themes are now CSS `html[data-theme]` blocks generated from `themes.mjs`, and an inline `<head>` script applies the saved theme before first paint. `test-themes.mjs` adds checks that the registry and CSS stay in sync, that P&L meaning holds, and that Theme 2 meets 3:1 contrast. |
| 2026-10-26 | Local **v2.46.8** (not deployed): topbar **sync** chip replaced by a **Theme** button that cycles saved color themes (Eric: "change it to a Theme button … cycle the themes like the music does"). New registry `public/js/themes.mjs` (Theme 1 Green = default, unchanged; Theme 2 PlayStation Blue, accent `#0070f3` from Ultima Bot); choice saved in `localStorage.easy0dte.theme`, falls back to Theme 1. Hardcoded accent colors in `styles.css` and the equity chart moved to CSS variables with Theme 1 values unchanged; P&L colors split to a fixed `--profit`. New `public/bg-currency-blue.svg`. New `scripts/test-themes.mjs` in `npm test`. Version bumped everywhere. |
| 2026-10-24 | **Deployed v2.46.7** to Firebase Hosting site `easy-0dte` (OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **15:14 PT** (Eric, 15:12 PT: "Deploy Frontend to Firebase"). Pre-checks: banner, calendar cross-check, chart-perf tests pass; no preview-hosted.mjs in public/; desk-config.js unchanged. Live verified at 1280: chip v2.46.7, GIF 200×210 (HTTP 200), left 67.1% / right 17.1%, headline clear, equity $100,000.00. |
| 2026-10-24 | Local **v2.46.7**: ClosedBar mascot position now proportional (Eric: "70% in … [70% here 20%]"). `--mcb-gif-right: max(var(--mcb-inset), calc(25% - 100px))` drives GIF margin-right and strip padding-right; GIF centered at 75% of the bar, floored at the inset so mobile is unchanged. 200px width and 2:1 overhang kept. Measured: Bar 1259/1419/804/380 px → left/right 67.1/17.1%, 68.0/18.0%, 62.6/12.6%, 45.8/1.6% (6 px) (1280×800, 1440×900, 820×1180, 390×844). Headline clear of GIF on desktop/wide/tablet; mobile as in v2.46.6. Tests pass. |
| 2026-10-24 | **Deployed v2.46.6** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **15:08 PT** (Eric, 15:08 PT: "Deploy to firebase"). Pre-checks: market-closed banner, calendar cross-check, chart-perf tests pass; no preview-hosted.mjs in public/; desk-config.js unchanged. Live verified: chip v2.46.6, GIF 200×210 (HTTP 200), banner y197–407 below KPIs, overhang 97/49, equity $100,000.00. |
| 2026-10-24 | Local **v2.46.6** (not deployed): ClosedBar mascot GIF **200 px wide** (was 300; `height: auto` → 210 px from the 400×420 source; HTML `width=200 height=210`; strip right padding reserves 200 px). Inset `clamp(6px, 1.1vw, 14px)` and the 2:1 overhang unchanged; banner height follows the GIF, so nothing clips. Measured on preview: desktop 1280 GIF 200×210, banner y197–407 (below KPIs at y187), overhang 97 above / 49 below, right gap 14 px; mobile 390 overhang 111 / 55, gap 6 px. Version bumped everywhere (chip, `VERSION`, `package.json`, `?v=` cache-busts). Banner, calendar cross-check and chart-perf tests pass. GIF file unchanged (Eric's original). |
| 2026-10-24 | **Deployed v2.46.5** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`, OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **14:41 PT** (Eric, 14:40 PT: "lets deploy the Frontend Desk UI to Firebase"). Includes v2.46.5 parts 1–3: ClosedBar GIF `/img/closedbar/sleeping/mascot-sleeping.gif` (original 9.8 MB file, loaded only when the bar first shows; 300 px wide, 2:1 overhang), single calendar source `marketDayStatus` / `resolveMarketClosed`, NYSE Rule 7.2 (no Friday Dec 31 closure for a Saturday Jan 1), PRE_HOLIDAY no chaining, calendar cross-check 2026–2036 passing against RESEARCH. Verified live: chip v2.46.5, GIF 200 (9,807,137 B), Market Closed banner showing Saturday, ledger populated ($100,000 Demo). `desk-config.js` unchanged. |
| 2026-10-24 | Local **v2.46.5** (not deployed), part 2: **ClosedBar image** now `CLOSED_BAR_IMAGE = '/img/closedbar/sleeping/mascot-sleeping.gif'` (one constant in `app.js`; set only when the bar first shows, so the 9.8 MB file never downloads during RTH; Eric's original untouched). **One calendar source:** new `marketDayStatus(ymd)` in `market-calendar.mjs` (weekend / nyseClosed / tradingDisabled / reason / earlyClose) drives `isMarketClosedAt` and mirrors RESEARCH `should_skip_trading`; new `resolveMarketClosed` prefers a fresh (<5 min) Cloud Run `market_session.market_open` flag if one is ever published (none today), else the calendar. Low-volume and pre-holiday skip days keep the market open, so the ClosedBar stays hidden in RTH and the HOLIDAY marquee explains the skip. New `scripts/test-calendar-crosscheck.mjs` walks 2026-2027 against the backend module (read-only python3): all skip days and reasons match; known backend gap 2027-12-24 (NYSE closed for Christmas observed, backend says LOW_VOLUME Christmas Eve). |
| 2026-10-24 | Local **v2.46.5** (not deployed), part 3: calendar review against Cursor's backend update. `test-calendar-crosscheck.mjs` now walks **2026-2036**; 2027-12-24 now matches (MARKET_CLOSED Christmas observed). Frontend fixes: (1) **NYSE Rule 7.2**: a Saturday Jan 1 is not observed on Friday Dec 31 (only Sunday -> Monday), so 2027-12-31 and 2032-12-31 stay open as LOW_VOLUME New Year's Eve; (2) **PRE_HOLIDAY no longer chains** (it used to mark Thu 2028-12-28, 2033-12-29, 2034-12-28 as pre-pre-holiday). RESEARCH backend later matched Rule 7.2 (New Year's Sunday→Monday only; federal Dec 31 observed NYD ignored). |
| 2026-10-24 | Local **v2.46.5** (not deployed): Market Closed mascot GIF moved right and up. Right inset `clamp(6px, 1.1vw, 14px)` (was `clamp(10px, 2.6vw, 36px)`, 33px at 1280); strip top `calc((100% - strip-h) * 2/3)` so the GIF overhangs the strip 2:1 above:below instead of centered. Eric replaced the GIF at 14:06 PT with a transparent **400x420** (9.8 MB) file; shown at **300px wide** per Eric (height auto = 315px from the 400x420 aspect; `max-width` cap on narrow phones), and the banner height now follows the GIF so nothing is clipped; still just below the 4 KPI cards. |
| 2026-10-24 | **Deployed v2.46.4** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`, OAuth site untouched) at **13:15 PT**. Verified live: chip v2.46.4, `/img/mascot-sleeping.gif` 200 (188,933 B), Market Closed banner visible (Saturday), data populated from Cloud Run (Demo equity $100,000.00 after the Cloud Run reseed). Preview proxy `scripts/preview-hosted.mjs` not in `public/` (404 live); production `desk-config.js` unchanged. |
| 2026-10-24 | Local **v2.46.4** (not deployed): **Market Closed overlay banner** on the Balance history card. Shows "The Market is Closed!" with the sleeping-bull mascot GIF (`public/img/mascot-sleeping.gif`) on the right, GIF in front of and taller than the lime strip. Visible after EOD, overnight, weekends, NYSE holidays and early-close afternoons via new `isMarketClosedAt` in `market-calendar.mjs` (pre-holiday skip days follow RTH bounds since the market is open). (Superseded same day:) absolutely positioned inside the card (scrolls with page), never over the 4 KPI cards or Account details; `pointer-events: none`; sizes via `clamp()` for desktop and iPhone. DOM touched only when the closed state flips (30s check), so no per-second re-render. Test: `scripts/test-market-closed-banner.mjs`. **Redesign (same v2.46.4):** banner + GIF now span the full window width (small left/right margin) at z-index 900 over every desk box incl. Account details, anchored in flow just below the 4 KPI cards (`.mcb-anchor`, scrolls with page); GIF right, taller than the strip; transparent GIF to be supplied by Eric at the same path. **GIF native size:** 360x240 shown 1:1 (`width:360px`; only `max-width: calc(100% - 2*inset)` caps it on very narrow screens), inset `clamp(10px, 2.6vw, 36px)` from the bar right end; banner box 240px tall with the 44-72px strip vertically centered so the GIF overhangs above and below unclipped. **Local preview:** `node scripts/preview-hosted.mjs` serves `public/` on 127.0.0.1:PORT and proxies the four Cloud Run desk_public files same-origin under `/gcs/` (bucket CORS allows only https://easy-0dte.web.app), `Cache-Control: no-store`; production `desk-config.js` unchanged. |
| 2026-10-15 | **Deployed v2.46.3** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`; OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **07:45 PT** (Eric, 07:31 PT: "Build the fix as v2.46.3 and deploy now"). Live channel last release 2026-10-15 07:45:39 PT. Verified: chip `v2.46.3`, `?v=2.46.3` assets; hosted `index.html`, `app.js`, `styles.css`, `js/snapshot.mjs` (VERSION `2.46.3`), `js/intl-cache.mjs`, `js/market-calendar.mjs`, `js/discovery-source.mjs`, `js/discovery-ticker.mjs`, `js/sfx.mjs` byte-identical to local. Post-deploy headless Chrome on the live site (07:46–07:49 PT, 3 min): renderer **258–314 MB flat** (v2.46.2: 4.3–9.6 GB), script time **0.9 s in 180 s** (v2.46.2: ~205 s of long tasks in 215 s), longest task 226 ms (tape change; v2.46.2: 34.6 s), background drift running on `transform`. Tests: all `npm test` steps pass except the known pre-existing `test-sfx-manifest` (moon clips); new `test-chart-perf` passes. |
| 2026-10-15 | Local **v2.46.3**: **Hosted desk no longer freezes or crashes (Chrome "Aw, Snap! Error code: 5").** Eric (Thu 07:16 PT): since v2.46.2 the background SVG stalls, the page slows, then the tab dies. Cause: the equity chart redraws every 1 s clock tick (`paintClock`) and on every poll, and each draw built ~11.5k `Intl.DateTimeFormat` objects (`ptParts` / `ymdInTz` / `isPtWeekend` constructed per call, ×2 passes per history point since v2.46.0 `downsampleEquityHistory`). On the live 409-point history that was ~0.85 s per draw; headless Chrome logged 205 s of long tasks in 215 s (max 34.6 s) and the renderer grew to 4.3–9.6 GB (ICU native memory, JS heap only 3–14 MB) → OOM. Fix: new `public/js/intl-cache.mjs` (one formatter per locale+options, byte-identical output; invalid dates still read `Invalid Date`); `snapshot.mjs` / `market-calendar.mjs` / `discovery-source.mjs` / `discovery-ticker.mjs` use it; `ptWallToMs`, `rthBoundsMs`, `isPtWeekend`, `rthSessionYmd` memoized (bounded, pure). `layoutRthChart` split into `buildRthChart` (sessions, bounds, labels) + `placeRthChart` (clamp to now + xNorm); `app.js` caches the downsample + build per history signature (every point's t + equity, mode) so the 1 s clock only re-places today's "now" end. `renderTape` skips the DOM rebuild when no rendered tape field changed. Background `.bg-scroll` drift moved from `background-position` to a compositor `transform` (oversized by one 180px loop; same tile phase, speed, direction) so it never waits on the main thread. Chart output unchanged (old vs new layout identical on the live history at several `now`s; 20k random timestamps 2025–2027 give identical RTH day / PT ymd / clock text / bounds / marquee text). Bench (live 409-pt history): full draw **~850 ms / 11,514 formatters → ~3 ms / 0** (4 built once); clock tick re-place ~0.2 ms. New `scripts/test-chart-perf.mjs` (+ fixture `scripts/fixtures/equity-history-2026-10-15.json`). Ledger-truth / read-only behavior unchanged. |
| 2026-10-14 | **Deployed v2.46.2** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`; OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **18:09 PT** (Eric, 18:00 PT: "Please deploy the Frontend desk UI to the Firebase hosting"). Live channel last release 2026-10-14 18:09:26 PT. Verified: chip `v2.46.2`; `app.js` / `desk-config.js` / `styles.css` `?v=2.46.2`; hosted `index.html`, `app.js`, `js/snapshot.mjs` (VERSION `2.46.2`), `js/market-calendar.mjs` (200, `text/javascript`, pre-holiday HOLIDAY copy), `desk-config.js`, `js/sfx.mjs`, `styles.css`, `sounds/manifest.json` byte-identical to local. `/sounds/**` Range returns **206**; the removed tracks return **404**. Hosted `desk-config.js` reads only Cloud Run's four `desk_public` objects (its one `127.0.0.1` is a comment). Mac sidecar off. Live Cloud Run schema 2 (snapshot 2.47.0 written 12:28 PT, discovery 12:30 PT) renders: all-time **83W/299L 21.7%**, avg **+$75.55 / -$106.70**, best/worst **+$539.10 / -$409.50**, PF **0.20** (`equity_curve`, 382 trades). Rejects **171** today. Deployed / cap **$600** (sum cap on; 90%, 50 slots, revision `00599-lhd`; fresh). Equity **$74,366.50**. `npm test`: only the pre-existing `test-sfx-manifest` moon-clip gap; every other test passes. Hosted now matches local. |
| 2026-10-14 | Local **v2.46.2**: **Pre-holiday skip days show the HOLIDAY banner with the reason.** Oct 12 edit (12:11 PT, not by this desk session): `public/js/market-calendar.mjs` now mirrors RESEARCH `calculate_pre_holiday_skip_sessions` (`modules/dynamic_holiday_calculator.py`). The weekday before each market-closed holiday and before each low-volume day (not chained further) is a `PRE_HOLIDAY` skip, carrying `upcoming` {ymd, name, reason}. `isSessionBellYmd` is false that day, so there are no bells or session, and Day P&L rolls to the previous real session. That is 24 days in 2026–27, for example Fri 09-04, Fri 10-09, Tue 11-10, Tue 11-24, Wed 12-23, Wed 12-30. Eric (Wed 10-14 18:05 PT): the day uses the same **HOLIDAY** label and style as a real holiday (🏖️ "Today is a Holiday! - Day before <name>!"), and the Why line reads "Trading disabled today: day before <real holiday name> (<its date>), pre-holiday low-volume risk session." The name and date come from the calendar's `upcoming`. Without it the name comes from the entry and no date is shown. This replaces the Oct 12 "🛡️ Pre-Holiday Risk Session" title. Tests: `test-session-marquee` asserts the HOLIDAY label + reason with name and date for Fri 2026-09-04 (Labor Day, Mon Sep 07) and Fri 10-09 (Columbus Day), and that Labor Day itself keeps its own copy. `test-day-rollover` holiday cases moved to the real last sessions (Labor Day → Thu 09-03; Thanksgiving → Mon 11-23), asserts unchanged. `test-cloudrun-desk-fields` check D moved from Fri 10-09 to Tue 10-13 08:05 PT (a real session, sizing > 24 h old), asserts unchanged + 1. Also: `npm run sfx:sync` drops the two music tracks Eric removed from `soundeffects/` ("03. Guardia Millenial Fair.ogg", "1-06 Ormus.ogg"); the manifest changed only by those two. The app.js `market-calendar.mjs?v=2.46.2` import tag from Oct 12 now matches the version. Files: `public/js/market-calendar.mjs`, `public/app.js`, `public/index.html`, `public/js/snapshot.mjs`, `public/js/sfx.mjs`, `public/sounds/`, `package.json`, `scripts/test-session-marquee.mjs`, `scripts/test-day-rollover.mjs`, `scripts/test-cloudrun-desk-fields.mjs`. Hosted Firebase was **v2.46.1** until `deploy now`. |
| 2026-10-08 | **Deployed v2.46.1** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`; OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **14:24 PT** (Eric, 14:23 PT: "Deploy the Frontend to Firebase"). Live channel last release 2026-10-08 14:24:15 PT. Verified: chip `v2.46.1`; `app.js?v=2.46.1`, `desk-config.js?v=2.46.1`, `styles.css?v=2.46.1`; hosted `index.html`, `app.js`, `js/snapshot.mjs` (VERSION `2.46.1`), `desk-config.js`, `js/sfx.mjs`, `styles.css` byte-identical to local. Hosted `desk-config.js` GCS URLs are only `head.json` / `snapshot.json` / `actions.json` / `discovery.json` (Cloud Run); `sizing.json` / `mock_options_history.json` appear only in comments, and the `ledgerUrl` path cannot run because the config does not set it. `/sounds/**` Range returns **206**. Mac sidecar left off. RESEARCH Cloud Run `YOUR_SERVICE_NAME-00593-mkh` (desk schema 2, VERSION 2.47.0) went live 14:18 PT, but the session had closed, so the public objects were still the pre-deploy 2.44.0 writes (snapshot / head 12:22 PT, discovery 12:30 PT, no `rejects` / `sizing`). Page logic on that live snapshot: all-time **53W/90L 37.1%**, avg **+$298.56 / -$182.18**, best/worst **+$3,036.30 / -$746.20**, PF **0.97** (`account.history`, 143 exits; Cloud Run's own count 14W/22L is 2-day). Rejects **—**. Deployed / cap **—** in a fresh browser. A browser holding the last real sizing (10-07 17:43 PT) shows **$569.21** (sum cap off, 90%, 7 slots), dimmed after 17:43 PT 10-08. Rejects, sizing and the curve lifetime arrive with Cloud Run's next snapshot / discovery write. `test-cloudrun-desk-fields` (schema-2 fixture) 32 checks ok; `test-hosted-lifetime` 22 checks ok. Hosted now matches local. |
| 2026-10-08 | Local **v2.46.1**: **Hosted desk reads nothing the Mac writes.** Eric (Thu 08:52 PT): the only components are Cloud Run, GCS and the Firebase Frontend; the desk never depends on the Mac. Page: (1) no `sizingUrl` / `ledgerUrl` in `desk-config.js`. `desk_public/sizing.json` and the slim `desk_public/mock_options_history.json` were Mac-written (stale since 10-07 17:43 PT) and are no longer read. The hosted source is Cloud Run `snapshot.json` only, and the sidecar `/api/state` is probed only on its own http origin, never from the https site. (2) **Rejects**: Cloud Run's `rejects` block `{schema, ymd, as_of, count, by_stage, by_reason, source}` is read from `snapshot.json` (top level or `metrics.rejects` + `rejects_breakdown`) and from `discovery.json` on every rotate. Only today's PT block counts, a newer `as_of` wins, and a missing block shows —. (3) **Deployed / cap**: from Cloud Run's `snapshot.sizing` (`kind: research_sizing`); the 600 stays ignored. With no block, the last real one this browser saw is kept; one older than 24 h is shown dimmed with its as-of time in PT. (4) **All-time**: Cloud Run's `lifetime_*` wins only when its `lifetime_trades` ≥ the page's `account.history` count. History is refused when flagged thinned (`history_complete:false` or `history_total_points` ≠ length) or when an unflagged copy has fewer exits than this browser already saw for the same seed. Correction: Cloud Run 2.44.0 thins `account.history` above **500 points** with a fixed stride (`desk_snapshot_pulse.py` L867–875; any thinned copy ≥ 251 points). It is 137 today, so the cap is ~21 sessions away. Cloud Run asks (RESEARCH, routed to its deployer): `/tmp/cloudrun_desk_spec.md`, snapshot schema 2 = rejects block (`modules/desk_rejects.py`, union in marker `desk_rejects`, also in discovery.json, reset on `reset_daily()`), `sizing` from `unified_0dte_account` resolvers replacing `DEPLOY_CAP = 600`, lifetime from equity_curve exits since the latest seed (+`lifetime_trades` / `lifetime_source`), history with `delta` / `kind`, no stride, `history_complete` / `history_total_points`, optional `desk_history/<mode>.json` + labelled exits + no silent backfill. Files: `public/js/snapshot.mjs`, `public/app.js`, `public/desk-config.js`, `public/styles.css`, `scripts/test-cloudrun-desk-fields.mjs` (new, 32 checks, in `npm test`, fixture `scripts/fixtures/cloudrun-snapshot-schema2.json`). Hosted Firebase is **v2.46.0** until `deploy now`. |
| 2026-10-08 | **Deployed v2.46.0** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`; OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **08:19 PT** (Eric, 08:18 PT: "Deploy Frontend to Firebase"). Live channel last release 2026-10-08 08:19:28 PT, 415 files. Verified: chip `v2.46.0`; `app.js?v=2.46.0`, `desk-config.js?v=2.46.0`, `styles.css?v=2.46.0`; hosted `index.html`, `app.js`, `js/snapshot.mjs`, `js/sfx.mjs`, `desk-config.js` byte-identical to local; `snapshot.mjs` VERSION `2.46.0` (200, `text/javascript`); `/sounds/**` Range returns **206** (`immutable` cache). Mac sidecar left off. `LIVE=1 node scripts/test-hosted-lifetime.mjs` against the live Cloud Run snapshot (v2.44.0, written 08:04:57 PT, 137 history points, published 10W/20L): the hosted page shows all-time **49W/86L 36.3%**, avg **+$311.87 / -$183.14**, best/worst **+$3,036.30 / -$746.20**, PF **0.97** (source `account.history`, 135 trades). Hosted now matches local. |
| 2026-10-08 | Local **v2.46.0**: **Hosted Account details show true all-time stats with no Mac.** Bug (Eric, Thu 08:08 PT, on https://easy-0dte.web.app): Demo Account details showed **33.3% · 10W/20L** instead of the all-time record, and the chart looked shorter. Cause: the Mac sidecar was off from ~17:45 PT 10/7, so the only writer of `desk_public/snapshot.json` was RESEARCH Cloud Run `desk_snapshot_pulse.py` (v2.44.0, last write 08:04:57 PT). It takes `lifetime_*` from `closed_positions`, which RESEARCH prunes to 2 days (30 rows). Its `account.history` is still the whole booked curve (seed $5,000 at 09-28 18:25 PT + 135 exits, uncapped, 137 points / 8.4 KB). Fix (page only, Cloud Run data only): `exitPnlsFromAccountHistory` takes each exit P&L from consecutive history points. It starts at the latest seed (the epoch), skips seed / deposit / withdraw / adjust and flat points, and refuses unlabelled history when withdrawals exist (never guesses). `resolveLifetimeStats` uses it whenever it has more trades than the published count and returns `lifetime_trades` / `lifetime_source`. Live check with the sidecar off (08:14:46 PT): **49W/86L 36.3%**, avg **+$311.87 / -$183.14**, best/worst **+$3,036.30 / -$746.20**, PF **0.97**, trade for trade equal to the ledger `equity_curve`. Chart: the data was never short (137 points, every session since 09-29). `downsampleEquityHistory` keeps every print for the last 10 sessions and the real low / high / close of each older session, so the chart stays small for months without inventing a value. Files: `public/js/snapshot.mjs`, `public/app.js`, `scripts/test-hosted-lifetime.mjs` (new, in `npm test`, fixtures `scripts/fixtures/cloudrun-snapshot-2026-10-08.json` + `ledger-curve-2026-10-08.json`, `LIVE=1` also checks the live snapshot), `scripts/test-lifetime-account.mjs`. Sidecar not restarted. Hosted Firebase is **v2.45.9** until `deploy now`. |
| 2026-10-06 | **Deployed v2.45.9** to Firebase Hosting site `easy-0dte` (https://easy-0dte.web.app, project `YOUR_FIREBASE_PROJECT`; OAuth site untouched) via `DEPLOY_NOW=YES ./scripts/deploy_desk_hosting.sh` at **07:26 PT** (Eric: "Deploy the Frontend to Firebase"). Live channel last release 2026-10-06 07:26:32 PT, 415 files. Verified: chip `v2.45.9`; `app.js?v=2.45.9`, `desk-config.js?v=2.45.9`, `styles.css?v=2.45.9`; hosted `app.js`, `js/discovery-source.mjs` and `js/discovery-ticker.mjs` byte-identical to local (200, `text/javascript`); `snapshot.mjs` VERSION `2.45.9`; `/sounds/**` Range returns **206** (`immutable` cache). The discovery strip fixes (newest scan only, no restart on unchanged chips, last real price with time, color legend) and the v2.45.8 ORB rows are live. Hosted now matches local. |
| 2026-10-06 | Local sidecar **v2.45.9**: **Discovery strip has one source of truth and stops flipping between priced, unpriced and frozen chips.** Bug (Eric, Tue 07:01 PT): the hosted strip sometimes showed bare symbols, sometimes green, sometimes grey prices. Cause (polled 1s, 07:04–07:06 PT): two writers on `desk_public/discovery.json`. Cloud Run (RESEARCH `desk_discovery_pulse`) wrote fresh pulses (seq 19 / 22 / 23 / 24, the 24-name WAIT_BREAK rotate, all priced, grey), and the sidecar overwrote each within 2–4s with a **frozen** copy (seq 13 @ 06:58:55 PT: 7 lime READY TL names MU / SNOW / SNDK / WDC / SOFI / RKLB / RGTI with **no price**, because RESEARCH only quotes window names, plus 24 frozen grey prices). The sidecar copy froze because `pulseTick` shared the main sync loop's `busy` flag; the ~2s main tick on a 2s timer starved it, so the actions copy, discovery marker and public scan stopped at 06:59:50 PT while the sidecar kept republishing the old copy. Colors are scan state, not quote age: lime = READY, blue = CONFIRM, grey = WAITING_FOR_BREAK. Fix: (1) **single writer**: the sidecar uploads `discovery.json` only when its copy is strictly newer (updated_at, then seq) than the GCS object (`publishDiscoveryIfNewer`, `shouldUploadDiscovery`). (2) `pulseTick` has its own in-flight flag (a run stuck > 30s no longer blocks), and publishes are serialized. During market hours a scan older than **2 min** is never published, in `discovery.json` or in `snapshot.json`/`/api/state`: it is omitted and marked `discovery.scan_stale`. READY/CONFIRM rows that RESEARCH's newer `watch_fsm` no longer holds are dropped (`dropReadyOffWatchList`). An older scan never replaces a newer local copy (`writeDiscoveryScan`). The sidecar's rotate-log fallback (`sidecar.discovery_rotate`, stamped with the build time plus Yahoo lasts) only stands in while RESEARCH's pulse is missing or older than 2 min. Once unstarved, it had become a second writer again (07:18–07:19 PT). (3) The page shows the newer of `snapshot.discovery` and `discovery.json` by `scan_as_of`/seq, never steps back to an older scan (`pickDiscovery`, `pulseBeatsDiscovery`), and restarts the loop only when the rendered chips change. (4) A chip with no price shows the **last real price** seen for that symbol today in a scan row, with its PT time (`SOFI·$27.10@07:07`), dimmed after 2 min. Nothing is invented: never seen = bare symbol, and nothing carries over from yesterday (`localStorage desk_disc_px`). The strip and label tooltips carry the color legend. Files: `public/js/discovery-source.mjs` (new), `public/js/discovery-ticker.mjs`, `public/app.js`, `public/styles.css`, `src/ledger-sync.mjs`, `src/desk-engine.mjs`, `scripts/test-discovery-source.mjs` (new, in `npm test`). Sidecar fixes reach hosted through the published files; the page fixes need deploy. Hosted Firebase is **v2.45.7** until `deploy now`. |
| 2026-10-06 | Local sidecar **v2.45.8**: **Hosted Event tape shows ORB Capture 1 / 2 as soon as they land.** Bug (Tue 06:47 PT): Telegram sent ORB Capture 1 (133 captured, 6.07s) at 06:45, and local `/api/state` had the `session-orb1-capture-2026-10-06` row plus `orb_capture`. But the published `desk_public/snapshot.json` (sidecar, 13:47:13Z) had **no `orb_capture`** and a tape top of `session-open-2026-10-06`, and `head.json` `tape_top_id` was `session-open`, so hosted (which builds session rows from `orb_capture`) never added ORB1. Cause: `src/ledger-sync.mjs` `publishPublicDemoFeeds` built the snapshot with `buildSnapshotFromLedger` + discovery + rejects only, skipping the `attachOrbCapture(…, loadScanContext().capture)` step that `desk-engine` applies to `/api/state`. The republish trigger `sessionBellSig` also ignored capture changes. The v2.45.3 guard and v2.45.6 rollover don't drop session rows. Fix: `withPublishedSessionState` attaches the scan-context capture (keeping an existing same-day one) and merges the same ORB session rows. `head.json` is built from that snapshot (`publicHeadFor`), so `tape_top_id` moves to ORB1/ORB2 and the hosted head poll refetches. `sessionBellSig` now includes the capture, so an ORB capture alone forces a republish. Hosted `guardDeskSnapshot` carries a same-day `orb_capture` forward when an incoming snapshot lacks it (`carryOrbCapture`). New `scripts/test-orb-publish.mjs` (23 checks). Not deployed; the publish fix alone restores ORB1 on the hosted v2.45.7 client. Hosted Firebase is **v2.45.7** until `deploy now`. |
| 2026-10-05 | Local sidecar **v2.45.7**: **Account details Hit rate / Avg Win / Avg Loss / Best / Worst / Profit Factor are all-time again.** Cause: RESEARCH prunes `closed_positions` to ~2 PT days (`MOCK_OPTIONS_LEDGER_MAX_AGE_DAYS`, capped at 2), so on Monday the ledger only kept today's closes. The desk computed "lifetime" from that truncated list (and hosted hydrate recomputed from the Event tape), so Hit rate showed today's 8W/18L as all-time. Total P&L stayed correct because it uses `booked_equity − seed`. `equity_curve` still holds every exit from account open (96 exits → +$2,220.90). Fix (`resolveLifetimeStats` in `public/js/snapshot.mjs`): prefer `equity_curve` exit deltas when longer than remaining closes; publish those fields on the snapshot; hydrate keeps a fuller published life and never lets a short tape overwrite it. Day W/L and Day P&L stay on the EOD / day panel from `daily_stats`. New `scripts/test-lifetime-account.mjs`. Hosted Firebase is **v2.45.7** until `deploy now`. |
| 2026-10-02 | Local sidecar **v2.45.6**: **Day P&L rolls to the current PT trading day.** Bug: at 06:35 PT Fri the Day P&L KPI still showed Thu's "-$1,109.00 / -19.40% Today". Cause: RESEARCH resets `daily_stats` only on the first main-loop pass after the market opens (`modules/prime_trading_system.py`: `_is_market_open()` gate → `NEW TRADING DAY` → `mock_ex.reset_daily()`; the live GCS ledger was rewritten **06:30:05 PT** with `stats_trading_date 2026-10-02` and zeroed stats). Its `_save` stamps `stats_trading_date` with the PT calendar date on every save, even before that reset. The Cloud Run `desk_snapshot_pulse.py` `snapshot.json` (executor memory) was last written 19:24 PT Thu with `stats_trading_date 2026-10-01` and `day_pnl -1109`. The desk showed `account.day_pnl` verbatim as "Today" regardless of its date, and the local sidecar was down, so nothing newer was published. Fix (`public/js/snapshot.mjs` `dayScope` / `withDayRollover`, run inside `withDeskDerived`, so it covers sidecar `/api/state`, the published snapshot and hosted hydrate): on an NYSE session day (`isSessionBellYmd`), from **00:00 PT**, day fields whose `stats_trading_date` is before today, or that are stamped today but carry non-zero stats while the tape has closes and none dated today, show **$0.00 / 0.00% Today**, or today's Event-tape closes if any. This covers the KPI, the Balance-history day hint, Account details, the EOD panel and day W/L. Rejects not dated today show —. On weekends and holidays the last session's Day P&L stays, labeled with its date (e.g. `Oct 1`). Unknown day values stay —. Today's `daily_stats` take over automatically. `guardDeskSnapshot` rejects a snapshot from an older `stats_trading_date` (`older_day`), so yesterday's pulse can't repaint over today. New `scripts/test-day-rollover.mjs` (41 checks). `test-day-kpi` / `test-rejects` now pin their fixture day. Hosted Firebase is **v2.45.5** until `deploy now`. |
| 2026-10-01 | Local sidecar **v2.45.5**: **Background music is a shuffled playlist (shuffle bag), not a random pick per click.** New pure `public/js/music-shuffle.mjs` (`createBag` Fisher-Yates, `nextFromBag`, `markStarted`, `skipInBag`, `reconcileBag`, `parseBag`, `bagPosition`; injectable rng), wired in `public/js/sfx.mjs` (replaces `nextMusicRel`, kept only for its test). The first visit builds a random order of every `manifest.music` track, persisted as `localStorage.desk_music_bag` (`{v, order, index, current}`), so reloads and revisits continue the cycle. The current track **loops** (`loop = true`, including after the iPhone blob upgrade); there is no `ended` → next. Only **Music off → on** advances to the next track of the order. A page load with Music already on also plays the next track. When every track has started once, a new order is shuffled whose first track is never the one that just played (unless there's only 1). A track is counted once when it first starts (`playing` / a resolved `play()`), never on loops or resumes, so plays stay equal. Interruptions (phone lock, 6:30–6:45 PT waiting window, ORB hold, `play()` retry, blob swap, ledger reload) resume the loaded track in place (`resumeMusicClip`). A media `error` skips that track for the cycle, at most 3 in a row. Manifest changes: new tracks are inserted at random in the unplayed remainder, removed tracks are dropped, a corrupt or empty store rebuilds. The Music chip tooltip shows `Track k of N: name`. The v2.45.4 `musicBlocked` / 8s no-restart and entry-replay fixes are unchanged. New `scripts/test-music-bag.mjs` (72 checks, in `npm test`). Hosted Firebase is **v2.45.4** until `deploy now`. |
| 2026-10-01 | Local sidecar **v2.45.4**: **Entry SFX no longer replays on every click while music loads.** Cause (`public/js/sfx.mjs`): the entry cue played through Web Audio (`playViaContext`), which left it in `pending` and never marked it played. Background music shared the SFX `playBlocked` flag: `startMusicClip` / `upgradeMusicToBlob` → `playElementNow(musicEl)` set `playBlocked = true` whenever music `play()` rejected while loading (AbortError from the src / blob swap, or NotAllowedError). Every click (`pointerdown` and `pointerup`, each) → `onPageGesture` → `kickCurrentCue` → `playMusic()` (restarting the music load, which rejected again) → `playBlocked` still true → `playRel(pending.rel)` replayed the entry. It stopped only once music actually played (then `playBlocked` went false). The new harness reproduced it: 6 clicks → 11 entry plays. Fix: music `play()` uses its own `playMusicElementNow` (`musicBlocked`, `musicStartAt`) and never touches the SFX state; a music `play()` still in flight (≤8s) is not re-called by clicks or polls; `pending` records `at` / `played` / `retried`. A gesture retries only a cue that never played, a oneshot at most once and only within **10s** of its first attempt; only trusted events count. Music still starts on the first gesture. `trade-cues.mjs` / `cueQueue` were not the cause (seen ids only grow; each cue is shifted once). New `scripts/test-sfx-gesture-replay.mjs` (real `sfx.mjs` on a fake DOM + Web Audio). Hosted Firebase is **v2.45.1** until `deploy now`. |
| 2026-10-01 | Local sidecar **v2.45.3**: **Event tape bounce fixed** (a refresh plays the sound and shows a new row, then the desk snaps back and the row returns on a later refresh). Cause: hosted `desk_public/snapshot.json` + `head.json` have **two writers**, Cloud Run `desk_snapshot_pulse.py` (executor memory at fill/close, v2.44.0) and the sidecar (every ~6-10s from its copy of `mock_options_history.json`, which RESEARCH rewrites ~every 9s). Seen 07:13:49-07:13:57 PT on the `QQQ-739p-681` exit: sidecar snapshot (open 4, no exit) → Cloud Run snapshot (open 3, exit) → sidecar again 7s later; `head.json` still described the sidecar copy while `snapshot.json` held Cloud Run's. Whenever the sidecar's ledger copy predated a fill, its snapshot replaced the newer one, and its `ledger.mtime` (publish time) made the client strip the `actions.json` row as already-in-ledger. Overlapping 1.5s polls on a slow phone could also apply an older response last. Not a cache issue: all desk_public JSONs are `public, max-age=0, no-cache`, fetched `no-store` + `?v=`. Fix: one hosted poll at a time; `guardDeskSnapshot` ignores a snapshot that lacks a shown entry/exit row and knows nothing newer, or has an older content time `as_of`, for up to 3 min, after which the source wins but newer shown rows carry forward (≤15 min, source `desk.carry`); a failed or blank fetch no longer wipes the desk; the reject count carries over a Cloud Run snapshot that has none; the sidecar stamps `ledger.mtime` / `as_of` (and `head.as_of`) with the ledger's GCS `updated` time. Trade SFX ids only grow (`seenTapeIds`, same ids in actions / Cloud Run / sidecar), so a reappearing row stays silent. New `scripts/test-stale-snapshot.mjs`. Hosted Firebase is **v2.45.1** until `deploy now`. |
| 2026-10-01 | Local sidecar **v2.45.2**: EOD / day PnL **rejects** now shows today's real trade-signal reject count instead of **—**. Source (read-only): the RESEARCH daily marker `research_app/daily_markers/{PT date}.json` that the sidecar already pulls for Discovery; `trendline_geometry.candidates` whose `state_reason` (else `setup_payload.attempt_consumed_reason`) starts `trade_profile_reject:` (stage trade_profile), `exec_reject:` (execution), `quality_reject:` or a `*_filter_reject:`, the same codes behind each **Result: Rejected** Telegram. Counted once per PT date + trade_id (`BP:bear:trendline0`), unioned in `data/desk_rejects.json`, carried as `metrics.rejects` + `metrics.rejects_breakdown` in `/api/state` and `desk_public/snapshot.json`; hover shows stage / reason counts. Today it read **65** at 06:54 PT (trade_profile 53 · execution 12) and **117** at 07:02 PT (trade_profile 94 · execution 23). ORB / Breakout rejects are not persisted by RESEARCH (Telegram only), so they are not counted; no marker for today → **—**. New `src/research-rejects.mjs` + `scripts/test-rejects.mjs`. Hosted Firebase still **v2.44.95** until `deploy now`. |
| 2026-09-30 | Local sidecar **v2.45.1**: **Music cut off on iPhone** (~60s then restart) fixed. Cause: `firebase.json` catch-all `**` `Cache-Control: no-cache` came after `/sounds/**` and overrode it, so hosted audio was never CDN-cached and Range requests got `200` (full body) instead of `206`; iPhone Safari streams `<audio>` by ranges, played the first chunk, then looped. Fix: `**` header rule moved first so `/sounds/**` `public, max-age=31536000, immutable` wins (clip URLs already carry `?v=` busting), and `sfx.mjs` now downloads the current music track in full in the background and swaps `#sfx-music` to a blob URL at the same position. Hosted Firebase still **v2.44.95** until `deploy now`. |
| 2026-09-30 | Local sidecar **v2.45.0**: **Deployed / cap** (Account details + Open Positions `util` header) now shows the **per-position cap**, not the all-slots total: Net equity × `0DTE_TRADING_CAPITAL_PCT` ÷ `0DTE_SLOT_COUNT` ($5,717.70 × 90% = $5,145.93 ÷ 7 = **$735.13**, shown with cents); sum cap on → min(that, `0DTE_MAX_POSITION_SIZE_SUM`). `deploy_cap` = `deploy_slot_cap`; hover explains the formula. Hosted Firebase still **v2.44.95** until `deploy now`. |
| 2026-09-30 | Local sidecar **v2.44.109**: **Deployed / cap** follows the RESEARCH position-sum cap instead of a hardcoded `$600`. Sidecar reads `0DTE_MAX_POSITION_SIZE_SUM` / `0DTE_TRADING_CAPITAL_PCT` / `0DTE_SLOT_COUNT` from Cloud Run `YOUR_SERVICE_NAME` env (read-only `gcloud run services describe`, every 2 min) + RESEARCH `configs/*.env`, embeds `sizing` in the snapshot and publishes `desk_public/sizing.json`. Sum cap **off** (today, `0`) → cap = Net equity × 90% total deployable (`$0 / $5,146`; per slot ÷ 7 = `$735.13` in the snapshot + hover); **on** → the sum cap `$`; unknown → **—**. Same in the Open Positions header (`util 0.00% · $0 / $5,146`). Cloud Run pulse `deploy_cap: 600` ignored; hosted keeps the last known sizing. `DESK_DEPLOY_CAP` / `deployCap` retired. Hosted Firebase still **v2.44.95** until `deploy now`. |
