"""
OAuth token Secret Manager lifecycle ($0.06 per ENABLED version / month).

Protocols:
- On renewal: keep exactly 1 ENABLED version per token secret (batched destroy).
- On daily expiry: destroy ALL ENABLED versions at 11:55 PM ET (before 12:00 AM renew alert).
- Never add Secret Manager versions from keep-alive (E*TRADE API call only).
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

log = logging.getLogger(__name__)

PROJECT_ID = "YOUR_GCP_PROJECT_ID"
DEFAULT_MAX_DESTROY_PER_CALL = 50
MAX_PRUNE_ROUNDS = 40

# Production OAuth tokens only (E*TRADE no longer provides sandbox access tokens).
OAUTH_ENVIRONMENT = "prod"

ALL_OAUTH_TOKEN_SECRETS = [
    "etrade-oauth-prod",
    "etrade-prod-access-token",
    "etrade-prod-access-token-secret",
]


def oauth_secret_ids_for_env(env: str) -> List[str]:
    """The three billable secrets written on each OAuth renewal (production only)."""
    if env != OAUTH_ENVIRONMENT:
        raise ValueError(f"Unsupported OAuth environment: {env!r} (only {OAUTH_ENVIRONMENT!r})")
    return [
        f"etrade-oauth-{env}",
        f"etrade-{env}-access-token",
        f"etrade-{env}-access-token-secret",
    ]


def write_secret_value_with_prune(
    secret_id: str,
    value: str,
    project_id: str = PROJECT_ID,
    keep_latest: int = 1,
) -> dict:
    """
    Canonical Secret Manager write for OAuth/token secrets.
    Adds one version then destroys older ENABLED versions until ``keep_latest`` remain.
    All production token writes MUST use this (never raw add_secret_version alone).
    """
    client = _get_client(None)
    parent = f"projects/{project_id}/secrets/{secret_id}"
    try:
        client.get_secret(request={"name": parent})
    except Exception:
        client.create_secret(
            request={
                "parent": f"projects/{project_id}",
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )
    client.add_secret_version(
        request={"parent": parent, "payload": {"data": value.encode("utf-8")}}
    )
    prune_result = prune_secret_until(
        secret_id,
        project_id=project_id,
        keep_latest=keep_latest,
        client=client,
    )
    log.info(
        "WRITE_SECRET | %s | pruned_destroyed=%s",
        secret_id,
        prune_result.get("destroyed", 0),
    )
    return {
        "secret_id": secret_id,
        "pruned_destroyed": prune_result.get("destroyed", 0),
        "errors": prune_result.get("errors", []),
    }


def audit_oauth_token_secrets(project_id: str = PROJECT_ID) -> dict:
    """Return ENABLED version counts for production token secrets (target: 1 each)."""
    try:
        client = _get_client(None)
    except ImportError as e:
        return {"error": str(e)}
    report = {}
    for secret_id in ALL_OAUTH_TOKEN_SECRETS:
        parent = f"projects/{project_id}/secrets/{secret_id}"
        try:
            enabled = _list_enabled_versions(client, parent)
            report[secret_id] = {
                "enabled_versions": len(enabled),
                "ok": len(enabled) <= 1,
                "missing": False,
            }
        except Exception as e:
            err = str(e)
            report[secret_id] = {
                "enabled_versions": 0,
                "ok": False,
                "missing": "not found" in err.lower() or "NOT_FOUND" in err,
                "error": err[:120],
            }
    report["all_ok"] = all(
        v.get("ok", False) and not v.get("missing", False)
        for v in report.values()
        if isinstance(v, dict)
    )
    return report


def _get_client(client=None):
    if client is not None:
        return client
    from google.cloud import secretmanager
    return secretmanager.SecretManagerServiceClient()


def _version_number(version) -> int:
    """Numeric Secret Manager version id from resource name (.../versions/N)."""
    try:
        return int(str(version.name).rsplit("/", 1)[-1])
    except (ValueError, AttributeError):
        return 0


def _version_sort_key(version) -> int:
    """
    Sort ENABLED versions oldest → newest for prune (keep highest id).
    Prefer version number over create_time — new versions can lack create_time
    immediately after add_secret_version and were incorrectly pruned first.
    """
    vn = _version_number(version)
    if vn:
        return vn
    if not version.create_time:
        return 0
    return getattr(version.create_time, "seconds", 0) or 0


def _list_enabled_versions(client, parent: str):
    versions = list(
        client.list_secret_versions(
            request={"parent": parent, "filter": "state:ENABLED"}
        )
    )
    versions.sort(key=_version_sort_key)
    return versions


def latest_enabled_version_resource_name(
    secret_id: str,
    project_id: str = PROJECT_ID,
    client=None,
) -> Optional[str]:
    """Resource name for newest ENABLED version, or None if missing."""
    client = _get_client(client)
    parent = f"projects/{project_id}/secrets/{secret_id}"
    enabled = _list_enabled_versions(client, parent)
    if not enabled:
        return None
    return enabled[-1].name


def access_latest_enabled_secret(
    secret_id: str,
    project_id: str = PROJECT_ID,
    client=None,
) -> str:
    """
    Read secret payload from newest ENABLED version.
    Do not use ``versions/latest`` — it is the highest version *number*, even if DESTROYED.
    """
    client = _get_client(client)
    version_name = latest_enabled_version_resource_name(
        secret_id, project_id=project_id, client=client
    )
    if not version_name:
        raise ValueError(f"No ENABLED version for secret {secret_id}")
    response = client.access_secret_version(request={"name": version_name})
    return response.payload.data.decode("utf-8")


def _destroy_version(client, version) -> bool:
    try:
        if version.state.name == "ENABLED":
            client.destroy_secret_version(request={"name": version.name})
        else:
            client.delete_secret_version(request={"name": version.name})
        return True
    except Exception as e:
        log.warning("destroy %s failed: %s", version.name, e)
        return False


def prune_secret_versions(
    secret_id: str,
    project_id: str = PROJECT_ID,
    keep_latest: int = 1,
    max_destroy: int = DEFAULT_MAX_DESTROY_PER_CALL,
    client=None,
    enabled_only: bool = True,
) -> dict:
    """
    Destroy oldest ENABLED versions, keeping ``keep_latest`` enabled versions.
    """
    result = {
        "secret_id": secret_id,
        "before_enabled": 0,
        "destroyed": 0,
        "remaining_enabled_approx": 0,
        "errors": [],
    }
    try:
        client = _get_client(client)
    except ImportError as e:
        result["errors"].append(str(e))
        return result

    parent = f"projects/{project_id}/secrets/{secret_id}"
    try:
        if enabled_only:
            versions = _list_enabled_versions(client, parent)
        else:
            versions = list(client.list_secret_versions(request={"parent": parent}))
            versions.sort(
                key=lambda v: getattr(v.create_time, "seconds", 0) if v.create_time else 0
            )
    except Exception as e:
        result["errors"].append(f"list: {e}")
        return result

    result["before_enabled"] = len(versions)
    if len(versions) <= keep_latest:
        result["remaining_enabled_approx"] = len(versions)
        return result

    to_remove = versions[:-keep_latest] if keep_latest > 0 else versions
    if max_destroy > 0:
        to_remove = to_remove[:max_destroy]

    destroyed = 0
    for version in to_remove:
        if _destroy_version(client, version):
            destroyed += 1
            if destroyed % 25 == 0:
                time.sleep(0.2)
        else:
            result["errors"].append(f"destroy failed: {version.name}")
            break

    result["destroyed"] = destroyed
    result["remaining_enabled_approx"] = max(keep_latest, len(versions) - destroyed)
    if destroyed:
        log.info(
            "SECRET_PRUNE | %s | enabled_before=%s destroyed=%s remaining~%s",
            secret_id,
            len(versions),
            destroyed,
            result["remaining_enabled_approx"],
        )
    return result


def prune_secret_until(
    secret_id: str,
    project_id: str = PROJECT_ID,
    keep_latest: int = 1,
    max_destroy_per_round: int = DEFAULT_MAX_DESTROY_PER_CALL,
    max_rounds: int = MAX_PRUNE_ROUNDS,
    client=None,
) -> dict:
    """Repeat batched prune until <= keep_latest ENABLED versions or max_rounds."""
    total_destroyed = 0
    errors: List[str] = []
    for _ in range(max_rounds):
        batch = prune_secret_versions(
            secret_id,
            project_id=project_id,
            keep_latest=keep_latest,
            max_destroy=max_destroy_per_round,
            client=client,
        )
        total_destroyed += batch.get("destroyed", 0)
        errors.extend(batch.get("errors", [])[:1])
        if batch.get("destroyed", 0) == 0:
            break
        if batch.get("remaining_enabled_approx", 99) <= keep_latest:
            break
        time.sleep(0.3)
    return {
        "secret_id": secret_id,
        "destroyed": total_destroyed,
        "errors": errors[:3],
    }


def purge_all_enabled_secret_versions(
    secret_id: str,
    project_id: str = PROJECT_ID,
    max_destroy: int = DEFAULT_MAX_DESTROY_PER_CALL,
    client=None,
) -> dict:
    """Destroy ALL enabled versions (midnight expiry / revoke dead tokens)."""
    return prune_secret_versions(
        secret_id,
        project_id=project_id,
        keep_latest=0,
        max_destroy=max_destroy,
        client=client,
        enabled_only=True,
    )


def purge_expired_oauth_environment(
    env: str,
    project_id: str = PROJECT_ID,
    max_rounds: int = MAX_PRUNE_ROUNDS,
) -> dict:
    """Remove all stored credentials for env after E*TRADE midnight expiry."""
    try:
        client = _get_client(None)
    except ImportError as e:
        return {"environment": env, "destroyed": 0, "errors": [str(e)]}

    total = 0
    per_secret = {}
    errors: List[str] = []
    for secret_id in oauth_secret_ids_for_env(env):
        destroyed = 0
        for _ in range(max_rounds):
            batch = purge_all_enabled_secret_versions(
                secret_id, project_id=project_id, client=client
            )
            destroyed += batch.get("destroyed", 0)
            if batch.get("errors"):
                errors.extend(batch["errors"][:1])
            if batch.get("destroyed", 0) == 0:
                break
            time.sleep(0.3)
        per_secret[secret_id] = destroyed
        total += destroyed
        log.info("PURGE_EXPIRED | env=%s secret=%s destroyed=%s", env, secret_id, destroyed)

    return {
        "environment": env,
        "destroyed": total,
        "per_secret": per_secret,
        "errors": errors[:10],
    }


def audit_oauth_env_secrets(env: str, project_id: str = PROJECT_ID) -> dict:
    """ENABLED version counts for the three token secrets of one environment."""
    try:
        client = _get_client(None)
    except ImportError as e:
        return {"environment": env, "all_ok": False, "error": str(e)}

    report: dict = {"environment": env}
    for secret_id in oauth_secret_ids_for_env(env):
        parent = f"projects/{project_id}/secrets/{secret_id}"
        try:
            enabled = _list_enabled_versions(client, parent)
            report[secret_id] = {
                "enabled_versions": len(enabled),
                "ok": len(enabled) <= 1,
                "missing": False,
            }
        except Exception as e:
            err = str(e)
            report[secret_id] = {
                "enabled_versions": 0,
                "ok": False,
                "missing": "not found" in err.lower() or "NOT_FOUND" in err,
                "error": err[:120],
            }
    report["all_ok"] = all(
        v.get("ok", False) and not v.get("missing", False)
        for k, v in report.items()
        if k not in ("environment", "all_ok", "error")
    )
    return report


def ensure_oauth_env_secret_policy(
    env: str,
    project_id: str = PROJECT_ID,
    max_retries: int = 2,
) -> dict:
    """
    Post-renewal gate: each env secret must have ≤1 ENABLED version.
    Re-runs prune_after_oauth_renewal if audit fails (handles SM API lag).
    """
    attempts = 0
    last_audit: dict = {}
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        last_audit = audit_oauth_env_secrets(env, project_id=project_id)
        if last_audit.get("all_ok"):
            log.info("OAUTH_POLICY_OK | env=%s attempts=%s", env, attempts)
            return {
                "environment": env,
                "ok": True,
                "attempts": attempts,
                "audit": last_audit,
            }
        if attempt < max_retries:
            log.warning(
                "OAUTH_POLICY_RETRY | env=%s attempt=%s audit=%s",
                env,
                attempts,
                last_audit,
            )
            prune_after_oauth_renewal(env, project_id=project_id)
            time.sleep(0.5)

    log.error(
        "OAUTH_POLICY_FAIL | env=%s still >1 ENABLED after %s attempts: %s",
        env,
        attempts,
        last_audit,
    )
    return {
        "environment": env,
        "ok": False,
        "attempts": attempts,
        "audit": last_audit,
    }


def prune_after_oauth_renewal(
    env: str,
    project_id: str = PROJECT_ID,
) -> dict:
    """After writing new tokens, ensure only 1 ENABLED version per token secret."""
    try:
        client = _get_client(None)
    except ImportError as e:
        return {"environment": env, "destroyed": 0, "errors": [str(e)]}

    total = 0
    per_secret = {}
    errors: List[str] = []
    for secret_id in oauth_secret_ids_for_env(env):
        result = prune_secret_until(
            secret_id, project_id=project_id, keep_latest=1, client=client
        )
        per_secret[secret_id] = result.get("destroyed", 0)
        total += result.get("destroyed", 0)
        errors.extend(result.get("errors", []))
    log.info("PRUNE_RENEWAL | env=%s total_destroyed=%s", env, total)
    return {
        "environment": env,
        "destroyed": total,
        "per_secret": per_secret,
        "errors": errors[:10],
    }


def prune_all_oauth_secrets(
    secret_ids: Optional[List[str]] = None,
    project_id: str = PROJECT_ID,
    keep_latest: int = 1,
    max_destroy_per_secret: int = 500,
) -> dict:
    """Scheduler / manual backlog drain for all OAuth token secrets."""
    secret_ids = secret_ids or ALL_OAUTH_TOKEN_SECRETS
    try:
        client = _get_client(None)
    except ImportError as e:
        return {"destroyed": 0, "errors": [str(e)], "per_secret": {}}

    total_destroyed = 0
    per_secret = {}
    errors: List[str] = []

    for secret_id in secret_ids:
        result = prune_secret_until(
            secret_id,
            project_id=project_id,
            keep_latest=keep_latest,
            max_destroy_per_round=max_destroy_per_secret,
            client=client,
        )
        secret_destroyed = result.get("destroyed", 0)
        per_secret[secret_id] = {"destroyed": secret_destroyed}
        total_destroyed += secret_destroyed
        errors.extend(result.get("errors", [])[:2])

    return {
        "destroyed": total_destroyed,
        "per_secret": per_secret,
        "errors": errors[:10],
        "estimated_monthly_savings_usd": round(total_destroyed * 0.06, 2),
    }


def _print_audit_report(report: dict) -> int:
    """Human-readable audit; returns exit code 0 if all_ok else 1."""
    import json
    import warnings

    warnings.filterwarnings("ignore", category=FutureWarning)

    if report.get("error"):
        print(json.dumps(report, indent=2))
        return 1

    print(json.dumps(report, indent=2))
    print()
    for secret_id in ALL_OAUTH_TOKEN_SECRETS:
        row = report.get(secret_id, {})
        if row.get("error"):
            print(f"  {secret_id}: ERROR — {row.get('error', '')[:80]}")
        elif row.get("missing"):
            print(f"  {secret_id}: MISSING — renew OAuth")
        elif row.get("ok"):
            print(f"  {secret_id}: OK (1 ENABLED version)")
        else:
            n = row.get("enabled_versions", "?")
            print(f"  {secret_id}: NEEDS CLEANUP ({n} ENABLED versions)")
    if report.get("all_ok"):
        print("\n✅ All token secrets OK (≤1 ENABLED each, none missing)")
        return 0
    print("\n⚠️  Run: bash scripts/cleanup_secrets_batch.sh --until-done")
    return 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OAuth token Secret Manager audit")
    parser.add_argument(
        "--audit",
        action="store_true",
        default=True,
        help="Print ENABLED version counts (default)",
    )
    parser.add_argument("--project", default=PROJECT_ID)
    args = parser.parse_args()
    raise SystemExit(_print_audit_report(audit_oauth_token_secrets(args.project)))
