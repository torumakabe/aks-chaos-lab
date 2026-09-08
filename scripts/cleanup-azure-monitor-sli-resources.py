#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from email.message import Message
from http.client import HTTPException
from typing import Any

SERVICE_GROUP_API_VERSION = "2024-02-01-preview"
SLI_API_VERSION = "2025-03-01-preview"
DATA_COLLECTION_RULE_ASSOCIATION_API_VERSION = "2024-03-11"
SERVICE_GROUP_DELETE_TIMEOUT_SECONDS = 600


def log(message: str) -> None:
    print(f"[sli-cleanup] {message}", file=sys.stderr, flush=True)


def env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        log(f"{command} is required")
        raise SystemExit(1)


def resolve_command(args: Sequence[str]) -> list[str]:
    resolved_args = list(args)
    executable = shutil.which(resolved_args[0])
    if executable:
        resolved_args[0] = executable
    return resolved_args


def run_command(
    args: Sequence[str],
    *,
    allow_failure: bool = False,
    quiet_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        resolve_command(args),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if quiet_stderr else subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0 and not allow_failure:
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        raise SystemExit(completed.returncode)
    return completed


def command_output(
    args: Sequence[str],
    *,
    allow_failure: bool = False,
    quiet_stderr: bool = False,
) -> str:
    return run_command(
        args,
        allow_failure=allow_failure,
        quiet_stderr=quiet_stderr,
    ).stdout.strip()


def command_json(
    args: Sequence[str],
    *,
    allow_failure: bool = False,
    quiet_stderr: bool = False,
) -> Any | None:
    output = command_output(
        args,
        allow_failure=allow_failure,
        quiet_stderr=quiet_stderr,
    )
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        if allow_failure:
            return None
        log(f"failed to parse JSON output from {' '.join(args)}")
        raise SystemExit(1) from None


def json_items(payload: Any | None) -> list[Any]:
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, list) or any(not json_id(item) for item in value):
        log("invalid resource list response: expected value array with resource IDs")
        raise SystemExit(1)
    return value


def json_id(payload: Any | None) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("id")
    return value if isinstance(value, str) else ""


def deployment_names(payload: Any | None, env_name: str, layer_name: str) -> list[str]:
    if not isinstance(payload, list):
        log("invalid deployment list response")
        raise SystemExit(1)

    names: list[str] = []
    for deployment in payload:
        if not isinstance(deployment, dict):
            log("invalid deployment list entry")
            raise SystemExit(1)
        tags = deployment.get("tags")
        name = deployment.get("name")
        if (
            isinstance(tags, dict)
            and isinstance(name, str)
            and tags.get("azd-env-name") == env_name
            and tags.get("azd-layer-name") == layer_name
        ):
            names.append(name)
    return names


def valid_env_value(value: str) -> bool:
    return not (
        value.startswith("ERROR:") or "key not found" in value or "Suggestion:" in value
    )


def get_env_value(name: str) -> str:
    current = os.environ.get(name, "").strip()
    if name == "AZURE_SUBSCRIPTION_ID" and name in os.environ:
        return current if valid_env_value(current) else ""
    if current and valid_env_value(current):
        return current

    if shutil.which("azd") is None:
        return ""

    completed = run_command(
        ["azd", "env", "get-value", name],
        allow_failure=True,
        quiet_stderr=True,
    )
    if completed.returncode != 0:
        return ""
    value = completed.stdout.strip()
    if value and valid_env_value(value):
        return value
    return ""


def resource_url(resource_id: str) -> str:
    if resource_id.startswith("https://management.azure.com/"):
        return resource_id
    if resource_id.startswith("/"):
        return f"https://management.azure.com{resource_id}"
    return resource_id


def service_group_name_from_id(service_group_id: str) -> str:
    return service_group_id.rstrip("/").split("/")[-1]


def is_owned_service_group_id(service_group_id: str, env_name: str) -> bool:
    if not env_name:
        return False
    prefix_path = (
        "https://management.azure.com/providers/Microsoft.Management/serviceGroups/"
    )
    url = resource_url(service_group_id).rstrip("/")
    if not url.lower().startswith(prefix_path.lower()):
        return False
    if "/" in url[len(prefix_path) :] or "?" in url or "#" in url:
        return False
    service_group_name = service_group_name_from_id(service_group_id)
    prefix = f"sg-aks-chaos-lab-{env_name}-"
    if not service_group_name.startswith(prefix):
        return False
    suffix = service_group_name.removeprefix(prefix)
    return bool(suffix) and "-" not in suffix


