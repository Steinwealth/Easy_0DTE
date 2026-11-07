# Deploying the Easy ORB / 0DTE Trading Service

**Public document.** Copy-paste project IDs, image URLs, service-account emails, and Cloud Run URLs live in [CloudSecrets.md](CloudSecrets.md). Credentials and account IDs live in [PrivateSecrets.md](PrivateSecrets.md) or `secretsprivate/`.

**Service:** RESEARCH Cloud Run (`easy-etrade-strategy` — **ORB ETF + ORB 0DTE + Trendline 0DTE** shared runtime)  
**Serving today:** `easy-etrade-strategy-00625-wwv` (Demo, 100% traffic). Sequential OCC inner batches of 25 + UL lane yield; no 4s/30s UL cancel; OCC apply-on-complete; eval JSONL engine/qty/fee keys; `0DTE_OPEN_EXACT_QUOTE_BATCH_SIZE=25`.  
**Rollback of this revision:** `easy-etrade-strategy-00624-8bn`.  
**Path (project root):** `0. Strategies and Automations/1. The Easy ORB Strategy RESEARCH APP`

This is a **quick operator checklist** for deploying code changes for the trading service to Cloud Run. For full cloud details see [Cloud.md](Cloud.md) (placeholders) and [CloudSecrets.md](CloudSecrets.md) (this project).

