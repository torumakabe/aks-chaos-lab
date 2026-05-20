#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
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
from collections.abc import Sequence
from typing import Any

SERVICE_GROUP_API_VERSION = "2024-02-01-preview"
SLI_API_VERSION = "2025-03-01-preview"
DATA_COLLECTION_RULE_ASSOCIATION_API_VERSION = "2024-03-11"


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
    if not isinstance(payload, dict):
        return []
    value = payload.get("value")
    return value if isinstance(value, list) else []


def json_id(payload: Any | None) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("id")
    return value if isinstance(value, str) else ""


def deployment_names(payload: Any | None, env_name: str, layer_name: str) -> list[str]:
    if not isinstance(payload, list):
        return []

    names: list[str] = []
    for deployment in payload:
        if not isinstance(deployment, dict):
            continue
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
    current = os.environ.get(name, "")
    if current and valid_env_value(current):
        return current

    if shutil.which("azd") is None:
        return ""

    value = command_output(
        ["azd", "env", "get-value", name],
        allow_failure=True,
        quiet_stderr=True,
    )
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


def discover_service_group_ids(env_name: str) -> list[str]:
    if not env_name:
        return []

    service_group_prefix = f"sg-aks-chaos-lab-{env_name}-"
    payload = command_json(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            (
                "https://management.azure.com/providers/Microsoft.Management/"
                f"serviceGroups?api-version={SERVICE_GROUP_API_VERSION}"
            ),
            "--output",
            "json",
        ],
        allow_failure=True,
        quiet_stderr=True,
    )

    service_group_ids: list[str] = []
    for service_group in json_items(payload):
        if not isinstance(service_group, dict):
            continue
        service_group_name = str(service_group.get("name", ""))
        service_group_id = str(service_group.get("id", ""))
        if (
            not service_group_name.startswith(service_group_prefix)
            or not service_group_id
        ):
            continue
        suffix = service_group_name.removeprefix(service_group_prefix)
        if not suffix or "-" in suffix:
            continue
        service_group_ids.append(service_group_id)
    return service_group_ids


def delete_service_group_sli_resources(
    service_group_id: str,
    *,
    dry_run: bool,
) -> None:
    service_group_url = resource_url(service_group_id)
    payload = command_json(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            f"{service_group_url}/providers/Microsoft.Monitor/slis?api-version={SLI_API_VERSION}",
            "--output",
            "json",
        ],
        allow_failure=True,
        quiet_stderr=True,
    )
    sli_ids = [
        item_id
        for item_id in (json_id(item) for item in json_items(payload))
        if item_id
    ]

    if sli_ids:
        for sli_id in sli_ids:
            log(f"deleting SLI {sli_id}")
            run_delete(
                [
                    "az",
                    "rest",
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
    run_delete(
        [
            "az",
            "rest",
            "--method",
            "delete",
            "--url",
            f"{service_group_url}?api-version={SERVICE_GROUP_API_VERSION}",
            "--output",
            "none",
        ],
        f"delete Service Group {service_group_id}",
        dry_run=dry_run,
    )


def delete_otlp_app_insights_dcra(
    resource_group: str,
    aks_cluster_name: str,
    *,
    dry_run: bool,
) -> None:
    if not resource_group or not aks_cluster_name:
        log(
            "AZURE_RESOURCE_GROUP or AZURE_AKS_CLUSTER_NAME is not set; skipping OTLP DCRA cleanup"
        )
        return

    payload = command_json(
        [
            "az",
            "resource",
            "show",
            "--resource-group",
            resource_group,
            "--resource-type",
            "Microsoft.ContainerService/managedClusters",
            "--name",
            aks_cluster_name,
            "--output",
            "json",
        ],
        allow_failure=True,
        quiet_stderr=True,
    )
    aks_cluster_id = json_id(payload)

    if not aks_cluster_id:
        log(
            f"AKS cluster {resource_group}/{aks_cluster_name} is already deleted or inaccessible"
        )
        return

    association_id = (
        f"{aks_cluster_id}/providers/Microsoft.Insights/"
        "dataCollectionRuleAssociations/OtlpAppInsightsExtension"
    )
    association_url = (
        f"{resource_url(association_id)}"
        f"?api-version={DATA_COLLECTION_RULE_ASSOCIATION_API_VERSION}"
    )

    payload = command_json(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            association_url,
            "--output",
            "json",
        ],
        allow_failure=True,
        quiet_stderr=True,
    )
    existing_association = json_id(payload)
    if not existing_association:
        log("OTLP App Insights DCRA is already deleted")
        return

    log(f"deleting OTLP App Insights DCRA {association_id}")
    run_delete(
        [
            "az",
            "rest",
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
        payload = command_json(
            [
                "az",
                "rest",
                "--method",
                "get",
                "--url",
                association_url,
                "--output",
                "json",
            ],
            allow_failure=True,
            quiet_stderr=True,
        )
        existing_association = json_id(payload)
        if not existing_association:
            log("OTLP App Insights DCRA deleted")
            return
        time.sleep(5)

    log("OTLP App Insights DCRA still exists after delete request")
    raise SystemExit(1)


def delete_sli_layer_deployment_records(env_name: str, *, dry_run: bool) -> None:
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
            "--output",
            "json",
        ],
        allow_failure=True,
        quiet_stderr=True,
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
                "--name",
                deployment_name,
                "--output",
                "none",
            ],
            allow_failure=True,
            quiet_stderr=True,
        )
        if completed.returncode != 0:
            log(f"failed to delete deployment record {deployment_name}; continuing")