def is_owned_resource_group(resource_group: str, env_name: str) -> bool:
    return bool(env_name) and resource_group == f"rg-aks-chaos-lab-{env_name}"


def run_delete(args: Sequence[str], description: str, *, dry_run: bool) -> None:
    if dry_run:
        log(f"dry-run: would {description}")
        return
    run_command(args)


def resource_group_exists(resource_group: str, subscription_id: str) -> bool:
    exists = command_output(
        [
            "az",
            "group",
            "exists",
            "--subscription",
            subscription_id,
            "--name",
            resource_group,
            "--output",
            "tsv",
        ],
    ).lower()
    if exists not in {"true", "false"}:
        log(
            f"failed to determine whether base resource group {resource_group} exists: {exists}"
        )
        raise SystemExit(1)
    return exists == "true"


# Service Group API limitations and removal criteria: docs/workarounds.md A-8.
def discover_service_group_ids(env_name: str, subscription_id: str) -> list[str]:
    if not env_name:
        return []

    payload = command_json(
        [
            "az",
            "deployment",
            "sub",
            "list",
            "--subscription",
            subscription_id,
            "--output",
            "json",
        ],
    )

    service_group_ids: set[str] = set()
    for deployment_name in deployment_names(payload, env_name, "base"):
        ids = command_json(
            [
                "az",
                "deployment",
                "operation",
                "sub",
                "list",
                "--subscription",
                subscription_id,
                "--name",
                deployment_name,
                "--query",
                "[?properties.targetResource.resourceType=='Microsoft.Management/serviceGroups'].properties.targetResource.id",
                "--output",
                "json",
            ],
        )
        if not isinstance(ids, list) or any(
            not isinstance(item, str) or not item for item in ids
        ):
            log(
                f"invalid Service Group IDs in deployment operations: {deployment_name}"
            )
            raise SystemExit(1)
        service_group_ids.update(ids)
    return sorted(service_group_ids)


