#!/bin/bash
# ======================================================
# GCR Image Cleanup Script
# Cleans up old container images while keeping recent ones
# Project: easy-etrade-strategy
# Rev 00259 + May 2026 fix: full sha256 digest + prune beyond KEEP_LATEST
# ======================================================

set +e  # Continue on individual delete failures; report at end

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_ID="${GCP_PROJECT:-easy-etrade-strategy}"
KEEP_LATEST="${KEEP_LATEST:-3}"
BATCH_PAUSE_SEC="${BATCH_PAUSE_SEC:-0}"

echo -e "${GREEN}🧹 Starting GCR Image Cleanup${NC}"
echo "   Project: $PROJECT_ID | keep newest: $KEEP_LATEST per repo (+ any 'latest' tag)"
echo ""

gcloud config set project "$PROJECT_ID" >/dev/null

cleanup_repository() {
    local REPO=$1
    local REPO_NAME
    REPO_NAME=$(basename "$REPO")

    echo -e "${YELLOW}📦 Cleaning up ${REPO_NAME}...${NC}"

    if ! gcloud container images describe "$REPO" >/dev/null 2>&1; then
        echo "  ⚠️  Repository not found: ${REPO}"
        echo ""
        return 0
    fi

    # Newest first; digest must include sha256: prefix for delete API
    local LIST_FILE
    LIST_FILE=$(mktemp)
    # Must use get(digest) — value(digest) strips sha256: and breaks delete API
    gcloud container images list-tags "$REPO" \
        --sort-by=~TIMESTAMP \
        --format="get[delimiter='\t'](digest,tags)" \
        >"$LIST_FILE" 2>/dev/null

    if [ ! -s "$LIST_FILE" ]; then
        echo "  ⚠️  No images found for ${REPO_NAME}"
        rm -f "$LIST_FILE"
        echo ""
        return 0
    fi

    local TOTAL
    TOTAL=$(wc -l < "$LIST_FILE" | tr -d ' ')
    echo "  📊 Total images: $TOTAL"

    local DELETED=0
    local KEPT=0
    local FAILED=0
    local RANK=0

    while IFS=$'\t' read -r DIGEST TAGS; do
        RANK=$((RANK + 1))
        # Defensive: gcloud value() formatter can emit short digests without sha256:
        if [[ "$DIGEST" != sha256:* ]]; then
            DIGEST="sha256:${DIGEST}"
        fi
        local REASON=""
        local DO_DELETE=false

        # Always keep newest N (by timestamp sort)
        if [ "$RANK" -le "$KEEP_LATEST" ]; then
            REASON="newest ${KEEP_LATEST}"
        elif [ -n "$TAGS" ] && [ "$TAGS" != "None" ] && echo "$TAGS" | grep -qw "latest"; then
            REASON="tag:latest"
        else
            DO_DELETE=true
        fi

        if [ "$DO_DELETE" = false ]; then
            echo "  ✅ Keeping (${REASON}): ${DIGEST#sha256:}"
            KEPT=$((KEPT + 1))
            continue
        fi

        local DELETE_ARGS=(--quiet)
        if [ -n "$TAGS" ] && [ "$TAGS" != "None" ]; then
            DELETE_ARGS+=(--force-delete-tags)
        fi

        echo "  🗑️  Deleting #${RANK}: ${DIGEST#sha256:} tags=${TAGS:-none}"
        local ERR_FILE
        ERR_FILE=$(mktemp)
        if gcloud container images delete "${REPO}@${DIGEST}" "${DELETE_ARGS[@]}" 2>"$ERR_FILE"; then
            DELETED=$((DELETED + 1))
        elif grep -qi "Not found" "$ERR_FILE" 2>/dev/null; then
            DELETED=$((DELETED + 1))
        else
            FAILED=$((FAILED + 1))
            echo -e "    ${RED}Failed:${NC} $(tr '\n' ' ' <"$ERR_FILE" | head -c 200)"
        fi
        rm -f "$ERR_FILE"

        if [ "$BATCH_PAUSE_SEC" -gt 0 ] && [ $((DELETED % 25)) -eq 0 ] && [ "$DELETED" -gt 0 ]; then
            sleep "$BATCH_PAUSE_SEC"
        fi
    done <"$LIST_FILE"

    rm -f "$LIST_FILE"
    echo "  📊 Summary: kept=$KEPT deleted=$DELETED failed=$FAILED"
    echo ""
    return 0
}

echo "=========================================="
echo "Cleaning up Easy ORB Strategy images"
echo "=========================================="
cleanup_repository "gcr.io/${PROJECT_ID}/easy-etrade-strategy"

echo "=========================================="
echo "Cleaning up Easy Collector images"
echo "=========================================="
cleanup_repository "gcr.io/${PROJECT_ID}/easy-collector"

echo "=========================================="
echo "Cleaning up OAuth service images"
echo "=========================================="
cleanup_repository "gcr.io/${PROJECT_ID}/easy-etrade-strategy-oauth"

echo "=========================================="
echo "Cleaning up LIVE Mode sibling images"
echo "=========================================="
cleanup_repository "gcr.io/${PROJECT_ID}/easy-etrade-strategy-live"

# Legacy repos (orphaned names — prune to same retention)
for LEGACY in easy-etrade-strategy-scanner easy-etrade-strategy-v2 easy-etrade-strategyatest \
  etrade-strategy etrade-strategy-scanner prime-trading-system tradingview-agent; do
  cleanup_repository "gcr.io/${PROJECT_ID}/${LEGACY}"
done

REMAINING=$(gcloud container images list-tags "gcr.io/${PROJECT_ID}/easy-etrade-strategy" --format="value(digest)" 2>/dev/null | wc -l | tr -d ' ')
LIVE_REMAINING=$(gcloud container images list-tags "gcr.io/${PROJECT_ID}/easy-etrade-strategy-live" --format="value(digest)" 2>/dev/null | wc -l | tr -d ' ')
echo -e "${GREEN}✅ Image cleanup complete${NC}"
echo "   easy-etrade-strategy images remaining: ${REMAINING:-unknown} (target ≤ ${KEEP_LATEST} + latest tag)"
echo "   easy-etrade-strategy-live images remaining: ${LIVE_REMAINING:-unknown} (target ≤ ${KEEP_LATEST} + latest tag)"
echo ""
echo "Re-run if failed>0 or count still high. Tune: KEEP_LATEST=3 ./scripts/cleanup_old_images.sh"