**Keep uploads lean:** Read **[§2 Lean Cloud Build source](#2-lean-cloud-build-source-upload-mandatory-before-each-deploy)** before every `gcloud builds submit` so future deploys stay fast and under the size budget.

---

## 0. Deployment policy (strict)

- **No auto-deploy:** code/doc updates do not imply deployment.
- Deploy only on an explicit instruction: **"deploy now"**.
- During active market session, do not deploy unless emergency rollout is explicitly approved.
- Deploy scripts are gated and require explicit confirmation variable:
  - `DEPLOY_NOW=YES`

---

## 1. Pre-deploy checklist

- **Local path:** You are in the RESEARCH APP root:
  - `cd "0. Strategies and Automations/1. The Easy ORB Strategy RESEARCH APP"`
- **gcloud configured to correct project:**
  - `gcloud config set project YOUR_PROJECT_ID` (see [CloudSecrets.md](CloudSecrets.md))
- **Auth:** You are logged in and allowed to deploy:
  - Permission to act as the trading service account (email in [CloudSecrets.md](CloudSecrets.md))
  - See [CloudSecrets.md](CloudSecrets.md) → *Service Names and URLs* if you need to re-check roles.
- **Code state:**
  - All desired fixes are committed (optional but recommended).
  - `BUILD_ID.txt` updated if you want a new build marker layer.

---

## 2. Lean Cloud Build source upload (mandatory before each deploy)

Cloud Build uploads a **source tarball** from the ORB Strategy directory before building the image. Size and contents are controlled by **ignore files**, not by “whatever is on disk.”

### 2.1 Two different ignore files

| File | Purpose |
|------|---------|
| **`.gcloudignore`** | Lists paths **not** sent to Google Cloud Build with `gcloud builds submit`. **This determines upload size and speed.** |
| **`.dockerignore`** | Lists paths **hidden from `docker build`** when Cloud Build runs the `Dockerfile`. Usually aligned with `.gcloudignore` for anything that should never enter an image. |

If you add new runtime code or data, ensure **neither** file accidentally excludes it (watch broad patterns like `*.json` — exceptions exist for holidays and watchlist).

### 2.2 Size budget and verification

**Target:** keep the **uncompressed** uploaded source set **under 11 MiB** (runtime only). Last SUCCESS `00620-btt` was 357 files / 20.5 MiB because `training/` and dated `*.bak1027_*` copies leaked — `*.bak_*` does not match `.bak1027_atrvwap`. Those are now ignored.

From the ORB Strategy root, **before** `gcloud builds submit`:

```bash
cd "0. Strategies and Automations/1. The Easy ORB Strategy RESEARCH APP"

gcloud meta list-files-for-upload . | wc -l
gcloud meta list-files-for-upload . | xargs stat -f%z 2>/dev/null | awk '{s+=$1} END {printf "bytes=%d (%.3f MiB)\n", s, s/1024/1024}'
```

With current ignores you should see on the order of **~320 files** and **about 10 MiB uncompressed**. If the byte total jumps, inspect what became eligible (new folders under the repo root, accidental removal of an ignore rule, or local junk — see below).

### 2.3 Never upload / never commit

- **`.gcloud_tmp/`** — If this directory exists **inside** the repo folder, it is **local gcloud state** (logs, SQLite DBs, credential-related caches). It must **not** be uploaded or committed. It is listed in `.gitignore`; delete locally if present: `rm -rf .gcloud_tmp` (gcloud will recreate config under your user profile as needed).

### 2.4 What stays out of the tarball on purpose

Examples (see `.gcloudignore` for the authoritative list):

- **`docs/`**, `**/*.md`**, session notes, **`easyCollector/`** (separate service), tests, deploy helper scripts (except the two cleanup shells copied into the image).
- **`training/`** — Desk Training Calendar source (`trade_profile_replay_book.csv`, `model_training_log.json`, `README.md`). Stays on the Mac. Cloud Run never reads it. The calendar JSONs that the browser loads are Desk `public/training/`, published only by Firebase (`deploy_desk_hosting.sh`).
- **Dated `*.bak*` copies** (`.bak1027_atrvwap`, `.bak1103_N3`, …). Local snapshots next to live files. `*.bak_*` does not match those names; `.gcloudignore` also lists `*.bak*`.
- **Legacy / unused Python:** `modules/symbol_score_integration.py` (no production imports — verified periodically; keeps the bundle lean).
- **Duplicate dependency list:** `easy0DTE/requirements.txt` — the image installs **`requirements.txt`** at the repo root only.
- **Optional watchlist JSON:** `data/watchlist/sentiment_pairs_mapping.json` — not referenced by Python; **`complete_sentiment_mapping.json`** and **`orb_inverse_mapping.json`** are used. Re-include this file in uploads only after adding runtime code that reads it (and trim elsewhere if you need to stay under the size budget).

### 2.5 What must upload for all three paths (ORB, ORB 0DTE, Trendline 0DTE)

Everything the **`Dockerfile`** copies or that those trees import at runtime, including:

- `Dockerfile`, `.dockerignore`, `main.py`, `cloud_run_entry.py`, root **`requirements.txt`**, `BUILD_ID.txt`
- **`modules/`** (production Python for ORB core, stealth, alerts, risk, etc.)
- **`configs/*.env`** — merged app configuration (`Data`, `Shared`, `ORBSO`, `ORB0DTE`, `Trendline0DTE`, `Risk`, `Alerts`)
- **`data/watchlist/`** — `core_list.csv`, `0dte_list.csv`, mapping JSON the code loads, **`data/holidays_*.json`**
- **`easy0DTE/`** — package + `modules/` + `configs/0dte.env` (no standalone `requirements.txt` in image)
- **`easyTrendline/`** — full Python package for Trendline 0DTE
- **`scripts/cleanup_old_images.sh`**, **`scripts/cleanup_old_revisions.sh`** — copied into the image for `/api/cleanup/images`

When you add a **new** top-level package or data directory: update the **`Dockerfile`** `COPY` lines if it is not already under an existing copy, and add **no** broad ignore rules that would drop it.

---

## 3. Build the container image

From the ORB Strategy root:

```bash
cd "0. Strategies and Automations/1. The Easy ORB Strategy RESEARCH APP"
gcloud config set project YOUR_PROJECT_ID   # CloudSecrets.md

gcloud builds submit \
  --tag gcr.io/YOUR_PROJECT_ID/YOUR_SERVICE_NAME:latest .
```

Notes:
- This uploads the current directory **subject to `.gcloudignore`** as build context and runs the `Dockerfile`.
- The resulting image is pushed as `gcr.io/YOUR_PROJECT_ID/YOUR_SERVICE_NAME:latest` (see [CloudSecrets.md](CloudSecrets.md)).
- **Image contents (three trading paths):** `modules/` + `main.py` (ORB ETF/core), **`easy0DTE/`** (ORB 0DTE options), **`easyTrendline/`** (Trendline 0DTE). The Dockerfile **RUN** step fails the build if checked entrypoints for those paths are missing.

---

## 4. Deploy to Cloud Run

After a successful build, deploy the new image to the trading service:

```bash
gcloud run deploy YOUR_SERVICE_NAME \
  --image gcr.io/YOUR_PROJECT_ID/YOUR_SERVICE_NAME:latest \
  --platform managed --region YOUR_REGION \
  --memory 2Gi --cpu 2 --max-instances 1 --min-instances 0 \
  --concurrency 80 --timeout 3600 --no-cpu-throttling \
  --service-account YOUR_TRADING_SERVICE_ACCOUNT \
  --set-env-vars="ENVIRONMENT=production,MODE=demo,BROKER=etrade,CLOUD_MODE=true,ENABLE_0DTE_STRATEGY=true,ENABLE_TRENDLINE_STRATEGY=true,LOG_LEVEL=INFO" \
  --allow-unauthenticated
```

Prefer **`DEPLOY_NOW=YES ./scripts/deploy_demo_service.sh`** (or `deploy_live_service.sh`) over hand-built `gcloud` so env stays in sync. Copy-paste values: [CloudSecrets.md](CloudSecrets.md).

Key points:
- **Project / region / service account:** [CloudSecrets.md](CloudSecrets.md)
- **Env vars:** match the production trading configuration (`MODE=demo` unless Live cutover).

---

## 5. Post-deploy health checks

After `gcloud run deploy` reports success:

1. **Check service URL** — [CloudSecrets.md](CloudSecrets.md) (do not paste the live URL into public docs).
2. **Health endpoint (optional):**
   - `GET /health` on the service URL should return OK.
3. **Logs:**
   - Use Cloud Logging to confirm the new revision is running and pipeline steps are firing:
     - `PIPELINE | STEP 1 ORB CAPTURE`
     - `PIPELINE | STEP 4 SIGNAL COLLECTION`
     - `PIPELINE | STEP 5 TRADE EXECUTION`
4. **Scheduler jobs:** (only if changed or after a long pause)
   - Validation/Prefetch jobs and trading jobs should still point to the correct service URL.
   - Reference [CloudSecrets.md](CloudSecrets.md) → *Cloud Scheduler Jobs (Project-Specific)* for commands.

---

## 6. Safe deploy habits

- Prefer **end-of-session deploys** to avoid changing behavior mid-trading-day.
- If a deploy contains non-trivial trading logic changes:
  - Verify Signal Collection and alerts on the next open before enabling live accounts.
  - Keep Cloud Logging open during 6:30–7:45 AM PT to confirm ORB/0DTE flows.
- Use cleanup scripts periodically to control image/revision sprawl:
  - See [CloudSecrets.md](CloudSecrets.md) → *Cloud Cleanup* for `cleanup_old_images.sh` and `cleanup_old_revisions.sh`.

---

*Short version: verify **[§2](#2-lean-cloud-build-source-upload-mandatory-before-each-deploy)** upload size and file list, then from the ORB Strategy root run `gcloud builds submit`, then `gcloud run deploy`, and verify the trading service URL and logs in GCP.* 