def service_group_exists(service_group_id: str, subscription_id: str) -> bool:
    service_group_url = resource_url(service_group_id).rstrip("/")
    completed = run_command(
        [
            "az",
            "rest",
            "--subscription",
            subscription_id,
            "--method",
            "get",
            "--url",
            f"{service_group_url}?api-version={SERVICE_GROUP_API_VERSION}",
            "--output",
            "json",
            "--only-show-errors",
        ],
        allow_failure=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip()
        prefix = "ERROR: Not Found("
        if message.startswith(prefix) and message.endswith(")"):
            try:
                details = json.loads(message[len(prefix) : -1])
            except json.JSONDecodeError:
                log("invalid Service Group error response")
            else:
                error = details.get("error") if isinstance(details, dict) else None
                if isinstance(error, dict) and error.get("code") == "ResourceNotFound":
                    return False
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        raise SystemExit(completed.returncode)

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        log("invalid Service Group JSON response")
        raise SystemExit(1) from None
    if resource_url(json_id(payload)).lower() != service_group_url.lower():
        log(f"Service Group response ID does not match {service_group_id}")
        raise SystemExit(1)
    return True


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


def delete_service_group_and_wait(
    service_group_id: str, subscription_id: str, *, dry_run: bool
) -> None:
    if dry_run:
        log(f"dry-run: would delete Service Group {service_group_id}")
        return

    token = command_output(
        [
            "az",
            "account",
            "get-access-token",
            "--subscription",
            subscription_id,
            "--resource",
            "https://management.azure.com/",
            "--query",
            "accessToken",
            "--output",
            "tsv",
        ],
    )
    if not token or any(character.isspace() for character in token):
        log("invalid ARM access token response")
        raise SystemExit(1)

    opener = urllib.request.build_opener(NoRedirectHandler())
    deadline = time.monotonic() + SERVICE_GROUP_DELETE_TIMEOUT_SECONDS

    def request(method: str, url: str) -> tuple[int, Message]:
        if not url.lower().startswith("https://management.azure.com/") or any(
            character in url for character in "\r\n#"
        ):
            log("invalid Service Group deletion URL; expected an ARM HTTPS URL")
            raise SystemExit(1)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log("Service Group deletion timed out")
            raise SystemExit(1)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}, method=method
        )
        try:
            with opener.open(req, timeout=min(60, remaining)) as response:
                return response.status, response.headers
        except urllib.error.HTTPError as error:
            log(f"Service Group deletion {method} failed: HTTP {error.code}")
            raise SystemExit(1) from None
        except (OSError, HTTPException) as error:
            log(f"Service Group deletion {method} failed: {error}")
            raise SystemExit(1) from None

    delete_url = (
        f"{resource_url(service_group_id).rstrip('/')}"
        f"?api-version={SERVICE_GROUP_API_VERSION}"
    )
    status, headers = request("DELETE", delete_url)
    if status == 204:
        return
    if status != 202:
        log(f"unexpected Service Group DELETE response: HTTP {status}")
        raise SystemExit(1)
    location = (headers.get("Location") or "").strip()
    if not location:
        log("Service Group DELETE returned 202 without Location")
        raise SystemExit(1)
    try:
        status_url = urllib.parse.urljoin(delete_url, location)
    except ValueError:
        log("invalid Service Group deletion Location")
        raise SystemExit(1) from None

    while True:
        try:
            delay = int(headers.get("Retry-After", "5"))
        except ValueError:
            log("invalid Service Group Retry-After header")
            raise SystemExit(1) from None
        if delay < 0:
            log("invalid Service Group Retry-After header")
            raise SystemExit(1)
        time.sleep(min(max(1, delay), max(0, deadline - time.monotonic())))
        status, headers = request("GET", status_url)
        if status in {200, 204}:
            log("Service Group deletion completed")
            return
        if status != 202:
            log(f"unexpected Service Group deletion status: HTTP {status}")
            raise SystemExit(1)


def delete_service_group_sli_resources(
    service_group_id: str,
    subscription_id: str,
    *,
    dry_run: bool,
) -> None:
    if not service_group_exists(service_group_id, subscription_id):
        log(f"Service Group {service_group_id} is already deleted")
        return

    service_group_url = resource_url(service_group_id).rstrip("/")
    payload = command_json(
        [
            "az",
            "rest",
            "--subscription",
            subscription_id,
            "--method",
            "get",
            "--url",
            f"{service_group_url}/providers/Microsoft.Monitor/slis?api-version={SLI_API_VERSION}",
            "--output",
            "json",
        ],
    )
    sli_ids = [
        item_id
        for item_id in (json_id(item) for item in json_items(payload))
        if item_id
    ]
    sli_prefix = f"{service_group_url.rstrip('/')}/providers/Microsoft.Monitor/slis/"
    for sli_id in sli_ids:
        sli_url = resource_url(sli_id)
        suffix = sli_url[len(sli_prefix) :]
        if (
            not sli_url.lower().startswith(sli_prefix.lower())
            or not suffix
            or any(char in suffix for char in "/?#")
        ):
            log(f"SLI ID is outside the selected Service Group: {sli_id}")
            raise SystemExit(1)

    if sli_ids:
        for sli_id in sli_ids:
            log(f"deleting SLI {sli_id}")
            run_delete(
                [
                    "az",
                    "rest",
                    "--subscription",
                    subscription_id,
                    "--method",
                    "delete",
                    "--url",
                    f"{resource_url(sli_id)}?api-version={SLI_API_VERSION}",
                    "--output",
                    "none",
                ],
                f"delete SLI {sli_id}",
                dry_run=dry_run,
            )
    else:
        log("no Service Group scoped SLI resources found")

    log(f"deleting Service Group {service_group_id}")
    delete_service_group_and_wait(service_group_id, subscription_id, dry_run=dry_run)


