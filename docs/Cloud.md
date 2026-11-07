# Cloud Deployment Guide
## Easy 0DTE Strategy — RESEARCH APP (public cloud)

**Last Updated:** August 31, 2026  
**Subject:** Deploy, scale, schedule, and operate the **RESEARCH** Cloud Run service.  
**Not this file:** Strategy → [0DTEORB1Breakout.md](0DTEORB1Breakout.md) · [0DTETrendline.md](0DTETrendline.md) · [0DTEORB2Breakout.md](0DTEORB2Breakout.md) · Risk → [0DTERisk.md](0DTERisk.md) · Env → [Configs.md](Configs.md).

**Public document:** Placeholders only — no project IDs, URLs, revisions, or credentials.  
**Operators:** [CloudSecrets.md](CloudSecrets.md) · Credentials → [PrivateSecrets.md](PrivateSecrets.md) · OAuth → [OAuth.md](OAuth.md) / [OAuthSecrets.md](OAuthSecrets.md).

### Path matrix (RESEARCH)

| Mode | Paths |
|------|--------|
| **Demo (watch default)** | ORB1 0DTE + Trendline + Breakout (`ENABLE_ORB_STRATEGY=false` · SC / validation / PP **paused**) |
| **Live** | Same three 0DTE lanes (**SO ETF blocked**) |
| **Rollback** | Re-enable SO ETF + SC/PP + `ORB_0DTE_DISCOVERY_MODE=batch` |

**Liveness vs keepalive:** Cloud Run **does not support TCP liveness** (TCP is startup-only). Deploy scripts pass **`--liveness-probe=""`** so HTTP `/health` is **not** a liveness probe. `/health` shares the trading asyncio loop — busy discovery used to starve it and Cloud Run killed the instance (Aug 24–26). Scheduler keep-alives may still **HTTP** `/api/health` as a wake ping; that is not liveness. Mock-ledger GCS I/O runs **off-loop**. RESEARCH Demo **`00515-m7w`** (Aug 26) has no HTTP liveness.

**Deploy Demo:** `DEPLOY_NOW=YES ./scripts/deploy_demo_service.sh` → `easy-etrade-strategy` · `MODE=demo` · `PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false` · 0DTE / Trendline / Breakout `ENABLE_*=true` · SO/SC/validation **false**.

---

## Table of contents

