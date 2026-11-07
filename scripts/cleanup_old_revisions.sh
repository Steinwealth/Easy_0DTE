#!/bin/bash
# ======================================================
# Cloud Run Revision Cleanup Script
# Cleans up old Cloud Run revisions while keeping recent ones
# Project: easy-etrade-strategy
# Rev 00259: Revision cleanup automation
# ======================================================

set +e  # Don't exit on error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Project configuration
PROJECT_ID="easy-etrade-strategy"
REGION="us-central1"

# Retention settings (Jul 2026: 3 per service — RESEARCH + FINAL trading + OAuth + Collector)
KEEP_REVISIONS="${KEEP_REVISIONS:-3}"

echo -e "${GREEN}🧹 Starting Cloud Run Revision Cleanup${NC}"
echo ""

# Set GCP project
gcloud config set project $PROJECT_ID

# Function to cleanup old revisions for a service
cleanup_service_revisions() {
    local SERVICE=$1
    
    echo -e "${YELLOW}📦 Cleaning up ${SERVICE} revisions...${NC}"
    
    # Get all revisions, sorted by last transition time (oldest first)
    # Exclude revisions with traffic (active revisions)
    local REVISIONS=$(gcloud run revisions list \
        --service=$SERVICE \
        --region=$REGION \
        --format="value(metadata.name,status.conditions[0].lastTransitionTime,status.traffic)" \
        --sort-by="~metadata.creationTimestamp" 2>/dev/null)
    
    if [ -z "$REVISIONS" ]; then
        echo "  ⚠️  No revisions found for ${SERVICE}"
        return
    fi
    
    local TOTAL=$(echo "$REVISIONS" | wc -l | tr -d ' ')
    echo "  📊 Total revisions: $TOTAL"
    echo "  📅 Keeping latest: ${KEEP_REVISIONS} revisions"
    
    local DELETED=0
    local KEPT=0
    local LINE_NUM=0
    
    # Process revisions (newest first) — avoid pipe subshell so counters are accurate
    while IFS=$'\t' read -r NAME DATE TRAFFIC; do
        LINE_NUM=$((LINE_NUM + 1))

        if [ "$LINE_NUM" -le "$KEEP_REVISIONS" ]; then
            echo "  ✅ Keeping (latest ${KEEP_REVISIONS}): ${NAME} (${DATE})"
            KEPT=$((KEPT + 1))
            continue
        fi

        if [ -n "$TRAFFIC" ] && [ "$TRAFFIC" != "None" ] && [ "$TRAFFIC" != "0" ]; then
            echo "  ✅ Keeping (has traffic): ${NAME} (${DATE}, traffic: ${TRAFFIC})"
            KEPT=$((KEPT + 1))
            continue
        fi

        echo "  🗑️  Deleting: ${NAME} (${DATE}, no traffic)"
        ERR=$(gcloud run revisions delete "$NAME" --region="$REGION" --quiet 2>&1)
        if [ $? -eq 0 ]; then
            DELETED=$((DELETED + 1))
        else
            echo "    ⚠️  Failed: ${ERR:-unknown error}"
            KEPT=$((KEPT + 1))
        fi
    done <<< "$REVISIONS"
    
    echo "  📊 Summary: Kept ${KEPT}, Deleted ${DELETED}"
    echo ""
}

# Cleanup all Cloud Run services (Rev 00294: include OAuth)
echo "=========================================="
echo "Cleaning up Easy ORB Strategy revisions"
echo "=========================================="
cleanup_service_revisions "easy-etrade-strategy"

echo "=========================================="
echo "Cleaning up Easy Collector revisions"
echo "=========================================="
cleanup_service_revisions "easy-collector"

echo "=========================================="
echo "Cleaning up Easy OAuth revisions"
echo "=========================================="
cleanup_service_revisions "easy-etrade-strategy-oauth"

echo "=========================================="
echo "Cleaning up FINAL app (LIVE service) revisions"
echo "=========================================="
cleanup_service_revisions "easy-etrade-strategy-live"

echo -e "${GREEN}✅ Revision cleanup complete!${NC}"
echo ""
echo "📋 Note: Cloud Run automatically manages some revisions."
echo "  This script helps clean up excess revisions beyond the default retention."
