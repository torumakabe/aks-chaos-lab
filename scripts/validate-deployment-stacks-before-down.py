#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from deployment_stacks import enabled as stacks_enabled  # noqa: E402

SERVICE_GROUP_API_VERSION = "2024-02-01-preview"
SLI_API_VERSION = "2025-03-01-preview"


def log(message: str) -> None:
    print(f"[stack-down-check] {message}", file=sys.stderr, flush=True)


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        log(f"{command} is required")
        raise SystemExit(1)


def run_command(
    args: list[str], *, allow_failure: bool = False
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(args[0])
    command = [executable, *args[1:]] if executable else args
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode and not allow_failure:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(result.returncode)
    return result


def command_json(args: list[str]) -> Any:
    output = run_command(args).stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        log(f"failed to parse JSON output from {' '.join(args)}")
        raise SystemExit(1) from None


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
    result = run_command(["azd", "env", "get-value", name], allow_failure=True)
    value = result.stdout.strip()
    return value if result.returncode == 0 and valid_env_value(value) else ""


def resource_url(resource_id: str) -> str:
    if resource_id.startswith("https://management.azure.com/"):
        return resource_id
    if resource_id.startswith("/"):
        return f"https://management.azure.com{resource_id}"
    return resource_id


def json_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("id")
    return value if isinstance(value, str) else ""


def is_owned_service_group_id(service_group_id: str, env_name: str) -> bool:
    prefix_path = (
        "https://management.azure.com/providers/Microsoft.Management/serviceGroups/"
    )
    url = resource_url(service_group_id).rstrip("/")
    if not url.lower().startswith(prefix_path.lower()):
        return False
    name = url[len(prefix_path) :]
    if not name or "/" in name or "?" in name or "#" in name:
        return False
    prefix = f"sg-aks-chaos-lab-{env_name}-"
    suffix = name.removeprefix(prefix)
    return name.startswith(prefix) and bool(suffix) and "-" not in suffix


def resource_group_exists(resource_group: str, subscription_id: str) -> bool:
    result = run_command(
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
        ]
    )
    value = result.stdout.strip().lower()
    if value not in {"true", "false"}:
        log(f"invalid resource group existence response: {value}")
        raise SystemExit(1)
    return value == "true"


def service_group_exists(service_group_id: str, subscription_id: str) -> bool:
    service_group_url = resource_url(service_group_id).rstrip("/")
    result = run_command(
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
    if result.returncode:
        prefix = "ERROR: Not Found("
        message = result.stderr.strip()
        if message.startswith(prefix) and message.endswith(")"):
            try:
                details = json.loads(message[len(prefix) : -1])
            except json.JSONDecodeError:
                log("invalid Service Group error response")
            else:
                error = details.get("error") if isinstance(details, dict) else None
                if isinstance(error, dict) and error.get("code") == "ResourceNotFound":
                    return False
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(result.returncode)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        log("invalid Service Group JSON response")
        raise SystemExit(1) from None
    if resource_url(json_id(payload)).lower() != service_group_url.lower():
        log(f"Service Group response ID does not match {service_group_id}")
        raise SystemExit(1)
    return True


def service_group_has_slis(service_group_id: str, subscription_id: str) -> bool:
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
        ]
    )
    items = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        log("invalid Service Group SLI list response")
        raise SystemExit(1)
    prefix = f"{service_group_url}/providers/Microsoft.Monitor/slis/"
    for item in items:
        sli_id = json_id(item)
        sli_url = resource_url(sli_id)
        suffix = sli_url[len(prefix) :]
        if (
            not sli_url.lower().startswith(prefix.lower())
            or not suffix
            or any(char in suffix for char in "/?#")
        ):
            log(f"SLI ID is outside the selected Service Group: {sli_id}")
            raise SystemExit(1)
    return bool(items)


def validate_stack_teardown(
    env_name: str, subscription_id: str, resource_group: str
) -> None:
    if not env_name:
        log("AZURE_ENV_NAME is required for Stack discovery")
        raise SystemExit(1)
    try:
        if not stacks_enabled():
            log("enable alpha.deployment.stacks before deleting an environment")
            raise SystemExit(1)
    except (RuntimeError, OSError) as error:
        log(str(error))
        raise SystemExit(1) from error

    stacks = command_json(
        [
            "az",
            "stack",
            "sub",
            "list",
            "--subscription",
            subscription_id,
            "--output",
            "json",
        ]
    )
    if not isinstance(stacks, list):
        log("invalid Stack list response")
        raise SystemExit(1)

    found: set[str] = set()
    service_group_ids: set[str] = set()
    for stack in stacks:
        if not isinstance(stack, dict):
            log("invalid Stack list entry")
            raise SystemExit(1)
        for layer in ("base", "sli"):
            if stack.get("name") != f"azd-stack-{env_name}-{layer}":
                continue
            tags = stack.get("tags")
            if not isinstance(tags, dict) or (
                tags.get("azd-env-name") != env_name
                or tags.get("azd-layer-name") != layer
            ):
                log("Stack tags do not match the selected environment and layer")
                raise SystemExit(1)
            found.add(layer)
            if layer == "base":
                resources = stack.get("resources") or []
                if not isinstance(resources, list):
                    log("invalid Stack resources response")
                    raise SystemExit(1)
                for resource in resources:
                    resource_id = json_id(resource)
                    if not resource_id:
                        log("invalid Stack resource ID")
                        raise SystemExit(1)
                    if is_owned_service_group_id(resource_id, env_name):
                        service_group_ids.add(resource_id)

    if "sli" in found and "base" not in found:
        log("base Stack is missing; complete the base migration before azd down")
        raise SystemExit(1)
    if "base" not in found and resource_group_exists(resource_group, subscription_id):
        log("base Stack is missing; import the existing environment before azd down")
        raise SystemExit(1)
    if "base" in found and "sli" not in found:
        for service_group_id in service_group_ids:
            if service_group_exists(service_group_id, subscription_id) and (
                service_group_has_slis(service_group_id, subscription_id)
            ):
                log(
                    "SLI resources exist without a sli Stack; complete the sli migration before azd down"
                )
                raise SystemExit(1)


def main() -> int:
    require_command("az")
    subscription_id = get_env_value("AZURE_SUBSCRIPTION_ID")
    if not subscription_id:
        log("AZURE_SUBSCRIPTION_ID is required")
        return 1
    env_name = get_env_value("AZURE_ENV_NAME")
    resource_group = get_env_value("AZURE_RESOURCE_GROUP") or (
        f"rg-aks-chaos-lab-{env_name}"
    )
    if env_name and resource_group != f"rg-aks-chaos-lab-{env_name}":
        log(
            "resource group is outside current env naming scope; "
            f"env={env_name}, resourceGroup={resource_group}"
        )
        return 1
    validate_stack_teardown(env_name, subscription_id, resource_group)
    log("Stack teardown preconditions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