1. [Overview](#overview)
2. [Cloud Architecture](#cloud-architecture)
3. [Supported Cloud Platforms](#supported-cloud-platforms)
4. [Google Cloud Platform (GCP) - Primary Platform](#google-cloud-platform-gcp---primary-platform)
5. [Alternative Cloud Platforms](#alternative-cloud-platforms)
6. [Scale-to-Zero Configuration](#scale-to-zero-configuration)
7. [Cloud Scheduler Jobs](#cloud-scheduler-jobs)
8. [Cloud Cleanup Policies](#cloud-cleanup-policies)
9. [Cloud Hygiene & Maintenance Mode](#cloud-hygiene--maintenance-mode-june-2026)
10. [Cloud Deployment Optimization Strategy](#cloud-deployment-optimization-strategy)
11. [Cost Analysis](#cost-analysis)
12. [Deployment Steps](#deployment-steps)
13. [Monitoring & Logging](#monitoring--logging)
14. [Security Configuration](#security-configuration)
15. [Data Persistence](#data-persistence)
16. [Production Readiness](#production-readiness)

---

## Overview

Easy 0DTE Strategy — RESEARCH cloud stack:

| Service | Role |
|---------|------|
| **Trading (Cloud Run)** | Morning ORB pipeline + path matrix above |
| **OAuth (Cloud Run)** | E*TRADE token renewal + keep-alive (shared with FINAL) |
| **Easy Collector** | Optional / separate — not required for trading |

Shared: GCP project · Telegram · unified 0DTE monitor (~2s) · OAuth · GCS `research_app/*`.

**Ops note:** Avoid redeploy **7:15–7:30 PT** (Breakout mid-range seeds are in-memory until activate).

---

## 🏗️ **Cloud Architecture**

### **Core Architecture Diagram**

```
┌─────────────────────────────────────────────────────────────┐
│                    Cloud Platform (GCP/AWS/etc.)          │
├─────────────────────────────────────────────────────────────┤
│  Container Service (Cloud Run / ECS / Lambda)               │
│  ├── Trading Service (Easy 0DTE Strategy — RESEARCH)       │
│  │   ├── Prime Trading Manager                             │
│  │   ├── ORB capture + SO/0DTE/TL/BO paths                 │
│  │   ├── Prime Data Manager                                │
│  │   ├── Trading Executor (Demo/Live Mode)                │
│  │   ├── Prime Alert Manager                               │
│  │   └── Unified 0DTE monitor                              │
│  ├── OAuth Service                                         │
│  │   ├── ETradeOAuth Manager                               │
│  │   ├── Token Keepalive Service                          │
│  │   └── Token Refresh Service                             │
│  └── Easy Collector Service                                │
│      ├── US Market Data Collection                          │
│      ├── Crypto Market Data Collection                     │
│      └── ML Dataset Generation                              │
├─────────────────────────────────────────────────────────────┤
│  Object Storage (GCS / S3 / etc.)                          │
│  ├── Trading State Persistence                             │
│  ├── Trade History                                         │
│  ├── Performance Logs                                      │
│  └── ML Datasets                                           │
├─────────────────────────────────────────────────────────────┤
│  Secret Management (Secret Manager / Secrets Manager)       │
│  ├── Broker API Keys                                       │
│  ├── Telegram Bot Tokens                                   │
│  └── OAuth Tokens                                          │
├─────────────────────────────────────────────────────────────┤
│  Scheduler (Cloud Scheduler / EventBridge / etc.)           │
│  ├── Trading Hours Keep-Alive                              │
│  ├── OAuth Token Refresh                                   │
│  ├── End-of-Day Reports                                    │
│  └── Automated Cleanup                                     │
├─────────────────────────────────────────────────────────────┤
│  Logging & Monitoring                                      │
│  ├── Application Logs                                      │
│  ├── Performance Metrics                                   │
│  └── Error Tracking                                        │
└─────────────────────────────────────────────────────────────┘
```

### **Service Components**

#### **1. Trading Service**
- **Purpose**: Easy 0DTE Strategy — RESEARCH watch default (ORB1 + Trendline + Breakout; SO/SC/validation **paused**)
- **Resources**: 2 vCPU, **8 GiB** (deployed). October 2026 p99 memory ~2.5 GiB — **4 GiB** is the next right-size. 2 GiB would OOM.
- **Scaling**: Scale-to-zero enabled (cost optimization)
- **Liveness**: **none** (HTTP `/health` stripped — Cloud Run rejects TCP liveness; startup is TCP :8080). `/health` is a cheap keepalive 200 only.
- **Container entrypoint**: **`cloud_run_entry.py`** — starts a minimal HTTP server on `PORT` immediately so Cloud Run’s **startup** probe passes, then runs the full app (`main.py --cloud-mode`). This avoids startup timeouts from slow OAuth/config/Secret Manager init.
- **Features**:
  - ORB capture for 0DTE geometry (`0dte_list.csv`, **150** names)
  - ORB1 watch + Trendline + Breakout (`ENABLE_ORB_STRATEGY=false`)
  - Unified 0DTE monitor (~2s exact OCC); mock-ledger GCS **off-loop**
  - Holiday filtering · stealth exits (PC-PX2/PX3)
  - Alert system integration

#### **2. OAuth Service**
- **Purpose**: Manages broker OAuth token lifecycle (web app + `oauth_backend.py`)
- **Resources**: 1 vCPU, 512Mi Memory
- **Scaling**: Scale-to-zero enabled
- **Features**:
  - Token refresh automation (keep-alive cron)
  - Token expiry alerts (Telegram)
  - Web dashboard for manual renewal
  - On `store_tokens()`, HTTP POST to trading `/api/oauth/token-renewed` (deployed Rev **00358**)

#### **3. Easy Collector Service**
- **Purpose**: Collects market data for ML algorithm development
- **Resources**: 1-2 vCPU, 1-2Gi Memory (depending on collection volume)
- **Scaling**: Scale-to-zero enabled
- **Features**:
  - US market snapshot collection (ORB, SIGNAL, OUTCOME)
  - Crypto market snapshot collection (multiple sessions)
  - ML-ready dataset generation

---

## ☁️ **Supported Cloud Platforms**

### **Primary Platform: Google Cloud Platform (GCP)**
- ✅ **Recommended**: Fully tested and optimized
- **Services Used**: Cloud Run, Cloud Storage, Secret Manager, Cloud Scheduler, Cloud Logging, Cloud Monitoring
- **Cost**: ~$50–56/month observed (Oct 2026, 2 vCPU / 8 GiB, weekday keepalive 5:00–14:59 PT). Docs target after right-size: ~$20–36.
- **Documentation**: Complete setup guide below

### **Alternative Platforms**

#### **Amazon Web Services (AWS)**
- **Container Service**: AWS ECS (Fargate) or AWS Lambda
- **Storage**: S3 for trade history and state
- **Secrets**: AWS Secrets Manager
- **Scheduler**: Amazon EventBridge (CloudWatch Events)
- **Cost Estimate**: ~$20-30/month (similar configuration)

#### **Microsoft Azure**
- **Container Service**: Azure Container Instances or Azure Functions
- **Storage**: Azure Blob Storage
- **Secrets**: Azure Key Vault
- **Scheduler**: Azure Logic Apps or Timer Triggers
- **Cost Estimate**: ~$20-30/month (similar configuration)

#### **Snowflake**
- **Note**: Snowflake is primarily a data warehouse, not a compute platform
- **Use Case**: Can be used for data storage and analytics
- **Integration**: Connect via API from GCP/AWS compute services
- **Cost**: Pay-per-use data storage and compute

#### **Other Platforms**
- **Vercel / Netlify**: Not suitable (serverless functions have execution time limits)
- **DigitalOcean**: Can use App Platform (similar to Cloud Run)
- **Heroku**: Possible but more expensive (~$25-50/month)

**Recommendation**: Use GCP for best cost optimization and feature set. AWS is a viable alternative with similar costs.

---

## 🚀 **Google Cloud Platform (GCP) - Primary Platform**

### **Required APIs**

Enable the following APIs in your Google Cloud project:

```bash
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable logging.googleapis.com
gcloud services enable monitoring.googleapis.com
gcloud services enable artifactregistry.googleapis.com  # For container images
```

### **Service Account Setup**

Create service accounts with required permissions:

```bash
# Create trading service account
gcloud iam service-accounts create etrade-strategy-sa \
    --display-name="ETrade Strategy Service Account"

# Create Easy Collector scheduler account
gcloud iam service-accounts create easy-collector-scheduler \
    --display-name="Easy Collector Scheduler Account"

# Grant permissions to trading service account
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:etrade-strategy-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:etrade-strategy-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:etrade-strategy-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/logging.logWriter"

# Grant permissions to Easy Collector scheduler account
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

### **Secret Manager Setup**

**Best practice (zero-code storage):** All credentials live in Secret Manager and are injected at runtime; never in source code. See [Security Configuration](#-security-configuration) for the full checklist.

Store required secrets in Secret Manager:

```bash
# E*TRADE API credentials
echo -n "your-consumer-key" | gcloud secrets create etrade-consumer-key --data-file=-
echo -n "your-consumer-secret" | gcloud secrets create etrade-consumer-secret --data-file=-

# Telegram credentials
echo -n "your-telegram-bot-token" | gcloud secrets create telegram-bot-token --data-file=-
echo -n "your-telegram-chat-id" | gcloud secrets create telegram-chat-id --data-file=-

# E*TRADE OAuth tokens (will be populated by OAuth service)
gcloud secrets create etrade-prod-access-token --data-file=-
gcloud secrets create etrade-prod-refresh-token --data-file=-
```

#### **⚠️ CRITICAL: Secret Manager — Token Cleanup Policy (Rev 00358+)**

**Billing:** $0.06 per **ENABLED** secret version per month.

**Canonical policy:** [OAuthSecrets.md](OAuthSecrets.md) — full protocols, API reference, incident history.

| Layer | Mechanism | When |
|-------|-----------|------|
| **Renewal prune** | `write_secret_value_with_prune()` + `prune_after_oauth_renewal()` + `ensure_oauth_env_secret_policy()` | Every OAuth PIN renewal (3 secrets × env) |
| **Midnight purge** | `GET /cron/purge-expired-tokens` via `oauth-token-purge-2355et` | **11:55 PM ET** daily (before 12:00 AM alert) |
| **Keep-alive** | E*TRADE API only — **no** `add_secret_version` | Hourly `oauth-keepalive-prod` / `sandbox` |
| **Friday safety net** | `secret-manager-cleanup-friday` → `POST /api/cleanup/images` `{"cleanup_secrets": true}` | Friday 3:00 AM PT |
| **Sunday safety net** | `gcr-image-cleanup-weekly` with `cleanup_secrets: true` | Sunday (revisions + OAuth secrets) |
| **Manual backlog** | `bash scripts/cleanup_secrets_batch.sh --until-done` | After incident or console mistakes |

**Target (six token secrets):** **1 ENABLED** version each → **~$0.36/month**.  
**Peak incident (May 2026):** ~3,186 ENABLED on `etrade-oauth-prod` alone → **~$191/month** from hourly keep-alive SM writes.

**Verify (gcloud — recommended):**
```bash
bash scripts/audit_oauth_secrets.sh
bash scripts/verify_oauth_token_policy.sh   # audit + schedulers — does NOT call live purge
bash scripts/setup_oauth_keepalive_scheduler.sh  # refresh hourly keep-alive URLs
```

**Deploy:** Use Cloud Build configs (`cloudbuild.yaml`, `cloudbuild-oauth.yaml`) so Option C image cleanup runs automatically. Verify: `bash scripts/audit_oauth_secrets.sh` → **1 ENABLED** per prod token secret. See [CloudSecrets.md](CloudSecrets.md) for project-specific `gcloud` commands.

**Console rule:** Destroy old **versions** only — never delete whole secret resources (requires full OAuth renewal to recreate).

### **Cloud Storage Buckets**

Create buckets for data persistence:

```bash
# Create buckets
gsutil mb -p YOUR_PROJECT_ID -l us-central1 gs://YOUR_PROJECT_ID-trades/
gsutil mb -p YOUR_PROJECT_ID -l us-central1 gs://YOUR_PROJECT_ID-state/
gsutil mb -p YOUR_PROJECT_ID -l us-central1 gs://YOUR_PROJECT_ID-logs/

# Set lifecycle policies (optional - auto-delete after 90 days)
gsutil lifecycle set lifecycle.json gs://YOUR_PROJECT_ID-trades/
```

---

## ⚙️ **Scale-to-Zero Configuration**

### **Overview**

Scale-to-zero allows services to shut down when not in use, dramatically reducing costs. The system automatically wakes up when needed via Cloud Scheduler jobs.

### **Configuration**

**Trading Service**:
```bash
gcloud run deploy YOUR_SERVICE_NAME \
    --min-instances 0 \
    --max-instances 1 \
    --no-cpu-throttling \
    # ... other settings
```

**OAuth Service**:
```bash
gcloud run deploy YOUR_OAUTH_SERVICE_NAME \
    --min-instances 0 \
    --max-instances 1 \
    # ... other settings
```

**Easy Collector Service**:
```bash
gcloud run deploy YOUR_COLLECTOR_SERVICE_NAME \
    --min-instances 0 \
    --max-instances 1 \
    # ... other settings
```

### **Scale-to-Zero Behavior**

**Trading Days (Monday-Friday)**:
- System wakes up at 5:30 AM PT via Good Morning alert
- Stays active through EOD (1:00 PM PT / 4:00 PM ET)
- Cloud Scheduler keep-alive jobs prevent shutdown during trading hours
- Scales to zero after EOD

**Weekends (Saturday-Sunday)**:
- System scales to zero after Friday EOD
- No charges until Monday morning
- No keep-alive jobs run (cost optimization)

**Holidays**:
- System scales to zero on non-trading days
- No keep-alive jobs run (cost optimization)
- Holiday detection prevents unnecessary wake-ups

**Cold Start Impact**:
- Container listens on `PORT` within seconds (minimal server in `cloud_run_entry.py`); full trading loop then initializes in the background
- ~10-30 seconds until trading loop is fully ready when Cloud Scheduler wakes system
- Acceptable for pre-market startup (system wakes before market open)

### **Cost Impact**

**Without Scale-to-Zero**: ~$155/month (24/7 operation)  
**With Scale-to-Zero (current 8 GiB box, 10-hour weekday wake):** ~$50–56/month. **After 4 GiB + shorter keepalive:** ~$33–38/month.  
**Savings**: 86-88% cost reduction

**Calculation**:
- Trading days: ~7.5 hours/day × 5 days/week = ~37.5 hours/week
- Without scale-to-zero: 168 hours/week (24/7)
- Reduction: 78% fewer compute hours

---

## ⏰ **Cloud Scheduler Jobs**

**Verify all jobs set and enabled:** See [CLOUD_JOBS_CHECKLIST.md](doc_elements/Sessions/2026/Feb24%20Session/CLOUD_JOBS_CHECKLIST.md) for a full checklist and `scripts/verify_all_cloud_jobs.py` to confirm every required job exists and is ENABLED.

### **Cloud Scheduler audit logs (Google Cloud policy — reviewed Mar 25, 2026)**

Google has been emitting **Admin Activity** audit logs for Cloud Scheduler in a **new standard (GFE) payload shape** since **September 15, 2025**, while continuing to emit the **legacy** shape until **September 30, 2026**. After that date, automation that still expects the old JSON (for example `authorizationInfo[].resource` as a short path, or camelCase `retryConfig` with duration strings) may break.

**This repository (Easy Trading Software workspace):**

- **Application code** does **not** read or parse Cloud Scheduler audit logs. The app is triggered by Scheduler **HTTP** requests only; diagnostics use **Cloud Run / application** logs (e.g. `gcloud run services logs read`, `PIPELINE`, `SO_PIPELINE`).
- **No code changes are required** in this codebase for the audit-log migration.

**You still need to act if** any **external** system parses Scheduler audit logs for **your GCP project**: Security Command Center export, **log sink → BigQuery**, SIEM (Splunk/Datadog/Chronicle), or custom jobs querying `cloudaudit.googleapis.com` / `protoPayload` for `cloudscheduler.googleapis.com`. Update those parsers to accept the **new** structure (full resource names, `resourceAttributes`, snake_case request fields such as `retry_config` with structured duration objects). Google also notes `callerIp` may differ between formats until legacy logs are turned off.

Official detail: use the announcement and samples in the email from Google Cloud (Mar 2026 correction to the Sept 2025 / Sept 2026 timeline).

### **ORB Strategy & 0DTE Strategy Jobs**

#### **Trading Hours Keep-Alive Jobs**

These jobs keep the trading service alive during trading hours:

```bash
# Pre-market wake-up (5:00-7:00 AM PT, every 3 minutes)
gcloud scheduler jobs create http trading-hours-keepalive-1 \
    --location=us-central1 \
    --schedule="*/3 5-6 * * 1-5" \
    --time-zone="America/Los_Angeles" \
    --uri="https://YOUR_SERVICE_URL/api/health" \
    --http-method=GET \
    --description="Keep trading service alive during pre-market (5-7 AM PT)"

# Trading session start (7:00-10:00 AM PT, every 5 minutes)
gcloud scheduler jobs create http trading-hours-keepalive-2 \
    --location=us-central1 \
    --schedule="*/5 7-9 * * 1-5" \
    --time-zone="America/Los_Angeles" \
    --uri="https://YOUR_SERVICE_URL/api/health" \
    --http-method=GET \
    --description="Keep trading service alive during trading session start (7-10 AM PT)"

# Trading session continuation (10:00 AM-2:00 PM PT, every 5 minutes)
gcloud scheduler jobs create http trading-hours-keepalive-3 \
    --location=us-central1 \
    --schedule="*/5 10-13 * * 1-5" \
    --time-zone="America/Los_Angeles" \
    --uri="https://YOUR_SERVICE_URL/api/health" \
    --http-method=GET \
    --description="Keep trading service alive during trading session (10 AM-2 PM PT)"
```

#### **Good Morning Alert**

Wakes system and sends morning status. Handler reloads broker tokens and runs a live SPY probe before the alert — metadata-only “Valid” is not enough for ORB (see [OAuth.md](OAuth.md)).

```bash
gcloud scheduler jobs create http oauth-market-open-alert \
    --location=us-central1 \
    --schedule="30 5 * * 1-5" \
    --time-zone="America/Los_Angeles" \
    --uri="https://YOUR_SERVICE_URL/api/alerts/market-open" \
    --http-method=POST \
    --description="Good Morning alert at 5:30 AM PT (8:30 AM ET)"
```

#### **Trading service — token renewal webhook (Rev 00356+)**

Called by OAuth backend after renewal or manually for testing:

```bash
# Reload in-memory credentials + live SPY probe (no ORB capture)
curl -X POST "https://YOUR_SERVICE_URL/api/oauth/token-renewed" \
  -H "Content-Type: application/json" \
  -d '{"environment": "prod"}'
```

#### **Manual ORB capture recovery (Rev 00356+)**

After token renewal, when scheduled window passed or for smoke test:

```bash
curl -X POST "https://YOUR_SERVICE_URL/api/manual-orb-capture?force=true"
```

Expect logs: `OAuth token reload: reloaded=True api_valid=True`, then `PIPELINE | STEP 1 ORB CAPTURE | COMPLETE`.

#### **7:00 AM PT – Validation Candle Open (Required for signal collection)**

Captures broker prices at 7:00 AM PT as the **open** for the 7:00–7:15 validation candle (batched 25/call, same as ORB). At 7:15 the service uses current quotes as **close** and computes GREEN/RED for each symbol. Without this job, cold start at 7:15 yields all NEUTRAL and no signals.

```bash
gcloud scheduler jobs create http validation-candle-700 \
    --location=us-central1 \
    --schedule="0 7 * * 1-5" \
    --time-zone="America/Los_Angeles" \
    --uri="https://YOUR_SERVICE_URL/api/alerts/validation-candle-700" \
    --http-method=POST \
    --description="7:00 AM PT: capture open prices for 7:00-7:15 validation candle (broker-only)"
```

#### **7:15 AM PT – Prefetch Validation Candle (Recommended for scale-to-zero)**

Runs the 7:00 open + 7:15 close prefetch so the validation candle (GREEN/RED per symbol) is ready for signal collection even when a different instance runs at 7:15. Same batched quote path as ORB (25/call). Create this job if the trading loop may not run at 7:15 (e.g. scale-to-zero).

```bash
gcloud scheduler jobs create http prefetch-validation-715 \
    --location=us-central1 \
    --schedule="15 7 * * 1-5" \
    --time-zone="America/Los_Angeles" \
    --uri="https://YOUR_SERVICE_URL/api/alerts/prefetch-validation-715" \
    --http-method=POST \
    --description="7:15 AM PT: prefetch 7:00 open + 7:15 close for validation candle"
```

#### **OAuth Token Keep-Alive**

Hourly token refresh to prevent expiry:

```bash
# Production OAuth keep-alive (every hour at :00)
gcloud scheduler jobs create http oauth-keepalive-prod \
    --location=us-central1 \
    --schedule="0 * * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_OAUTH_SERVICE_URL/api/keepalive/force/prod" \
    --http-method=POST \
    --description="Production OAuth token keep-alive (hourly)"

# Sandbox OAuth keep-alive (every hour at :30)
gcloud scheduler jobs create http oauth-keepalive-sandbox \
    --location=us-central1 \
    --schedule="30 * * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_OAUTH_SERVICE_URL/api/oauth/keepalive/sandbox" \
    --http-method=POST \
    --description="Sandbox OAuth token keep-alive (hourly)"
```

#### **OAuth Midnight Alert**

Daily token expiry reminder:

```bash
gcloud scheduler jobs create http oauth-midnight-alert \
    --location=us-central1 \
    --schedule="0 0 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_SERVICE_URL/api/alerts/oauth-expiry" \
    --http-method=POST \
    --description="OAuth token expiry alert at midnight ET"
```

#### **End-of-Day Report**

Daily performance summary:

```bash
gcloud scheduler jobs create http end-of-day-report \
    --location=us-central1 \
    --schedule="55 15 * * 1-5" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_SERVICE_URL/api/end-of-day-report" \
    --http-method=POST \
    --description="End-of-day report at 3:55 PM ET (12:55 PM PT)"
```

Live Cloud Scheduler stays on the old `5 16` (4:05 ET / 1:05 PT) job until `deploy now` plus an explicit cron update. Do not move the live job before flatten 12:50 is on the revision.

### **Easy Collector Jobs**

#### **US Market Snapshots (Weekdays Only)**

```bash
# US ORB (9:45 AM ET weekdays)
gcloud scheduler jobs create http easy-collector-us-orb \
    --location=us-central1 \
    --schedule="45 9 * * 1-5" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/us/orb" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --description="Collect US ORB snapshots at 9:45 AM ET on weekdays"

# US SIGNAL (10:30 AM ET weekdays)
gcloud scheduler jobs create http easy-collector-us-signal \
    --location=us-central1 \
    --schedule="30 10 * * 1-5" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/us/signal" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --description="Collect US SIGNAL snapshots at 10:30 AM ET on weekdays"

# US OUTCOME (3:55 PM ET weekdays)
gcloud scheduler jobs create http easy-collector-us-outcome \
    --location=us-central1 \
    --schedule="55 15 * * 1-5" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/us/outcome" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --description="Collect US OUTCOME snapshots at 3:55 PM ET on weekdays"
```

#### **Crypto Market Snapshots (Daily - All Sessions)**

**London Session**:
```bash
# London ORB (3:15 AM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-london-orb \
    --location=us-central1 \
    --schedule="15 3 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/LONDON/orb" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "LONDON"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# London SIGNAL (4:00 AM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-london-signal \
    --location=us-central1 \
    --schedule="0 4 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/LONDON/signal" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "LONDON"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# London OUTCOME (2:55 AM ET next day)
gcloud scheduler jobs create http easy-collector-crypto-london-outcome \
    --location=us-central1 \
    --schedule="55 2 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/LONDON/outcome" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "LONDON"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

**US Session**:
```bash
# US ORB (8:15 AM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-us-orb \
    --location=us-central1 \
    --schedule="15 8 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/US/orb" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "US"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# US SIGNAL (9:00 AM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-us-signal \
    --location=us-central1 \
    --schedule="0 9 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/US/signal" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "US"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# US OUTCOME (4:55 PM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-us-outcome \
    --location=us-central1 \
    --schedule="55 16 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/US/outcome" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "US"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

**Reset Session**:
```bash
# Reset ORB (5:15 PM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-reset-orb \
    --location=us-central1 \
    --schedule="15 17 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/RESET/orb" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "RESET"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Reset SIGNAL (6:00 PM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-reset-signal \
    --location=us-central1 \
    --schedule="0 18 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/RESET/signal" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "RESET"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Reset OUTCOME (6:55 PM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-reset-outcome \
    --location=us-central1 \
    --schedule="55 18 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/RESET/outcome" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "RESET"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

**Asia Session**:
```bash
# Asia ORB (7:15 PM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-asia-orb \
    --location=us-central1 \
    --schedule="15 19 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/ASIA/orb" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "ASIA"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Asia SIGNAL (8:00 PM ET daily)
gcloud scheduler jobs create http easy-collector-crypto-asia-signal \
    --location=us-central1 \
    --schedule="0 20 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/ASIA/signal" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "ASIA"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Asia OUTCOME (2:55 AM ET next day)
gcloud scheduler jobs create http easy-collector-crypto-asia-outcome \
    --location=us-central1 \
    --schedule="55 2 * * *" \
    --time-zone="America/New_York" \
    --uri="https://YOUR_COLLECTOR_URL/collect/crypto/ASIA/outcome" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"session": "ASIA"}' \
    --oidc-service-account-email=easy-collector-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### **Automated Cleanup Job**

Weekly cleanup of container images and Cloud Run revisions:

```bash
gcloud scheduler jobs create http gcr-image-cleanup-weekly \
    --location=us-central1 \
    --schedule="0 2 * * 0" \
    --time-zone="America/Los_Angeles" \
    --uri="https://YOUR_SERVICE_URL/api/cleanup/images" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"cleanup_images": false, "cleanup_revisions": true, "cleanup_secrets": true}' \
    --description="Weekly cleanup of Cloud Run revisions + OAuth secrets (Sunday 2:00 AM PT)"
```

### **Complete Job Summary**

| Job Name | Schedule | Purpose | Strategy |
|----------|----------|---------|----------|
| `trading-hours-keepalive-1` | Every 3 min, 5-7 AM PT weekdays | Pre-market wake-up | ORB + 0DTE |
| `trading-hours-keepalive-2` | Every 5 min, 7-10 AM PT weekdays | Trading session start | ORB + 0DTE |
| `trading-hours-keepalive-3` | Every 5 min, 10 AM-2 PM PT weekdays | Trading session continuation | ORB + 0DTE |
| `oauth-market-open-alert` | 5:30 AM PT weekdays | Good Morning alert | ORB + 0DTE |
| `validation-candle-700` | 7:00 AM PT weekdays | **Paused** in watch mode (`ENABLE_VALIDATION_CANDLE=false`) | Rollback only |
| `prefetch-validation-715` | 7:15 AM PT weekdays | **Paused** in watch mode | Rollback only |
| `oauth-keepalive-prod` | Hourly at :00 | Production OAuth refresh | ORB + 0DTE |
| ~~`oauth-keepalive-sandbox`~~ | — | **Deleted** (Jun 2026 hygiene) | — |
| `oauth-token-purge-2355et` | 11:55 PM ET daily | Destroy expired OAuth token SM versions | ORB + 0DTE |
| `oauth-midnight-alert` | 12:00 AM ET daily | Token renewal Telegram reminder | ORB + 0DTE |
| `end-of-day-report` | 3:55 PM ET weekdays | Daily performance report | ORB + 0DTE |
| `gcr-image-cleanup-weekly` | Sunday 2:00 AM PT | Revisions + secrets cleanup | ORB + 0DTE |
| `secret-manager-cleanup-friday` | Friday 3:00 AM PT | Secret Manager only (OAuth secrets → 1 version) | ORB + 0DTE |
| ~~`easy-collector-*` (15 jobs)~~ | — | **Deleted Oct 25 2026** — Collector cloud retired; local `easyCollector/` kept | — |

**Total Jobs**: 13 (11 enabled + 2 paused validation rollback). Collector Cloud Run `easy-collector` deleted the same day. Easy 0DTE services `easy-etrade-strategy` + `easy-etrade-strategy-oauth` unchanged.

---

## 🧹 **Cloud Cleanup Policies**

This section covers **two policies**: (1) **OAuth token Secret Manager cleanup** — security + largest variable cost risk; (2) **general cloud cost cleanup** — revisions, images, storage, scale-to-zero.

**Quick links:** [Token Secret Cleanup Policy](#token-secret-cleanup-policy-rev-00358) · [Cloud Cost Cleanup Policy](#cloud-cost-cleanup-policy) · [Cloud Deployment Optimization Strategy](#cloud-deployment-optimization-strategy)

---

### **Token Secret Cleanup Policy (Rev 00358)**

**Applies to:** OAuth token secrets in **your GCP project** (typically three prod token secrets — see [OAuthSecrets.md](OAuthSecrets.md)).

| Secret | Role | On renewal | On 11:55 PM ET purge |
|--------|------|------------|----------------------|
| `etrade-oauth-{env}` | Combined JSON token blob | Prune to 1 ENABLED | Destroy **all** ENABLED |
| `etrade-{env}-access-token` | Access token (split) | Prune to 1 ENABLED | Destroy **all** ENABLED |
| `etrade-{env}-access-token-secret` | Access secret (split) | Prune to 1 ENABLED | Destroy **all** ENABLED |

**Not purged on expiry:** consumer keys, Telegram, Polygon — static credentials.

**Renewal sequence (automated in app):**
1. `write_secret_value_with_prune` per secret (add + batched destroy of older ENABLED, up to 50 per round).
2. `prune_after_oauth_renewal(env)` — second pass on all three secrets.
3. `ensure_oauth_env_secret_policy(env)` — audit; retry prune if any secret still has &gt;1 ENABLED.
4. Live SPY probe; trading webhook `POST /api/oauth/token-renewed` with `oauth_verified: true` — reload + retry probe (**no** SM prune on trading service; Rev 00361).

**Keep-alive rule:** `keepalive_oauth.py` calls E*TRADE only (`accounts/list`). `_forbidden_keepalive_secret_write()` raises if SM writes are reintroduced. **Cloud Scheduler** (`Google-Cloud-Scheduler` User-Agent) always runs the hourly call; dashboard manual calls may skip if &lt; 80 minutes. Reads tokens via `access_latest_enabled_secret` (not `versions/latest`).

**Schedulers:**

| Job | Schedule | Endpoint / body |
|-----|----------|-----------------|
| `oauth-token-purge-2355et` | 11:55 PM ET | `GET /cron/purge-expired-tokens` |
| `oauth-midnight-alert` | 12:00 AM ET | `GET /cron/midnight-expiry-alert` (Telegram only) |
| `oauth-keepalive-prod` | Hourly :00 | `POST /api/keepalive/force/prod` |
| `secret-manager-cleanup-friday` | Fri 3 AM PT | `POST /api/cleanup/images` `cleanup_secrets: true` |

**Setup:** `bash scripts/setup_oauth_midnight_scheduler.sh` · `bash scripts/setup_secret_cleanup_friday.sh`

**Module:** `modules/secret_version_cleanup.py` — single write API for all app token paths.

---

### **Dual-app deployment model (RESEARCH + FINAL)**

Both apps share one GCP project and **identical cleanup scripts**. Deploy path differs by Cloud Run service and `MODE`.

| | **RESEARCH APP** | **FINAL APP** |
|---|------------------|---------------|
| **Folder** | `1. The Easy ORB Strategy RESEARCH APP` | `1. The Easy ORB Strategy FINAL APP` |
| **Cloud Run (Demo)** | `easy-etrade-strategy` | `easy-etrade-strategy-live` (`MODE=demo`) |
| **Cloud Run (Live)** | Use FINAL sibling deploy | `easy-etrade-strategy-live` (`MODE=live`) |
| **GCR image** | `gcr.io/…/easy-etrade-strategy` | `gcr.io/…/easy-etrade-strategy-live` |
| **Cloud Build (Demo)** | `cloudbuild.yaml` | `cloudbuild-live.yaml` |
| **Cloud Build (Live)** | `cloudbuild-live.yaml` (sibling image) | `cloudbuild-live.yaml` |
| **Deploy (Demo)** | `DEPLOY_COMMANDS.sh` or `gcloud builds submit --config cloudbuild.yaml` | `DEPLOY_NOW=YES ./scripts/deploy_demo_service.sh` |
| **Deploy (Live)** | `DEPLOY_NOW=YES ./scripts/deploy_live_service.sh` | `DEPLOY_NOW=YES ./scripts/deploy_live_service.sh` |

**Shared services (same project):** OAuth → `easy-etrade-strategy-oauth` · Collector → `easy-collector` (RESEARCH deploy only; schedulers typically **PAUSED**).

**Image cleanup (Option C):** Every Cloud Build pipeline ends with `scripts/cloudbuild_image_cleanup.sh`, which prunes **all four** GCR repos **and** Artifact Registry `cloud-run-source-deploy` to **3** digests + `latest` per package/repo. Weekly scheduler (`gcr-image-cleanup-weekly`) prunes **revisions + secrets only** — not images.

**Do not bypass Cloud Build** for production deploys (`deploy_safe.sh` local `docker push` skips Option C). Prefer `DEPLOY_COMMANDS.sh` or `gcloud builds submit --config cloudbuild*.yaml`.

---

### **Cloud Cost Cleanup Policy**

**Goal:** Keep monthly GCP spend in the **~$20–36** range (scale-to-zero) and prevent runaway Secret Manager version billing.

| Resource | Policy | Automation | Manual fallback |
|----------|--------|--------------|-----------------|
| **Cloud Run compute** | `min-instances=0`, `max-instances=1` | At deploy | — |
| **Cloud Run revisions** | Keep last **3** per service (RESEARCH + FINAL trading = **6** total) | `gcr-image-cleanup-weekly` → `cleanup_revisions: true` | `KEEP_REVISIONS=3 bash scripts/cleanup_old_revisions.sh` or re-hit API |
| **Container images (GCR)** | Last **3** digests + `latest` tag per repo | **Post-deploy** via Cloud Build Option C | `KEEP_LATEST=3 bash scripts/cleanup_old_images.sh` |
| **Artifact Registry** | Last **3** digests per package in `cloud-run-source-deploy` | **Post-deploy** + AR lifecycle policy | `KEEP_LATEST=3 bash scripts/cleanup_artifact_registry.sh` |
| **Secret Manager (OAuth)** | **1 ENABLED** per token secret | Renewal prune + 11:55 PM purge + Fri/Sun | `scripts/cleanup_secrets_batch.sh --until-done` |
| **GCS build artifacts** | Delete after **30 days** | Lifecycle policy on `*_cloudbuild` buckets | `gsutil lifecycle` (see below) |
| **GCS trading data** (`exit_monitoring/`, `daily_markers/`, `priority_optimizer/`) | Age-based delete (see [Telemetry & GCS data plane](#telemetry--gcs-data-plane-may-2026)) | `scripts/apply_gcs_data_lifecycle.sh` + in-app retention | `scripts/cleanup_priority_optimizer_data.sh` |
| **Exit monitoring collector** | **Off** on Cloud Run unless `EXIT_MONITORING_ENABLED=true` | `configs/Shared.env` + code default | Per-symbol record/blob caps when enabled |
| **Trendline JSONL** | **8 MiB** file cap, **10k** line tail | `modules/telemetry_jsonl_utils.py` on append + startup | `modules/telemetry_startup.py` on boot |
| **Cloud Scheduler** | Pause unused jobs quarterly | Manual review | Reduce invocation cost |

**Unified cleanup API (trading service):**
```bash
curl -X POST "https://YOUR_SERVICE_URL/api/cleanup/images" \
  -H "Content-Type: application/json" \
  -d '{"cleanup_images": false, "cleanup_revisions": true, "cleanup_secrets": true}'
```

**Verification:**
```bash
bash scripts/verify_oauth_token_policy.sh
gcloud run revisions list --service=YOUR_SERVICE --region=us-central1 --format="value(metadata.name)" | wc -l
```

**429 quota on revision deletes:** Wait 1–2 minutes and call cleanup again until **~3** revisions remain per service.

**Cost impact summary:**

| Item | Without cleanup | With policy (target) |
|------|-----------------|----------------------|
| Secret Manager (OAuth tokens) | **$100–300+/mo** at peak | **~$0.36/mo** (6 ENABLED) |
| Revision storage | Unbounded growth | ~91% reduction |
| Image storage | Unbounded growth | ~85% reduction |
| Cloud Run (24/7) | ~$155/mo | ~$11–15/mo (scale-to-zero) |

See [Cost Analysis](#cost-analysis) and [Cloud Deployment Optimization Strategy](#cloud-deployment-optimization-strategy).

---

### **Secret Manager — root cause (May 2026)**

| Secret | Typical backlog | Cause |
|--------|-----------------|--------|
| `etrade-oauth-prod` | **~3,000+** | Hourly `oauth-keepalive-prod` wrote a **new version** after each E*TRADE call via `gcloud secrets versions add` **without** destroying old versions |
| `etrade-oauth-sandbox` | **~1,700** | Same (`oauth-keepalive-sandbox`) |
| `etrade-*-access-token*` | **~150 each** | OAuth renewal adds 3 secrets; inline cleanup tried to delete **all** old versions in one call and timed out |

**Cost:** $0.06 per version per month → **~$191/mo** for `etrade-oauth-prod` alone at 3,186 versions.

**Fixes (Rev 00358+, deploy OAuth + trading to apply):**
1. Keep-alive **no longer** writes Secret Manager versions (API call alone prevents idle timeout).
2. On token renewal, destroy **up to 50** old versions per secret per write (batched).
3. **Friday 3:00 AM PT** — `secret-manager-cleanup-friday` → `POST /api/cleanup/images` with `cleanup_secrets: true`.
4. **Sunday** — `gcr-image-cleanup-weekly` (revisions + secrets).
5. **Backlog today:** `bash scripts/cleanup_secrets_batch.sh --until-done` (batches of 10).

**Deployment (Rev 00294+ / Option C Jun 2026):** Cleanup scripts ship in the container image (`Dockerfile` copies `./scripts/`). **Revision** cleanup runs in-container via Python Cloud Run API. **Image** cleanup runs in **Cloud Build** after each deploy (gcloud available in build step) — not in the trading container weekly job.

### **Container Image Cleanup (Option C — implemented June 2026)**

#### Problem

Each `gcloud builds submit` pushes a new digest to GCR. Without deletion, repos accumulate unbounded images. **Jul 2026 root cause of ~30 GB bloat:** `easy-etrade-strategy-live` had **42** digests because (1) `cloudbuild-live.yaml` had **no cleanup step**, and (2) `cleanup_old_images.sh` only pruned DEMO/OAuth/Collector — **never LIVE**. Weekly `gcr-image-cleanup-weekly` uses **`cleanup_images: false`** (no `gcloud` in the trading container).

#### Implemented solution: Option C (Cloud Build post-deploy)

**Primary automation:** Final step in **each** Cloud Build pipeline runs `scripts/cloudbuild_image_cleanup.sh` → `scripts/cleanup_old_images.sh`. The wrapper cleans **all active** GCR repos on every run.

| Pipeline | Config | Image pushed | Cleanup step |
|----------|--------|--------------|--------------|
| DEMO trading | `cloudbuild.yaml` | `easy-etrade-strategy:latest` | ✅ `gcr-image-cleanup` |
| **LIVE trading** | `cloudbuild-live.yaml` | `easy-etrade-strategy-live:latest` | ✅ **added Jul 2026** |
| OAuth | `cloudbuild-oauth.yaml` | `easy-etrade-strategy-oauth:latest` | ✅ `gcr-image-cleanup` |
| Collector | `easyCollector/cloudbuild.yaml` | `easy-collector:latest` | ✅ `gcr-image-cleanup` |

**Cloud Build step (all four configs):**

```yaml
  - name: 'gcr.io/cloud-builders/gcloud'
    id: gcr-image-cleanup
    entrypoint: bash
    args: ['scripts/cloudbuild_image_cleanup.sh']
    waitFor: ['build-trading']  # or build-trading-live / build-oauth / push
```

**Retention policy** (`KEEP_LATEST=3` default):

- Keep newest **3** digests per GCR repo (by timestamp)
- Always keep any digest tagged **`latest`**
- Target steady state: ≤ **4** images per repo (3 + `latest`)

**Repos pruned (GCR):** `easy-etrade-strategy` · **`easy-etrade-strategy-live`** · `easy-collector` · `easy-etrade-strategy-oauth` · legacy orphans (`etrade-strategy*`, `prime-trading-system`, …)

**Artifact Registry:** `cloud-run-source-deploy` — `scripts/cleanup_artifact_registry.sh` keeps **3** per package; `scripts/ar_lifecycle_cloud_run_source_deploy.json` applied for ongoing hygiene. Runs in `cloudbuild_image_cleanup.sh` after GCR prune.

**Why Option C (vs alternatives evaluated June 2026):**

| Criterion | A: Cloud Run Job | B: gcloud in trading container | **C: Cloud Build step** ✅ |
|-----------|------------------|-------------------------------|----------------------------|
| Automation | Weekly all repos | Weekly all repos | **Per deploy — GCR + AR** |
| Implementation effort | Medium (new Job + SA) | Low–Medium (fat image) | **Low** |
| Runtime security | Isolated SA | Trading SA needs GCR delete | **Build SA only** |
| Cold-start impact | None | +~150 MB SDK bloat | **None** |
| Missed cleanup if no deploy for weeks | No | No | **Possible** — use manual fallback |
| Coupling to trading service | None | **High** (mis-invocation risk) | **None** |

**Decision:** Option C implemented June 2026. Option A remains a **deferred** fallback if fully hands-off weekly cleanup is needed without any deploy for months. **Option B not recommended** — expands trading container permissions and blast radius.

**Verify after deploy:**

```bash
# Cloud Build logs (look for):
# CLOUDBUILD GCR IMAGE CLEANUP — SUMMARY
#   gcr.io/YOUR_PROJECT_ID/YOUR_TRADING_IMAGE: N image(s)
```

Confirm active revision digest remains in the keep set after first automated run.

**Manual fallback** (skipped Cloud Build step, or long gap without deploy):

```bash
KEEP_LATEST=3 bash scripts/cleanup_old_images.sh
KEEP_LATEST=3 bash scripts/cleanup_artifact_registry.sh
# Or from Cloud Shell if local gcloud crashes Python
```

**Weekly scheduler (unchanged — revisions + secrets only):**

```bash
curl -X POST "https://YOUR_SERVICE_URL/api/cleanup/images" \
  -H "Content-Type: application/json" \
  -d '{"cleanup_images": false, "cleanup_revisions": true, "cleanup_secrets": true}'
```

**Do not** set `cleanup_images: true` on the weekly endpoint — no `gcloud` in the trading image.

**Scripts:** `scripts/cleanup_old_images.sh` · `scripts/cleanup_artifact_registry.sh` · `scripts/cloudbuild_image_cleanup.sh` · `scripts/setup_cleanup_scheduler.sh`

**Cost impact:** Prevents GCR creep (~**$1–3/mo** if unmanaged); build step adds ~30–120s per deploy (negligible $).

### **Cloud Run Revision Cleanup**

**Automated Cleanup** (via Cloud Scheduler):
- **Schedule**: Weekly (Sunday 2:00 AM PT)
- **Retention Policy**: Keep last **3** revisions per service; keep last **3** GCR/AR images per repo/package
- **Protection**: Never delete revisions with active traffic
- **Expected Savings**: 91% reduction in stored revisions

**Manual Cleanup Script**:
```bash
# Run cleanup script
./scripts/cleanup_old_revisions.sh

# Or via API endpoint
curl -X POST https://YOUR_SERVICE_URL/api/cleanup/images \
  -H "Content-Type: application/json" \
  -d '{"cleanup_revisions": true}'
```

### **Telemetry & GCS data plane (May 2026 — updated Jun 15, 2026)**

**Problem:** Unbounded telemetry can balloon GCS and Cloud Run ephemeral disk. In one production audit, **`exit_monitoring/`** consumed ~**86%** of the trading data bucket before caps were applied.

**In-app guards (deploy with trading service):**

| Writer | Default (cloud) | Module / env |
|--------|-----------------|--------------|
| Mock options ledger | **400** closed trades rolling | `MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES` — `easy0DTE/modules/mock_options_executor.py` |
| Exit monitoring GCS | **Disabled** | `EXIT_MONITORING_ENABLED=false` — `modules/prime_exit_monitoring_collector.py` |
| Execution snapshots | Slim GCS JSON, **no local file** on Cloud Run | `EXECUTION_SNAPSHOT_SLIM`, `EXECUTION_SNAPSHOT_LOCAL`, `EXECUTION_SNAPSHOT_KEEP_SESSIONS=30` — `modules/execution_feature_snapshot.py` |
| Trendline features JSONL | **8 MiB** cap, **10k** line tail | `TELEMETRY_JSONL_*` — `easyTrendline/trendline_feature_logger.py` |
| Startup hygiene | Trim JSONL · prune local execution snapshots (**30** session dates) · clear `retrieved_data` cache | `modules/telemetry_startup.py` (runs when `K_SERVICE` / cloud mode) |

**When exit tuning needs samples:** set `EXIT_MONITORING_ENABLED=true` for **one session only**, then turn off. Optional limits: `EXIT_MONITORING_MAX_RECORDS_PER_SYMBOL=120`, `EXIT_MONITORING_MAX_BLOB_BYTES=512000`, `EXIT_MONITORING_SAMPLE_EVERY=6`.

**Canonical env:** `configs/Shared.env` § execution gating — see [Configs.md](Configs.md#telemetry--data-retention-sharedenv).

---

### **Cloud Storage Lifecycle Policies**

#### **Trading bucket** (`gs://YOUR_PROJECT_ID-data`)

Repo policy: `scripts/gcs_lifecycle_easy_etrade_data.json`

| Prefix | Max age (days) |
|--------|----------------|
| `exit_monitoring/` | **14** |
| `daily_markers/` · `research_app/daily_markers/` · `final_app/daily_markers/` | **30** |
| `research_app|final_app/alert_markers/` · `eod_markers/` · `signal_collection_markers/` | **60** |
| `priority_optimizer/execution_snapshots/` | **90** (legacy) |
| `research_app|final_app/priority_optimizer/execution_snapshots/` | **40** |
| `priority_optimizer/trendline_break_snapshots/` · `trendline_outcomes/` (+ namespaced) | **40** |
| `priority_optimizer/comprehensive_data/` | **60** |
| `priority_optimizer/daily_signals/` (+ namespaced) | **3** |
| `profit_optimizer/` | **35** |

**Apply (project-specific bucket name):**
```bash
cd "0. Strategies and Automations/1. The Easy ORB Strategy"
./scripts/apply_gcs_data_lifecycle.sh
# Or: gsutil lifecycle set scripts/gcs_lifecycle_easy_etrade_data.json gs://YOUR_PROJECT_ID-data
```

**Dedupe / manual hygiene** (does not replace lifecycle):
```bash
./scripts/cleanup_priority_optimizer_data.sh              # dry-run
EXECUTE=YES ./scripts/cleanup_priority_optimizer_data.sh   # apply stage dedupe + age purge
```

**Trade / demo ledgers** (`{research_app|final_app}/mock_options_history.json`, `live_options_history.json`): **no GCS lifecycle delete** — bounded by **in-app rolling retention** (`MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES` / `LIVE_OPTIONS_LEDGER_MAX_CLOSED_TRADES`, default **400** closed trades). Account balance, stats, and open positions are preserved.

#### **Build artifacts** (`*_cloudbuild`, `run-sources-*`)

- **Policy**: Auto-delete files older than **30 days**
- **Note**: Does NOT affect container images (Artifact Registry)

```bash
# Example: build-only bucket (not the trading data bucket)
gsutil lifecycle set lifecycle.json gs://YOUR_PROJECT_ID_cloudbuild/
```

### **Cost Impact**

- **Image Storage Savings**: ~$0.15/month
- **Revision Storage Savings**: ~$0.16/month
- **Total Cleanup Savings**: ~$0.31/month
- **Annual Savings**: ~$3.72/year

---

## ☁️ **Cloud Hygiene & Maintenance Mode (June 2026)**

**Closeout date:** June 4, 2026 · **Status:** Maintenance mode — policies in repo are correct; **July 2026 live audit** found revision/GCR drift (see below). Treat Secret Manager regression and deploy-without-Cloud-Build as **reactive** incidents.

### July 2026 live hygiene audit (project `easy-etrade-strategy`)

**Billing context:** May **$107.80** → June **$75.00** (~30% down — Secret Manager cleanup policies working). July day-1 forecast ~**$103** is extrapolation noise; re-check mid-month.

| Check | Target (policy) | Live (Jul 2, 2026) | Status |
|-------|-----------------|---------------------|--------|
| **Scale-to-zero** | `minScale=0` all services | DEMO · LIVE · OAuth · Collector all **0** | ✅ |
| **Max instances** | Trading **1** | DEMO **1** · LIVE **1** · OAuth **3** · Collector **5** | ✅ |
| **Collector schedulers** | All **PAUSED** | **15/15 PAUSED** | ✅ |
| **Required session jobs** | 7 ENABLED + OAuth/cleanup | `verify_all_cloud_jobs.py` — **all pass** | ✅ |
| **Cleanup schedulers** | Fri + Sun ENABLED | `secret-manager-cleanup-friday` · `gcr-image-cleanup-weekly` | ✅ |
| **`EXIT_MONITORING_ENABLED`** | `false` | `configs/Shared.env` — **false** | ✅ |
| **Cloud Run revisions** | ≤ **3** per service | RESEARCH **3** · LIVE **3** · OAuth **3** · Collector **3** (Jul 2, 2026) | ✅ |
| **GCR digests** | ≤ **3** + `latest` per active repo | `easy-etrade-strategy` **3** · `easy-etrade-strategy-live` **3** | ✅ |
| **Artifact Registry** | ≤ **3** per package in `cloud-run-source-deploy` | **12** total (~4 packages × 3) + lifecycle policy | ✅ |
| **Legacy GCR repos** | Delete or ignore | `etrade-strategy*` · `prime-trading-system` · `tradingview-agent` — low $ but clutter | 🟡 optional purge |
| **Secret Manager** | **1 ENABLED** per OAuth secret | Run `bash scripts/audit_oauth_secrets.sh` monthly | 🔍 verify |
| **Cross-project bleed** | Only `easy-etrade-strategy` bills | Odin Kalshibot VM **TERMINATED** · TradingView schedulers **PAUSED** | ✅ |

**Runaway cost #1 (historical):** Secret Manager OAuth version bloat — **~$191/mo** peak May 2026. **Mitigation in code:** keep-alive no longer writes SM versions; Fri/Sun cleanup jobs; `oauth-token-purge-2355et` nightly.

**Runaway cost #2 (resolved Jul 2026):** GCR + revision + AR sprawl — fixed via `KEEP_REVISIONS=3` / `KEEP_LATEST=3` and post-deploy Option C. Re-run if drift returns:

```bash
cd "0. Strategies and Automations/1. The Easy ORB Strategy RESEARCH APP"

# 1) Revisions + OAuth secret prune (in-container API)
curl -s -X POST "https://easy-etrade-strategy-hskvzzwwxq-uc.a.run.app/api/cleanup/images" \
  -H "Content-Type: application/json" \
  -d '{"cleanup_images": false, "cleanup_revisions": true, "cleanup_secrets": true}'
# Repeat after 1–2 min if 429 quota hit

# 2) GCR image prune (needs local gcloud)
KEEP_LATEST=3 bash scripts/cleanup_old_images.sh
KEEP_LATEST=3 bash scripts/cleanup_artifact_registry.sh

# 3) OAuth secret audit (target: 1 ENABLED per prod token secret)
bash scripts/audit_oauth_secrets.sh
```

**Healthy monthly target:** **~$33–38** for `easy-etrade-strategy` after 4 GiB + keepalive through 13:00 PT (observed Sep/Oct 2026 **~$55** on the current 8 GiB / 10-hour box). Odin residual **~$1.20/mo** (30 GB disk on a TERMINATED `kalshibot-demo` VM) until that disk is deleted.

**Operator note:** Local macOS `gcloud`/`gsutil` may crash Python (SDK + LibreSSL). Run remaining GCP one-time steps in **[Google Cloud Shell](https://console.cloud.google.com/?cloudshell=true)**:

```bash
cd "/path/to/1. The Easy ORB Strategy"
bash scripts/cloud_hygiene_phase3_gcp_cloudshell.sh
```

### Status summary

| Phase 3 action | Repository | GCP (live) |
|----------------|------------|------------|
| **Option C — Cloud Build image cleanup** | ✅ All four `cloudbuild*.yaml` (incl. `cloudbuild-live.yaml` in both apps) | Active on **next** `gcloud builds submit` per pipeline |
| **`exit_monitoring/` legacy delete + 14d lifecycle** | ✅ `gcs_lifecycle_easy_etrade_data.json` | Run Cloud Shell script once if not done |
| **Sandbox token secret shells** | ✅ Docs + app = prod-only | Verified absent Jun 4 (optional: delete `etrade-sandbox-consumer-*`) |

### Final architecture (data & cleanup)

| Layer | Component | Role |
|-------|-----------|------|
| **Compute** | `YOUR_TRADING_SERVICE` Cloud Run | ORB + 0DTE + Trendline; trading-hours keepalive |
| **OAuth** | `YOUR_OAUTH_SERVICE` | Token renewal; SM writes with prune |
| **Collector** | `YOUR_COLLECTOR_SERVICE` | Snapshots; schedulers **PAUSED** when unused |
| **Images** | GCR × **4** repos + AR `cloud-run-source-deploy` | Post-deploy Option C; `KEEP_LATEST=3` |
| **Secrets** | SM prod tokens only | Renewal prune + 11:55 PM ET purge + Fri/Sun weekly |
| **Telemetry GCS** | `gs://YOUR_PROJECT_ID-data` | Lifecycle per prefix; exit monitoring **off** |

### Authoritative GCS retention

**Bucket:** `gs://YOUR_PROJECT_ID-data`  
**Source:** `scripts/gcs_lifecycle_easy_etrade_data.json`  
**Apply:** `scripts/apply_gcs_data_lifecycle.sh` or Cloud Shell script above

| Prefix | Age delete | Writer / consumer |
|--------|------------|-------------------|
| `exit_monitoring/` | **14 days** | `EXIT_MONITORING_ENABLED=false` — write-only when enabled; **no readers** in app |
| `daily_markers/` · `research_app|final_app/daily_markers/` | 30 days | Session markers (ORB1/ORB2/TL/SO/PP/`watch_fsm` + compact `qg_admit`) |
| `research_app|final_app/alert_markers/` · `eod_markers/` · `signal_collection_markers/` | 60 days | Telegram dedup |
| `priority_optimizer/execution_snapshots/` | 90 days | Legacy PO / guides |
| `research_app|final_app/priority_optimizer/execution_snapshots/` | **40 days** | Namespaced 7:30 execution snapshots |
| `priority_optimizer/trendline_break_snapshots/` · `trendline_outcomes/` (+ namespaced) | **40 days** | Trendline research |
| `priority_optimizer/comprehensive_data/` | 60 days | PO |
| `priority_optimizer/daily_signals/` (+ namespaced) | 3 days | In-app ~2d |
| `profit_optimizer/` | 2 days in-app | Metrics JSON + 2s JSONL (`daily/` · `paths/` · `cohorts/`); retrieve daily for local archive |
| `{ns}/mock_options_history.json` · `live_options_history.json` | *(none — in-app)* | Rolling **400** closed trades; balances/stats kept |

**Dedupe / manual hygiene:** `scripts/cleanup_priority_optimizer_data.sh` (stage dedupe + age purge).

### Persistence required for trading performance

Must-persist day-freeze + ledger + OAuth + **watch FSM** plane (full key table): **[Data.md §10 — Persistence required for app performance](Data.md#persistence-required-for-app-performance)**. Without it, Cloud Run restart can redraw wrong ORB/TL geometry, double-fire SO, drop open-position monitors, or re-admit with empty Skip Signal keys.

**QG admit after a kill:** compact `qg_admit` (sva, ATM IV, seconds, dir%, VWAP, vol, RSI, BB, L1d5) lives on `{ns}/daily_markers/{date}.json` → `watch_fsm` and restores onto candidate metadata. 1m bar cache and the L1 quote ring stay **ephemeral** — rebuild bars via `hydrate_equity_session_tech` (one-symbol if sva is still empty at admit); L1d5 miss-passes until ~5s of quote polls. Full PO snapshot stays **execution-only**. SIGTERM runs `flush_watch_fsm_on_shutdown`; a hard kill without SIGTERM can still lose ~5s FSM debounce and ~8s open-book flush. Grep: `QG_ADMIT_REBUILD` · `QG_SS_FIELD_GAP` · `stamp_qg_admit_fields`. Local until **`deploy now`**.

### Phase 2 audit findings (June 4, 2026)

| Workstream | Cost significance | Status |
|------------|-------------------|--------|
| **Cloud Logging** | Negligible (~0.1 GiB/mo) | ✅ Healthy — see [Monitoring & Logging](#monitoring--logging) |
| **Exit monitoring GCS** | Negligible (~82 MiB legacy) | ✅ Writer off; lifecycle 14d; one-time legacy delete via Cloud Shell |
| **Profit Optimizer GCS** | Short-retention, bounded | ✅ 2d in-app; durable history is local retrieved archive |
| **Image cleanup** | Low (~$1–3/mo if unmanaged) | ✅ Option C on deploy — [Container Image Cleanup](#container-image-cleanup-option-c--implemented-june-2026) |
| **Sandbox resources** | ~$0.12/mo max | ✅ Token shells deleted; optional consumer-key delete |

**Realistic savings from Phase 2–3:** ~**$0–2/month** — operational hygiene, not material bill reduction. Dominant costs remain **Cloud Run trading-hours keepalive** (~$11–25/mo) and **scheduler wakes**.

### Remaining cloud-cost risk ranking

| Rank | Risk | Severity | Mitigation |
|------|------|----------|------------|
| **1** | Secret Manager version bloat | 🔴 High if regresses | Renewal prune, 11:55 PM purge, Fri/Sun cleanup, keep-alive **no SM writes** |
| **2** | GCR image accumulation | 🟡 Medium | **Option C** on every Cloud Build deploy |
| **3** | Cloud Run revision sprawl | 🟢 Low | Weekly API cleanup (~3/service) |
| **4** | GCS telemetry runaway | 🟢 Low | `EXIT_MONITORING_ENABLED=false`; PO caps; lifecycle rules |
| **5** | Cloud Logging volume | 🟢 Negligible | ~0.1 GiB/mo vs 50 GiB free tier |
| **6** | Sandbox secret shells | 🟢 Negligible | Mostly deleted Jun 2026 |
| **7** | Trading-hours keepalive compute | 🟡 Intentional | Required for ORB session; `max-instances=1` |

### Exit monitoring — verified policy

| Check | Result |
|-------|--------|
| `EXIT_MONITORING_ENABLED` in `configs/Shared.env` | **`false`** |
| Code consumers of `gs://.../exit_monitoring/` | **None** (write path only in `prime_exit_monitoring_collector.py`) |
| Profit / Priority Optimizer | **No dependency** |
| Cloud Run env override | Not set — uses Shared.env default |

**When tuning exits locally:** enable for **one session only**, then revert. Caps: `EXIT_MONITORING_MAX_RECORDS_PER_SYMBOL=120`, `EXIT_MONITORING_MAX_BLOB_BYTES=512000`, `EXIT_MONITORING_SAMPLE_EVERY=6`.

### Sandbox — verified policy (June 2026)

| Resource | State |
|----------|-------|
| `etrade-sandbox-access-token` / `*-secret` / `etrade-oauth-sandbox` | **NOT_FOUND** (deleted) |
| `etrade-sandbox-consumer-key/secret` | Exist, 1 ENABLED each — optional later delete (~$0.12/mo) |
| `oauth-keepalive-sandbox` scheduler | **Deleted** |

App uses **production tokens only** — [OAuthSecrets.md](OAuthSecrets.md).

### Actions NOT worth pursuing (cost)

| Action | Reason |
|--------|--------|
| Shorten Cloud Logging retention below 30 days | Saves &lt;$0.01; hurts forensics |
| Exclude all INFO / monitor ticks | Breaks desk greps (`PROFIT_OPTIMIZER_*`, `TRENDLINE_*`) |
| Re-enable `EXIT_MONITORING_ENABLED` on Cloud Run long-term | Historical 86% bucket driver |
| Pause trading-hours keepalive | Breaks scale-to-zero reliability |
| BigQuery log sink | Adds cost without analytics need |

### Quarterly maintenance checklist

- [ ] `bash scripts/audit_oauth_secrets.sh` — exactly **1 ENABLED** per prod token secret
- [ ] GCR digest count per **active** repo ≤ **4** (3 + `latest`) — `KEEP_LATEST=3 bash scripts/cleanup_old_images.sh`
- [ ] Artifact Registry `cloud-run-source-deploy` ≤ **3** per package — `KEEP_LATEST=3 bash scripts/cleanup_artifact_registry.sh`
- [ ] Cloud Run revisions per service ≤ **3** (RESEARCH + FINAL trading = **6** total across both apps)
- [ ] `python3 scripts/verify_all_cloud_jobs.py` — 7 session jobs + cleanup jobs ENABLED; all `easy-collector-*` **PAUSED**
- [ ] `gsutil du -s gs://YOUR_PROJECT_ID-data` — no single prefix &gt;50% of bucket
- [ ] `EXIT_MONITORING_ENABLED` still **false**
- [ ] Last Cloud Build log shows `CLOUDBUILD GCR IMAGE CLEANUP — SUMMARY`
- [ ] Logging ingestion &lt;1 GiB/mo (Console → Logging → Usage)
- [ ] Cross-project: Odin Kalshibot VM **stopped** or deleted; TradingView agent idle

**Detail audits (reference):** [ExitMonitoringRetentionAudit.md](ExitMonitoringRetentionAudit.md) · [ProfitOptimizerRetentionAudit.md](ProfitOptimizerRetentionAudit.md) · [SandboxResourceAudit.md](SandboxResourceAudit.md)

---

## 📐 **Cloud Deployment Optimization Strategy**

This section defines the **critical cloud cleanup and cost optimization strategy** for the Easy ORB Strategy. Follow this to keep deployment costs efficient and optimized.

### **Source tarball size (`.gcloudignore`)**

ORB Strategy Cloud Run builds upload only paths **not** excluded by **`.gcloudignore`** (see **[Deploy.md](Deploy.md)** § *Lean Cloud Build source upload*). Keep uploads lean (~**4 MiB** uncompressed source budget), exclude **`docs/`**, **`easyCollector/`**, local **`.gcloud_tmp/`**, and duplicate/unused assets so **ORB ETF + ORB 0DTE + Trendline 0DTE** runtime files still ship. Verify with `gcloud meta list-files-for-upload .` before each submit.

### **Strategy Overview**

| Priority | Action | Frequency | Impact |
|----------|--------|-----------|--------|
| **1. Scale-to-zero** | `min-instances=0`, `max-instances=1` for all services | At deploy | 85–90% cost reduction |
| **2. Revision cleanup** | Keep last **3** revisions per service (traffic-protected) | Weekly (automated) | Prevents unbounded revision growth |
| **3. Image cleanup** | Keep last **3** digests + `latest` (GCR + AR) via Cloud Build Option C | **Every deploy** (automated) | Prevents container storage creep |
| **4. Secret Manager** | 1 version per secret; batched destroy on renewal; Friday + Sunday jobs | Automatic + weekly | Prevents $100+/mo version bloat |
| **5. Scheduler pruning** | Pause unused jobs (e.g., Easy Collector if not used) | Quarterly review | Reduces job count and invocations |

### **Critical Cleanup Checklist**

**One-time setup (before first deploy):**
- [ ] Grant `roles/run.admin` to the trading service account (for in-container revision cleanup)
- [ ] Create Cloud Scheduler job `gcr-image-cleanup-weekly` (Sunday 2:00 AM PT)
- [ ] Verify `google-cloud-run>=0.10.0` in requirements.txt
- [ ] Ensure cleanup scripts are in Dockerfile COPY (Rev 00294+)

**Weekly (automated via `gcr-image-cleanup-weekly`):**
- [ ] Revision + secret cleanup via `POST /api/cleanup/images` with `{"cleanup_revisions": true, "cleanup_secrets": true, "cleanup_images": false}`

**Every deploy (automated via Cloud Build Option C):**
- [ ] GCR image cleanup runs in build logs — verify `CLOUDBUILD GCR IMAGE CLEANUP — SUMMARY`

**Monthly (manual verification):**
- [ ] GCR image count per repo ≤ **4**: `gcloud container images list-tags gcr.io/YOUR_PROJECT_ID/YOUR_TRADING_IMAGE --format="value(digest)" | wc -l`
- [ ] Revision count **3** per service (trading services: RESEARCH + FINAL = **6** total)

**Quarterly:**
- [ ] Review Cloud Scheduler jobs; pause Easy Collector jobs if unused
- [ ] Check Secret Manager versions (target: 1 per secret)
- [ ] Verify scale-to-zero: `gcloud run services describe YOUR_SERVICE --region=us-central1 --format="yaml(spec.template.spec.containerConcurrency,spec.template.metadata.annotations)"`

### **Rate Limits and Quotas**

Cloud Run enforces a **Write requests per minute per region** quota. When deleting many revisions in one run:

- **Symptom:** `429 Quota exceeded for quota metric 'Write requests'`
- **Behavior:** Revision cleanup succeeds for some deletions, then hits the limit
- **Action:** Wait 1–2 minutes and call the cleanup endpoint again; repeat until revision count is **~3** per service
- **Prevention:** Weekly runs keep revision count low, so quota is rarely hit

### **Verification Commands**

```bash
# Revision count per service (target: 3 each; RESEARCH + FINAL trading = 6 total)
gcloud run revisions list --service=YOUR_TRADING_SERVICE --region=us-central1 --format="value(metadata.name)" | wc -l

# Test cleanup endpoint (revisions only)
curl -s -X POST "https://YOUR_SERVICE_URL/api/cleanup/images" \
  -H "Content-Type: application/json" \
  -d '{"cleanup_images": false, "cleanup_revisions": true}'

# Trigger scheduler job manually
gcloud scheduler jobs run gcr-image-cleanup-weekly --location=us-central1
```

### **Documentation References**

- **Project-specific values and scripts:** [CloudSecrets.md](CloudSecrets.md) § Cloud Cleanup
- **Full cleanup policies:** [Cloud Cleanup Policies](#cloud-cleanup-policies) (above)
- **Cost breakdown:** [Cost Analysis](#cost-analysis) (below)

---

## 💰 **Cost Analysis**

### **Monthly Cost Breakdown (GCP with Scale-to-Zero)**

| Service | Resource | ORB Strategy | 0DTE Strategy | Easy Collector | Total |
|---------|----------|-------------|---------------|----------------|-------|
| **Cloud Run (Trading)** | 2 vCPU, **8 GiB**, scale-to-zero, CPU always on | **~$50–53** | Included | - | **~$50–53** |
| **Cloud Run (OAuth)** | 1 vCPU, 512Mi, scale-to-zero | $0.30–0.50 | Included | - | $0.30–0.50 |
| **Cloud Run (Collector)** | 1 vCPU, 512Mi | - | - | **$0** (schedulers PAUSED) | **$0** |
| **Cloud Storage** | `easy-etrade-strategy-data` ~70 MB | ~$0.01 | Included | - | ~$0.01 |
| **Cloud Scheduler** | 11 enabled + 17 paused jobs | ~$2.50 | Included | paused jobs still count | ~$2.50 |
| **Secret Manager** | 6 ENABLED static versions | $0.36 | Included | - | $0.36 |
| **Artifact Registry / GCR** | 9.4 GB (active + leftover images) | ~$0.94 | Included | - | ~$0.94 |
| **Cloud Logging** | 24–47 GiB / 30d | ~$0 | Included | - | **~$0** (under 50 GiB free tier) |
| **Firebase Hosting** | Desk + OAuth sites | $0 (free tier) | Included | - | $0 |
| **Total (trading project)** | | **~$55** | **Included** | **$0** | **~$55/month** |

### **Cost Optimization**

**Before Optimization (historical)**: ~$155/month (24/7) + $200/month (Secret Manager) = ~$355/month  
**Observed Jun 2026 bill:** **$75.00** account-wide (Odin disk, legacy GCR, SM).  
**Observed Sep 2026 invoice:** **$86.83** — Cloud Storage **$23.77** (+336%, PO / flight-recorder fill) + Cloud Logging **$14.59** + Cloud Run **$39.59**.  
**Observed Oct 1–25, 2026 invoice:** **$46.28** (Storage **$9.20**, Logging **$0**, Cloud Run **$30.10**). **October forecast $59.06** (−32% vs September). Storage caps and 2-day lifecycle are holding (data bucket ~70 MB).  
**Target after optional right-size (4 GiB + keepalive through 13:00 PT):** **~$45–50**. The September overrun is already fixed.

**Key Optimizations**:
1. ✅ Scale-to-zero (only pay when running)
2. ✅ Resource optimization (right-sized containers)
3. ✅ Container image cleanup (**Option C** — post-deploy Cloud Build)
4. ✅ Storage lifecycle policies (auto-delete old files)
5. ✅ Efficient Cloud Scheduler usage
6. ✅ **Secret Manager version cleanup** (automatic, keeps only latest version per secret)

**After Secret Manager fix**: For further reductions (Cloud Run settings, TradingView Agent disabled, Scheduler job pruning, Artifact Registry), see **CloudSecrets.md** § Cost control and the session doc **CloudCostReduction.md** in `doc_elements/Sessions/2026/Feb12 Session/`.

### **Cost by Strategy**

**ORB Strategy Only**: ~$15.26-24.85/month  
**0DTE Strategy Only**: Included with ORB Strategy (shared infrastructure)  
**Easy Collector Only**: ~$4.07-10.45/month  
**All Strategies Combined**: ~$19.33-35.30/month

**Note**: ORB Strategy and 0DTE Strategy share the same Cloud Run service and infrastructure, so there's no additional cost for enabling 0DTE Strategy.

---

## 🚀 **Deployment Steps**

### **Step 1: Prerequisites**

1. Create Google Cloud project (or use existing)
2. Enable required APIs (see above)
3. Create service accounts with proper permissions
4. Store secrets in Secret Manager
5. Create Cloud Storage buckets

### **Step 2: Build Container Images**

Build from the **ORB Strategy project root** (directory containing `main.py`, `Dockerfile`, `cloud_run_entry.py`). The trading service image uses `cloud_run_entry.py` as the container CMD so the process listens on `PORT` immediately.

```bash
# From ORB Strategy project root (directory containing main.py, Dockerfile, cloud_run_entry.py)
# Build trading service image (includes cloud_run_entry.py entrypoint)
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/YOUR_TRADING_IMAGE:latest .

# Build OAuth service image (separate project/repo if applicable)
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/YOUR_OAUTH_IMAGE:latest

# Build Easy Collector image
cd easyCollector
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/YOUR_COLLECTOR_IMAGE:latest .
```

### **Step 3: Deploy Services**

**Trading Service**:
```bash
gcloud run deploy YOUR_SERVICE_NAME \
    --image gcr.io/YOUR_PROJECT_ID/YOUR_TRADING_IMAGE:latest \
    --platform managed \
    --region us-central1 \
    --memory 2Gi \
    --cpu 2 \
    --max-instances 1 \
    --min-instances 0 \
    --concurrency 80 \
    --timeout 3600 \
    --no-cpu-throttling \
    --service-account etrade-strategy-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --set-env-vars="ENVIRONMENT=production,STRATEGY_MODE=standard,ETRADE_MODE=demo,SYSTEM_MODE=full_trading,CLOUD_MODE=true,ENABLE_0DTE_STRATEGY=true,LOG_LEVEL=INFO" \
    --allow-unauthenticated
```

**OAuth Service**:
```bash
gcloud run deploy YOUR_OAUTH_SERVICE_NAME \
    --image gcr.io/YOUR_PROJECT_ID/YOUR_OAUTH_IMAGE:latest \
    --platform managed \
    --region us-central1 \
    --memory 512Mi \
    --cpu 1 \
    --max-instances 1 \
    --min-instances 0 \
    --concurrency 100 \
    --timeout 300 \
    --service-account etrade-strategy-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --set-env-vars="ENVIRONMENT=production" \
    --allow-unauthenticated
```

**Easy Collector Service**:
```bash
gcloud run deploy YOUR_COLLECTOR_SERVICE_NAME \
    --image gcr.io/YOUR_PROJECT_ID/YOUR_COLLECTOR_IMAGE:latest \
    --platform managed \
    --region us-central1 \
    --memory 2Gi \
    --cpu 2 \
    --max-instances 1 \
    --min-instances 0 \
    --concurrency 10 \
    --timeout 900 \
    --service-account etrade-strategy-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --set-env-vars="ENVIRONMENT=production" \
    --allow-unauthenticated
```

### **Step 4: Set Up Cloud Scheduler Jobs**

Use the Cloud Scheduler job commands provided in the [Cloud Scheduler Jobs](#cloud-scheduler-jobs) section above.

**Quick Setup Script**:
```bash
# For Easy Collector (15 jobs)
cd easyCollector
./SETUP_SCHEDULER.sh

# For ORB/0DTE Strategy (manual setup - see commands above)
# Or use setup scripts if available
```

### **Step 5: Verify Deployment**

```bash
# Check services are running
gcloud run services list --region=us-central1

# Check logs
gcloud run services logs read YOUR_SERVICE_NAME --region=us-central1 --limit 50

# /api/health is a scheduler wake ping — not Cloud Run liveness (liveness is off).
# HTTP /health shares the trading asyncio loop; Cloud Run rejects TCP liveness.
curl https://YOUR_SERVICE_URL/api/health
```

---

## 📊 **Monitoring & Logging**

### **Cloud Logging**

**Prerequisites:** `gcloud auth login` and `gcloud config set project YOUR_PROJECT_ID`. See [CloudSecrets.md](CloudSecrets.md) for project-specific values and a full "How to Fetch Cloud Logs" reference.

View logs in real-time:
```bash
# View recent logs
gcloud run services logs read YOUR_SERVICE_NAME \
    --region=us-central1 \
    --limit 50

# Follow logs
gcloud run services logs tail YOUR_SERVICE_NAME \
    --region=us-central1

# Filter logs (resource-based)
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=YOUR_SERVICE_NAME" \
    --project=YOUR_PROJECT_ID \
    --limit 50 \
    --freshness=1d \
    --format="table(timestamp,textPayload)"
```

**Fetching Cloud Logs for Diagnosis:** Use `gcloud logging read` with text filters to pull specific events (e.g. signal collection, validation candle, 0-signals diagnosis). For the complete command set and copy-paste examples, see [CloudSecrets.md § How to Fetch Cloud Logs](CloudSecrets.md#how-to-fetch-cloud-logs).

### **Cloud Logging — cost & volume (June 2026 audit)**

**Verdict:** Cloud Logging is **not a meaningful cost driver** (~**80–120 MiB/month** ingestion vs **50 GiB/month** free tier → effectively **$0**).

| Bucket | Retention | Purpose |
|--------|-----------|---------|
| `_Default` | **30 days** | Application, Cloud Run, platform logs |
| `_Required` | **400 days** | Audit / activity logs |

**Estimated monthly volume (trading + OAuth):** ~261,000 entries · ~0.08–0.10 GiB ingestion.

**Highest-volume emitters (startup noise, not billing):**

| Token / pattern | Category |
|-----------------|----------|
| `CONFIG_MISSING_KEY` | Startup config warnings (`config_loader`) |
| `TRENDLINE_CONFIG_UNUSED_WARNING` | Trendline config audit (startup) |
| `ORB_0DTE_*`, `TRENDLINE_*`, `PROFIT_OPTIMIZER_*` | Session-bounded strategy logs |

**Recommendations:** Keep **30-day** retention; **no** cost-driven log exclusions. Optional: reduce `CONFIG_MISSING_KEY` spam when keys are intentional (operational noise only).

**Verification:**

```bash
gcloud logging buckets list --project=YOUR_PROJECT_ID --location=global
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=YOUR_SERVICE_NAME AND timestamp>="2026-06-04T00:00:00Z"' \
  --project=YOUR_PROJECT_ID --limit=20 --format="table(timestamp,textPayload)"
```

### **Cloud Monitoring**

Set up monitoring dashboards:
- Trading performance metrics
- API usage and rate limits
- System health and errors
- Cost tracking
- Request latency
- Error rates

### **Alerting**

Configure alerting policies for:
- Service downtime
- High error rates
- Cost threshold exceeded
- OAuth token expiry warnings

---

## 🔒 **Security Configuration**

### **Google-Recommended Security Best Practices**

Follow this unified security framework to reduce risk from long-lived credentials and unauthorized access. These align with Google Cloud’s advisory for credential and key management.

#### **1. Zero-code storage (credentials never in source)**

- **Never** commit API keys, OAuth tokens, or secrets to source code or version control.
- Store all credentials in **Secret Manager** and inject at runtime (e.g. Cloud Run env or startup).
- Keep a local **PrivateSecrets.md** (or similar) out of the repo (add to `.gitignore`); use it only for your own reference and for populating Secret Manager.
- This project uses Secret Manager for E*TRADE keys, Telegram tokens, and OAuth tokens; no secrets live in the codebase.

#### **2. Disable dormant keys**

- **Audit** service account keys and API keys regularly.
- **Decommission** any key with no activity in the last 30 days.
- Prefer **Workload Identity** or **no user-managed keys** where possible (e.g. Cloud Run using a service account without downloaded keys).

#### **3. Enforce API and key restrictions**

- **Never** leave an API key unrestricted.
- **Limit keys** to specific APIs (e.g. only the APIs your app needs).
- **Apply restrictions** by environment: IP addresses, HTTP referrers, or bundle IDs as appropriate for the key type.
- In Google Cloud Console: APIs & Services → Credentials → select key → set “API restrictions” and “Application restrictions”.

#### **4. Least privilege (service accounts)**

- **Never** grant broad roles (e.g. Owner, Editor) to service accounts.
- Grant only the **minimum roles** required (e.g. `roles/secretmanager.secretAccessor`, `roles/storage.objectAdmin`, `roles/logging.logWriter`, `roles/run.invoker` as needed).
- Use **IAM Recommender** in the Cloud Console (IAM → Recommender) to find and remove unused permissions for service accounts.
- Review recommendations periodically and prune access.

#### **5. Mandatory rotation and key lifecycle**

- **Service account keys (if you must use them):**  
  Enforce a maximum lifetime with the organization policy `iam.serviceAccountKeyExpiryHours` (e.g. 90 days) so keys expire and must be rotated.
- **Prefer no user-managed keys:**  
  Where possible, use **Workload Identity** or attach a service account to Cloud Run/Compute so no key file exists. To block creation of new keys, set the organization policy `iam.disableServiceAccountKeyCreation` (or the constraint `iam.managed.disableServiceAccountKeyCreation`) so new service account keys cannot be created.
- **Broker/OAuth tokens:**  
  Rotate via your OAuth flow and Secret Manager; keep only the latest version per secret to limit exposure and cost.

#### **6. Operational safeguards**

- **Essential Contacts:**  
  In Google Cloud Console → IAM & Admin → **Essential Contacts**, set contacts for security and billing so critical notifications (e.g. security incidents, abuse) reach the right people.
- **Billing anomaly and budget alerts:**  
  In Billing → Budgets & alerts, create a **budget** and enable **billing anomaly** and **budget threshold** alerts. A sudden spike in consumption is often the first sign of compromised credentials; act on these alerts promptly.

### **IAM Roles (project setup)**

Apply least privilege when creating service accounts:

- Service account with **minimal required permissions** (see § Google-Recommended above).
- Secret Manager: `roles/secretmanager.secretAccessor` for credentials only.
- Storage: `roles/storage.objectAdmin` (or narrower) for persistence buckets only.
- Logging: `roles/logging.logWriter`.
- **No** roles such as Owner, Editor, or Security Admin unless strictly required.

### **Network Security**

- Cloud Run services are publicly accessible (required for Cloud Scheduler HTTP invocations).
- OAuth and management endpoints protected by access codes or authentication.
- API endpoints can be protected by IAM or identity-aware access (optional).
- Use a VPC connector for private resources if needed.

### **Data Encryption**

- All secrets encrypted at rest in Secret Manager.
- All data encrypted in transit (HTTPS).
- Cloud Storage encrypted by default; CMEK optional for additional control.

---

## 💾 **Data Persistence**

### **GCS Persistence**

**Trade History**:
- Trades persist immediately to GCS when closed
- Trade history survives Cloud Run redeployments
- Format: JSON files in `gs://YOUR_PROJECT_ID-trades/`

**System State**:
- Trading state persisted to GCS
- Account balances persisted (Demo mode)
- Format: JSON files in `gs://YOUR_PROJECT_ID-state/`

**Performance Logs**:
- Performance metrics logged to GCS
- Format: JSON files in `gs://YOUR_PROJECT_ID-logs/`

### **Backup Strategy**

- **Automatic**: GCS provides automatic redundancy
- **Manual**: Export trade history periodically
- **Retention**: Keep all trade history (no auto-delete)

---

## ✅ **Production Readiness**

### **Pre-Deployment Checklist**

- [ ] Google Cloud project created and configured
- [ ] Required APIs enabled
- [ ] Service accounts created with **least-privilege** permissions (see Security Configuration)
- [ ] **No credentials in code**; all secrets in Secret Manager, injected at runtime
- [ ] Secrets stored in Secret Manager
- [ ] Container images built and tested
- [ ] Cloud Run services configured
- [ ] Cloud Scheduler jobs created
- [ ] Monitoring and alerting set up
- [ ] **Essential Contacts** set (IAM & Admin → Essential Contacts) for security/billing notifications
- [ ] **Billing budget and anomaly alerts** configured; notifications acted on
- [ ] Network security configured
- [ ] Backup and recovery procedures tested
- [ ] GCS persistence configured
- [ ] Cost monitoring active

### **Post-Deployment Checklist**

- [ ] All services running and healthy
- [ ] Monitoring dashboards populated
- [ ] Alerting policies active
- [ ] OAuth tokens refreshed successfully
- [ ] Trading signals generating correctly
- [ ] Telegram alerts working
- [ ] Performance metrics within expected ranges
- [ ] Cost monitoring active
- [ ] Trade persistence working
- [ ] 0DTE strategy enabled (if configured)
- [ ] Easy Collector collecting data (if configured)
- [ ] **IAM Recommender** reviewed; unused service account permissions pruned
- [ ] **Service account / API keys** audited; dormant keys (30+ days inactive) decommissioned

### **Best Practices**

#### **Security (Google-recommended)**
- **Zero-code storage:** Never commit keys; use Secret Manager and inject at runtime
- **Least privilege:** Use IAM Recommender to prune unused permissions; no Owner/Editor for service accounts
- **Key restrictions:** Restrict API keys to specific APIs and environments (IP/referrer/bundle)
- **Rotation / no keys:** Prefer no user-managed SA keys (e.g. Cloud Run attached SA); if keys exist, enforce expiry and rotate
- **Operational:** Set Essential Contacts and billing anomaly/budget alerts; act on spikes (possible compromise)
- Encrypt all sensitive data; rotate credentials regularly; audit dormant keys quarterly

#### **Performance**
- Monitor resource utilization
- Optimize based on actual usage patterns
- Implement proper caching strategies
- Use async processing where possible

#### **Reliability**
- Implement comprehensive error handling
- Set up proper monitoring and alerting
- Regular backup procedures
- Disaster recovery planning
- GCS persistence for trade history

#### **Cost Management**
- Right-size resources based on actual usage
- Monitor costs continuously
- Implement budget alerts
- Regular cost optimization reviews
- Use scale-to-zero for cost optimization

---

## 🔄 **Alternative Cloud Platforms**

### **Amazon Web Services (AWS)**

**Container Service**: AWS ECS (Fargate) or AWS Lambda  
**Storage**: S3  
**Secrets**: AWS Secrets Manager  
**Scheduler**: Amazon EventBridge (CloudWatch Events)  
**Cost**: ~$20-30/month (similar configuration)

**Deployment Steps**:
1. Create ECS cluster or Lambda functions
2. Store secrets in AWS Secrets Manager
3. Set up EventBridge rules for scheduling
4. Configure S3 buckets for data persistence
5. Set up CloudWatch for monitoring

### **Microsoft Azure**

**Container Service**: Azure Container Instances or Azure Functions  
**Storage**: Azure Blob Storage  
**Secrets**: Azure Key Vault  
**Scheduler**: Azure Logic Apps or Timer Triggers  
**Cost**: ~$20-30/month (similar configuration)

**Deployment Steps**:
1. Create Container Instances or Functions
2. Store secrets in Azure Key Vault
3. Set up Logic Apps for scheduling
4. Configure Blob Storage for data persistence
5. Set up Azure Monitor for monitoring

### **Migration Guide**

To migrate from GCP to AWS/Azure:
1. Export trade history from GCS
2. Recreate secrets in new platform's secret manager
3. Update configuration files with new service URLs
4. Redeploy container images to new platform
5. Set up equivalent scheduler jobs
6. Test all functionality
7. Import trade history to new storage

---

## 📞 **Support & Troubleshooting**

### **Common Issues**

**Service Not Waking Up**:
- Check Cloud Scheduler jobs are enabled
- Verify service URLs are correct
- Check service account permissions
- Review logs for errors

**OAuth Token Expiry**:
- Check OAuth keep-alive jobs are running
- Verify token refresh endpoint is accessible
- Review OAuth service logs
- Manually renew tokens via web dashboard

**Good Morning valid but ORB capture 0 symbols (`token_rejected`)**:
- Secret Manager may be fresh while Cloud Run **in-memory** token is stale or rejected
- Deploy a revision that includes OAuth reload + ORB token prep (Rev **00358+** feature set)
- Renew at OAuth web app before 5:30 AM PT; grep `OAuth token reload` and `ORB_TOKEN_PREP`
- See [OAuth.md](OAuth.md) and [CloudSecrets.md](CloudSecrets.md) for project-specific revision numbers and URLs

**High Costs**:
- Verify scale-to-zero is enabled (`min-instances=0`)
- Check Cloud Scheduler jobs aren't running too frequently
- Review Cloud Storage usage
- Enable cleanup policies
- **Check Secret Manager versions** (should be 1 per secret):
  ```bash
  for SECRET in $(gcloud secrets list --format='value(name)'); do
    COUNT=$(gcloud secrets versions list $SECRET --format='value(name)' | wc -l)
    if [ $COUNT -gt 1 ]; then
      echo "⚠️  $SECRET has $COUNT versions (should be 1)"
    fi
  done
  ```
  If versions > 1, run: `bash scripts/cleanup_secrets_batch.sh --until-done` and `bash scripts/audit_oauth_secrets.sh`

### **Useful Commands**

```bash
# List all Cloud Scheduler jobs
gcloud scheduler jobs list --location=us-central1

# Check service status
gcloud run services describe SERVICE_NAME --region=us-central1

# View service logs
gcloud run services logs read SERVICE_NAME --region=us-central1 --limit 100

# Check costs
gcloud billing accounts list
gcloud billing projects list --billing-account=BILLING_ACCOUNT_ID
```

---

## 📝 **Revision History**

Feature-level changelog (no project-specific revision IDs — see [CloudSecrets.md](CloudSecrets.md) for live deployment state).

### **June 2026 — Telemetry hygiene (Rev Jun 15)**
- Ledger rolling retention: `MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES=400` (closed trades only)
- GCS lifecycle expansion: namespaced execution snapshots **40d**; Trendline break/outcome prefixes **40d** — `scripts/gcs_lifecycle_easy_etrade_data.json`
- ORB entry execution telemetry: `metadata.execution_payload` · `EXECUTION_FILL_SUMMARY`

### **June 2026 — Cloud hygiene closeout**
- Option C post-deploy GCR cleanup in all three Cloud Build pipelines
- GCS lifecycle policy (`exit_monitoring/` 14d, PO/PO prefixes)
- Cloud Logging audit: negligible cost (~0.1 GiB/mo)
- `EXIT_MONITORING_ENABLED=false` verified as production default

### **May 2026 — OAuth token lifecycle (Rev 00358+)**
- Token SM prune on renewal; 11:55 PM ET purge; keep-alive **no SM writes**
- Webhook `oauth_verified` on token renew; `cloudbuild-oauth.yaml`
- Scripts: `audit_oauth_secrets.sh`, `verify_oauth_token_policy.sh`

### **May 2026 — Telemetry bounds**
- Exit monitoring off by default; JSONL caps; slim execution snapshots
- Bucket lifecycle for `daily_markers/` and optimizer prefixes

### **February 2026 — Cloud Run startup**
- **`cloud_run_entry.py`** — listen on `PORT` immediately; avoid startup timeout

### **January 2026 — Cleanup automation (Rev 00259+)**
- Weekly revision cleanup via `/api/cleanup/images`
- Cleanup scripts in container image; Secret Manager version hygiene

---

*Last Updated: August 26, 2026 · Easy 0DTE Strategy — RESEARCH APP — placeholders only. TCP liveness local until `deploy now`. Project-specific values: [CloudSecrets.md](CloudSecrets.md)*