def otlp_app_insights_dcra_exists(association_id: str, subscription_id: str) -> bool:
    association_url = resource_url(association_id)
    collection_url = association_url.rsplit("/", 1)[0]
    url = f"{collection_url}?api-version={DATA_COLLECTION_RULE_ASSOCIATION_API_VERSION}"
    while True:
        payload = command_json(
            [
                "az",
                "rest",
                "--subscription",
                subscription_id,
                "--method",
                "get",
                "--url",
                url,
                "--output",
                "json",
            ],
        )
        if not isinstance(payload, dict):
            log("invalid DCRA list response: expected an object")
            raise SystemExit(1)
        if any(
            resource_url(json_id(item)).lower() == association_url.lower()
            for item in json_items(payload)
        ):
            return True
        next_link = payload.get("nextLink")
        if next_link is None or next_link == "":
            return False
        if not isinstance(next_link, str) or not resource_url(
            next_link
        ).lower().startswith(f"{collection_url}?".lower()):
            log("invalid DCRA list nextLink")
            raise SystemExit(1)
        url = resource_url(next_link)


def delete_otlp_app_insights_dcra(
    resource_group: str,
    aks_cluster_name: str,
    subscription_id: str,
    *,
    dry_run: bool,
) -> None:
    if not resource_group or not aks_cluster_name:
        log(
            "AZURE_RESOURCE_GROUP or AZURE_AKS_CLUSTER_NAME is not set; skipping OTLP DCRA cleanup"
        )
        return

    if not resource_group_exists(resource_group, subscription_id):
        log(f"base resource group {resource_group} is already deleted")
        return

    payload = command_json(
        [
            "az",
            "resource",
            "list",
            "--subscription",
            subscription_id,
            "--resource-group",
            resource_group,
            "--resource-type",
            "Microsoft.ContainerService/managedClusters",
            "--name",
            aks_cluster_name,
            "--output",
            "json",
        ],
    )
    if (
        not isinstance(payload, list)
        or len(payload) > 1
        or any(not json_id(item) for item in payload)
    ):
        log("invalid AKS list response: expected at most one cluster with an ID")
        raise SystemExit(1)
    if not payload:
        log(f"AKS cluster {resource_group}/{aks_cluster_name} is already deleted")
        return
    aks_cluster_id = json_id(payload[0])
    expected_cluster_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.ContainerService/managedClusters/{aks_cluster_name}"
    )
    if (
        resource_url(aks_cluster_id).lower()
        != resource_url(expected_cluster_id).lower()
    ):
        log(
            f"AKS ID does not match the selected subscription and cluster: {aks_cluster_id}"
        )
        raise SystemExit(1)

    association_id = (
        f"{aks_cluster_id}/providers/Microsoft.Insights/"
        "dataCollectionRuleAssociations/OtlpAppInsightsExtension"
    )
    association_url = (
        f"{resource_url(association_id)}"
        f"?api-version={DATA_COLLECTION_RULE_ASSOCIATION_API_VERSION}"
    )

    if not otlp_app_insights_dcra_exists(association_id, subscription_id):
        log("OTLP App Insights DCRA is already deleted")
        return

    log(f"deleting OTLP App Insights DCRA {association_id}")
    run_delete(
        [
            "az",
            "rest",
            "--subscription",
            subscription_id,
            "--method",
            "delete",
            "--url",
            association_url,
            "--output",
            "none",
        ],
        f"delete OTLP App Insights DCRA {association_id}",
        dry_run=dry_run,
    )
    if dry_run:
        return

    for _attempt in range(1, 13):
        if not otlp_app_insights_dcra_exists(association_id, subscription_id):
            log("OTLP App Insights DCRA deleted")
            return
        time.sleep(5)

    log("OTLP App Insights DCRA still exists after delete request")
    raise SystemExit(1)


