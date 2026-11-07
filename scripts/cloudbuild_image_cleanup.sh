#!/usr/bin/env bash
# Post-deploy GCR image cleanup — intended as final Cloud Build step (Option C).
# Cleans all active repos (DEMO, LIVE, OAuth, Collector); emits summary into build logs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KEEP_LATEST="${KEEP_LATEST:-3}"
export GCP_PROJECT="${GCP_PROJECT:-${PROJECT_ID:-easy-etrade-strategy}}"

echo "=========================================="
echo "CLOUDBUILD GCR IMAGE CLEANUP — START"
echo "Project: ${GCP_PROJECT} | keep newest: ${KEEP_LATEST} per repo (+ latest tag)"
echo "=========================================="

bash "${ROOT}/scripts/cleanup_old_images.sh"

echo ""
echo "=========================================="
echo "CLOUDBUILD ARTIFACT REGISTRY CLEANUP — START"
echo "=========================================="
export APPLY_AR_LIFECYCLE="${APPLY_AR_LIFECYCLE:-YES}"
bash "${ROOT}/scripts/cleanup_artifact_registry.sh" || echo "⚠️  AR cleanup step failed (non-fatal for deploy)"

echo ""
echo "=========================================="
echo "CLOUDBUILD GCR IMAGE CLEANUP — SUMMARY"
for repo in easy-etrade-strategy easy-etrade-strategy-live easy-collector easy-etrade-strategy-oauth; do
  count=$(gcloud container images list-tags "gcr.io/${GCP_PROJECT}/${repo}" \
    --format="value(digest)" 2>/dev/null | wc -l | tr -d ' ')
  echo "  gcr.io/${GCP_PROJECT}/${repo}: ${count:-0} image(s)"
done
ar_count=$(gcloud artifacts docker images list \
  "us-central1-docker.pkg.dev/${GCP_PROJECT}/cloud-run-source-deploy" \
  --format="value(version)" 2>/dev/null | wc -l | tr -d ' ')
echo "  us-central1-docker.pkg.dev/${GCP_PROJECT}/cloud-run-source-deploy: ${ar_count:-0} image(s)"
echo "=========================================="
echo "CLOUDBUILD GCR IMAGE CLEANUP — COMPLETE"
echo "=========================================="
