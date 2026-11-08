"""
Cloud cost hygiene: revision + Secret Manager cleanup via Google Cloud APIs.
Rev 00294: Revision cleanup in-container (no gcloud).
Rev 00358: Secret Manager version cleanup in-container (no gcloud).
Jul 2026: KEEP_REVISIONS=3; protect revisions with active traffic.
"""
import os
from typing import List, Set, Tuple
import logging

log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GCP_PROJECT", "YOUR_GCP_PROJECT_ID")
REGION = os.environ.get("CLOUD_RUN_REGION", "us-central1")
# Trading (RESEARCH + FINAL) + shared OAuth/Collector — 3 revisions each
SERVICES = [
    "YOUR_SERVICE_NAME",
    "YOUR_SERVICE_NAME-live",
    "easy-collector",
    "YOUR_SERVICE_NAME-oauth",
]
KEEP_REVISIONS = int(os.environ.get("KEEP_REVISIONS", "3"))

# Keep latest N versions per secret ($0.06/version/month)
SECRET_KEEP_VERSIONS = 1
SECRETS_TO_PRUNE = [
    "etrade-oauth-prod",
    "etrade-prod-access-token",
    "etrade-prod-access-token-secret",
]


def cleanup_cloud_run_revisions() -> dict:
    """
    Delete old Cloud Run revisions, keeping the latest KEEP_REVISIONS per service.
    Uses Cloud Run Admin API - works inside container without gcloud.
    Returns dict with deleted counts and any errors.
    """
    results = {"deleted": 0, "kept": 0, "errors": [], "per_service": {}}
    try:
        from google.cloud.run_v2 import RevisionsClient
        from google.cloud.run_v2.types import ListRevisionsRequest, DeleteRevisionRequest
        from google.api_core import exceptions as google_exceptions
    except ImportError as e:
        results["errors"].append(f"google-cloud-run not installed: {e}")
        return results

    client = RevisionsClient()
    parent_base = f"projects/{PROJECT_ID}/locations/{REGION}"

    for service in SERVICES:
        parent = f"{parent_base}/services/{service}"
        try:
            deleted, kept, errs = _cleanup_service_revisions(
                client, parent, service, ListRevisionsRequest, DeleteRevisionRequest, google_exceptions
            )
            results["deleted"] += deleted
            results["kept"] += kept
            results["errors"].extend(errs)
            results["per_service"][service] = {"deleted": deleted, "kept": kept}
        except google_exceptions.NotFound:
            log.info(f"   {service}: service not found (skipped)")
            results["per_service"][service] = {"deleted": 0, "kept": 0, "skipped": "service not found"}

    return results


def _traffic_revision_ids(service_name: str) -> Set[str]:
    """Revision short names (last path segment) that currently receive traffic."""
    ids: Set[str] = set()
    try:
        from google.cloud.run_v2 import ServicesClient
        from google.cloud.run_v2.types import GetServiceRequest

        client = ServicesClient()
        name = f"projects/{PROJECT_ID}/locations/{REGION}/services/{service_name}"
        svc = client.get_service(request=GetServiceRequest(name=name))
        for target in svc.traffic or []:
            if target.revision:
                ids.add(target.revision.split("/")[-1])
    except Exception as e:
        log.warning("   %s: could not read traffic targets (%s) — skip traffic guard", service_name, e)
    return ids


def _cleanup_service_revisions(
    client, parent: str, service_name: str,
    ListRevisionsRequest, DeleteRevisionRequest, google_exceptions
) -> Tuple[int, int, List[str]]:
    deleted, kept = 0, 0
    errors = []
    traffic_ids = _traffic_revision_ids(service_name)
    try:
        request = ListRevisionsRequest(parent=parent, page_size=100)
        revisions = list(client.list_revisions(request=request))
        def _sort_key(r):
            if not r.create_time:
                return 0
            return getattr(r.create_time, 'seconds', 0) or 0
        revisions.sort(key=_sort_key, reverse=True)

        total = len(revisions)
        log.info(f"   {service_name}: {total} revisions, keeping latest {KEEP_REVISIONS}")

        kept_rank = 0
        for rev in revisions:
            rev_name = rev.name or ""
            rev_id = rev_name.split("/")[-1]
            if rev_id in traffic_ids:
                kept += 1
                log.info(f"   ✅ Keeping (traffic): {rev_id}")
                continue
            kept_rank += 1
            if kept_rank <= KEEP_REVISIONS:
                kept += 1
                continue
            if not rev_name:
                continue
            try:
                op = client.delete_revision(request=DeleteRevisionRequest(name=rev_name))
                op.result(timeout=30)
                deleted += 1
                log.info(f"   🗑️ Deleted: {rev_id}")
            except google_exceptions.PermissionDenied as e:
                errors.append(f"{service_name}: Permission denied for {rev_name}: {e}")
                kept += 1
            except Exception as e:
                errors.append(f"{service_name}: Failed to delete {rev_name}: {e}")
                kept += 1
    except google_exceptions.NotFound:
        raise  # Propagate so caller can skip service
    except Exception as e:
        errors.append(f"{service_name}: List failed: {e}")

    return deleted, kept, errors


def cleanup_secret_manager_versions(
    secret_ids: List[str] = None,
    keep_latest: int = SECRET_KEEP_VERSIONS,
    max_destroy_per_secret: int = 500,
) -> dict:
    """Drain old versions via batched prune (scheduler / POST /api/cleanup/images)."""
    try:
        from modules.secret_version_cleanup import prune_all_oauth_secrets
    except ImportError as e:
        return {
            "destroyed": 0,
            "errors": [str(e)],
            "per_secret": {},
            "estimated_monthly_savings_usd": 0.0,
        }
    return prune_all_oauth_secrets(
        secret_ids=secret_ids or SECRETS_TO_PRUNE,
        project_id=PROJECT_ID,
        keep_latest=keep_latest,
        max_destroy_per_secret=max_destroy_per_secret,
    )