def delete_sli_layer_deployment_records(
    env_name: str, subscription_id: str, *, dry_run: bool
) -> None:
    if not env_flag("AZURE_MONITOR_SLI_FIX_SLI_VOID", default=True):
        log(
            "AZURE_MONITOR_SLI_FIX_SLI_VOID=false; skipping SLI layer void prevention (evidence-collection mode)"
        )
        return

    if not env_name:
        log(
            "AZURE_ENV_NAME is not set; skipping SLI sub-scope deployment record cleanup"
        )
        return

    payload = command_json(
        [
            "az",
            "deployment",
            "sub",
            "list",
            "--subscription",
            subscription_id,
            "--output",
            "json",
        ],
    )
    sli_deployments = deployment_names(payload, env_name, "sli")

    if not sli_deployments:
        log(f"no SLI sub-scope deployment record found for env {env_name}")
        return

    for deployment_name in sli_deployments:
        log(f"deleting SLI sub-scope deployment record {deployment_name}")
        if dry_run:
            log(f"dry-run: would delete SLI deployment record {deployment_name}")
            continue
        completed = run_command(
            [
                "az",
                "deployment",
                "sub",
                "delete",
                "--subscription",
                subscription_id,
                "--name",
                deployment_name,
                "--output",
                "none",
            ],
            allow_failure=True,
            quiet_stderr=True,
        )
        if completed.returncode != 0:
            log(f"failed to delete deployment record {deployment_name}")
            raise SystemExit(1)


def delete_base_resource_group_sync(
    env_name: str,
    resource_group: str,
    subscription_id: str,
    *,
    dry_run: bool,
) -> None:
    if not env_flag("AZURE_MONITOR_SLI_FIX_BASE_VOID", default=True):
        log(
            "AZURE_MONITOR_SLI_FIX_BASE_VOID=false; skipping base RG sync delete (evidence-collection mode)"
        )
        return

    if not resource_group and env_name:
        resource_group = f"rg-aks-chaos-lab-{env_name}"

    if not resource_group:
        log(
            "AZURE_RESOURCE_GROUP and AZURE_ENV_NAME are not set; skipping base RG sync delete"
        )
        return

    if not resource_group_exists(resource_group, subscription_id):
        log(f"base resource group {resource_group} is already deleted")
        return

    log(f"deleting base resource group {resource_group} (synchronous)")
    if dry_run:
        log(f"dry-run: would delete base resource group {resource_group}")
        return

    completed = run_command(
        [
            "az",
            "group",
            "delete",
            "--subscription",
            subscription_id,
            "--name",
            resource_group,
            "--yes",
            "--no-wait",
            "--output",
            "none",
        ],
        allow_failure=True,
        quiet_stderr=True,
    )
    if completed.returncode != 0:
        log(f"failed to initiate base RG deletion for {resource_group}")
        raise SystemExit(1)

    for attempt in range(1, 241):
        if not resource_group_exists(resource_group, subscription_id):
            log(f"base resource group {resource_group} deleted (attempt {attempt})")
            return
        if attempt == 1 or attempt % 12 == 0:
            log(f"{resource_group} still deleting (attempt {attempt}/240)")
        time.sleep(15)

    log(
        f"base resource group {resource_group} still exists after delete timeout; manual cleanup required"
    )
    raise SystemExit(1)