def delete_base_resource_group_sync(
    env_name: str,
    resource_group: str,
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

    exists = (
        command_output(
            ["az", "group", "exists", "--name", resource_group, "--output", "tsv"],
            allow_failure=True,
            quiet_stderr=True,
        )
        or "unknown"
    )

    if exists.lower() != "true":
        log(
            f"base resource group {resource_group} is already deleted or inaccessible (exists={exists})"
        )
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
        last_exists = (
            command_output(
                ["az", "group", "exists", "--name", resource_group, "--output", "tsv"],
                allow_failure=True,
                quiet_stderr=True,
            )
            or "unknown"
        )
        if last_exists.lower() == "false":
            log(f"base resource group {resource_group} deleted (attempt {attempt})")
            return
        if attempt == 1 or attempt % 12 == 0:
            log(
                f"{resource_group} still deleting (attempt {attempt}/240, exists={last_exists})"
            )
        time.sleep(15)

    log(
        f"base resource group {resource_group} still exists after delete timeout; manual cleanup required"
    )
    raise SystemExit(1)


def delete_base_layer_deployment_records(env_name: str, *, dry_run: bool) -> None:
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
            "--output",
            "json",
        ],
        allow_failure=True,
        quiet_stderr=True,
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

    candidate_service_group_ids = (
        [service_group_id] if service_group_id else discover_service_group_ids(env_name)
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

    if not resource_group and env_name:
        resource_group = f"rg-aks-chaos-lab-{env_name}"

    if not aks_cluster_name and env_name:
        aks_cluster_name = f"aks-aks-chaos-lab-{env_name}"

    if service_group_ids:
        for current_service_group_id in service_group_ids:
            delete_service_group_sli_resources(
                current_service_group_id, dry_run=dry_run
            )
    else:
        log("no in-scope Service Group ID found; skipping Service Group scoped cleanup")

    if resource_group and not is_owned_resource_group(resource_group, env_name):
        log(
            "resource group is outside current env naming scope; "
            f"skipping RG-scoped cleanup: env={env_name}, resourceGroup={resource_group}"
        )
        resource_group = ""
        aks_cluster_name = ""

    delete_otlp_app_insights_dcra(resource_group, aks_cluster_name, dry_run=dry_run)

    # Eliminate the void deployment polling 404 risk by short-circuiting both layers'
    # Destroy paths. Order matters:
    #   1. SLI record first (sub-scope only, no RG)
    #   2. Base RG sync delete
    #   3. Base record last
    delete_sli_layer_deployment_records(env_name, dry_run=dry_run)
    delete_base_resource_group_sync(env_name, resource_group, dry_run=dry_run)
    delete_base_layer_deployment_records(env_name, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
