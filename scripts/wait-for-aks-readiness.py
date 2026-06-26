#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence

DEFAULT_NAMESPACE = "chaos-lab"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_POLL_SECONDS = 10


def resolve_command(args: Sequence[str]) -> list[str]:
    resolved_args = list(args)
    executable = shutil.which(resolved_args[0])
    if executable:
        resolved_args[0] = executable
    return resolved_args


def run_command(args: Sequence[str], *, allow_failure: bool = False) -> str:
    completed = subprocess.run(
        resolve_command(args),
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0 and not allow_failure:
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        raise SystemExit(completed.returncode)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        print(f"error: {command} is required", file=sys.stderr)
        raise SystemExit(1)


def get_env_value(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    if shutil.which("azd") is None:
        return ""
    return run_command(
        ["azd", "env", "get-value", name],
        allow_failure=True,
    )


def refresh_kubeconfig(resource_group: str, cluster_name: str) -> None:
    if not resource_group or not cluster_name:
        return
    run_command(
        [
            "az",
            "aks",
            "get-credentials",
            "--resource-group",
            resource_group,
            "--name",
            cluster_name,
            "--overwrite-existing",
            "--only-show-errors",
        ]
    )


def kubectl(args: Sequence[str]) -> str:
    return run_command(["kubectl", *args], allow_failure=True)


def can_i(verb: str, resource: str, namespace: str) -> bool:
    output = kubectl(["auth", "can-i", verb, resource, "-n", namespace])
    return output.strip().lower() == "yes"


def readiness_failures(namespace: str) -> list[str]:
    failures: list[str] = []
    if not kubectl(["get", "namespace", "default", "-o", "name"]):
        failures.append("Kubernetes API")
    required_permissions = (
        ("get", "poddisruptionbudgets.policy"),
        ("create", "deployments.apps"),
        ("patch", "deployments.apps"),
        ("get", "services"),
    )
    for verb, resource in required_permissions:
        if not can_i(verb, resource, namespace):
            failures.append(f"Azure RBAC {verb} {resource} in namespace {namespace}")
    return failures


def wait_until_ready(args: argparse.Namespace) -> None:
    deadline = time.monotonic() + args.timeout_seconds
    last_failures: list[str] = []
    while time.monotonic() < deadline:
        last_failures = readiness_failures(args.namespace)
        if not last_failures:
            print("ok: AKS Kubernetes API and Azure RBAC are ready")
            return
        print(
            "waiting for AKS readiness: " + ", ".join(last_failures),
            file=sys.stderr,
            flush=True,
        )
        time.sleep(args.poll_seconds)

    print(
        "error: AKS did not become ready before timeout: " + ", ".join(last_failures),
        file=sys.stderr,
    )
    raise SystemExit(1)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for AKS Kubernetes API and Azure RBAC readiness before applying manifests.",
    )
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    require_command("az")
    require_command("azd")
    require_command("kubectl")
    refresh_kubeconfig(
        get_env_value("AZURE_RESOURCE_GROUP"),
        get_env_value("AZURE_AKS_CLUSTER_NAME"),
    )
    wait_until_ready(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