def delete_base_layer_deployment_records(
    env_name: str, subscription_id: str, *, dry_run: bool
) -> None:
    if not env_flag("AZURE_MONITOR_SLI_FIX_BASE_VOID", default=True):
        log(
            "AZURE_MONITOR_SLI_FIX_BASE_VOID=false; skipping base layer void prevention (evidence-collection mode)"
        )
        return

    if not env_name:
        log(
            "AZURE_ENV_NAME is not set; skipping base sub-scope deployment record cleanup"
        )
        return

    payload = command_json(
        [
            "az",
            "deployment",
            "sub",
            "list",
            "--subscription",
            subscription_id,
            "--output",
            "json",
        ],
    )
    base_deployments = deployment_names(payload, env_name, "base")

    if not base_deployments:
        log(f"no base sub-scope deployment record found for env {env_name}")
        return

    for deployment_name in base_deployments:
        log(f"deleting base sub-scope deployment record {deployment_name}")
        if dry_run:
            log(f"dry-run: would delete base deployment record {deployment_name}")
            continue
        completed = run_command(
            [
                "az",
                "deployment",
                "sub",
                "delete",
                "--subscription",
                subscription_id,
                "--name",
                deployment_name,
                "--output",
                "none",
            ],
            allow_failure=True,
            quiet_stderr=True,
        )
        if completed.returncode != 0:
            log(f"failed to delete base deployment record {deployment_name}")
            raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean up Azure Monitor SLI resources before azd down.",
    )
    parser.add_argument("phase", nargs="?", default="pre")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover targets and log delete operations without deleting resources.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.phase != "pre":
        log(f"unknown cleanup phase: {args.phase} (expected 'pre')")
        return 1

    dry_run = args.dry_run or env_flag("AZURE_MONITOR_SLI_CLEANUP_DRY_RUN")
    require_command("az")
    try:
        subscription_id = get_env_value("AZURE_SUBSCRIPTION_ID")
    except OSError as exc:
        log(f"failed to obtain AZURE_SUBSCRIPTION_ID: {exc}")
        return 1
    if not subscription_id:
        log(
            "AZURE_SUBSCRIPTION_ID is required; it is missing, empty, or could not be retrieved"
        )
        return 1

    # azd hooks cannot pass arguments or inline environment assignments when using
    # Python file execution. Default to the same auto-confirmed behavior that the
    # previous azure.yaml inline shell hook provided.
    if not env_flag("CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES", default=True):
        if env_flag("AZURE_MONITOR_SLI_CLEANUP_SKIP_UNCONFIRMED"):
            log(
                "CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES is not true; skipping cleanup"
            )
            return 0
        log("set CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES=true to delete resources")
        return 1

    if dry_run:
        log("dry-run enabled; delete operations will be skipped")

    env_name = get_env_value("AZURE_ENV_NAME")
    service_group_id = get_env_value("AZURE_MONITOR_SLI_SERVICE_GROUP_ID")
    service_group_name = get_env_value("AZURE_MONITOR_SLI_SERVICE_GROUP_NAME")
    resource_group = get_env_value("AZURE_RESOURCE_GROUP")
    aks_cluster_name = get_env_value("AZURE_AKS_CLUSTER_NAME")

    if not service_group_id and service_group_name:
        service_group_id = (
            f"/providers/Microsoft.Management/serviceGroups/{service_group_name}"
        )

    service_group_failed = False
    try:
        candidate_service_group_ids = (
            [service_group_id]
            if service_group_id
            else discover_service_group_ids(env_name, subscription_id)
        )
        service_group_ids = [
            candidate_service_group_id
            for candidate_service_group_id in candidate_service_group_ids
            if is_owned_service_group_id(candidate_service_group_id, env_name)
        ]
        skipped_service_group_ids = sorted(
            set(candidate_service_group_ids) - set(service_group_ids)
        )
        for skipped_service_group_id in skipped_service_group_ids:
            log(
                "skipping Service Group outside current env naming scope: "
                f"env={env_name}, serviceGroup={skipped_service_group_id}"
            )

        if service_group_ids:
            for current_service_group_id in service_group_ids:
                delete_service_group_sli_resources(
                    current_service_group_id, subscription_id, dry_run=dry_run
                )
        else:
            log(
                "no in-scope Service Group ID found; skipping Service Group scoped cleanup"
            )
    except SystemExit:
        service_group_failed = True
        log("Service Group cleanup failed; continuing subscription-scoped cleanup")

    if not resource_group and env_name:
        resource_group = f"rg-aks-chaos-lab-{env_name}"

    if not aks_cluster_name and env_name:
        aks_cluster_name = f"aks-aks-chaos-lab-{env_name}"

    if resource_group and not is_owned_resource_group(resource_group, env_name):
        log(
            "resource group is outside current env naming scope; "
            f"stopping RG-scoped cleanup: env={env_name}, resourceGroup={resource_group}"
        )
        return 1

    delete_otlp_app_insights_dcra(
        resource_group, aks_cluster_name, subscription_id, dry_run=dry_run
    )

    # Eliminate the void deployment polling 404 risk by short-circuiting both layers'
    # Destroy paths. Order matters:
    #   1. SLI record first (sub-scope only, no RG)
    #   2. Base RG sync delete
    #   3. Base record last, retained if Service Group cleanup needs a retry
    delete_sli_layer_deployment_records(env_name, subscription_id, dry_run=dry_run)
    delete_base_resource_group_sync(
        env_name, resource_group, subscription_id, dry_run=dry_run
    )
    if service_group_failed:
        log("retaining base deployment records for Service Group cleanup retry")
        return 1
    delete_base_layer_deployment_records(env_name, subscription_id, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
