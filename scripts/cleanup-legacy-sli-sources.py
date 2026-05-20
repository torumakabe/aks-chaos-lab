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
from collections.abc import Sequence

SLI_API_VERSION = "2025-03-01-preview"


def log(message: str) -> None:
    print(f"[legacy-sli-cleanup] {message}", file=sys.stderr, flush=True)


def resolve_command(args: Sequence[str]) -> list[str]:
    resolved_args = list(args)
    executable = shutil.which(resolved_args[0])
    if executable:
        resolved_args[0] = executable
    return resolved_args


def run_command(args: Sequence[str], *, dry_run: bool) -> None:
    if dry_run:
        log(f"dry-run: {' '.join(args)}")
        return

    completed = subprocess.run(resolve_command(args), check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def command_output(
    args: Sequence[str],
    *,
    allow_failure: bool = False,
    quiet_stderr: bool = False,
) -> str:
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
    return completed.stdout.strip()


def resource_exists(args: Sequence[str]) -> bool:
    return bool(command_output(args, allow_failure=True, quiet_stderr=True))


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
    return value if value and valid_env_value(value) else ""


def resource_url(resource_id: str) -> str:
    if resource_id.startswith("https://management.azure.com/"):
        return resource_id
    if resource_id.startswith("/"):
        return f"https://management.azure.com{resource_id}"
    return resource_id


def delete_kubernetes_legacy_resources(*, dry_run: bool) -> None:
    if shutil.which("kubectl") is None:
        log("kubectl not found; skipping Kubernetes legacy cleanup")
        return

    run_command(
        [
            "kubectl",
            "-n",
            "chaos-lab",
            "delete",
            "cronjob/synthetic-traffic",
            "serviceaccount/synthetic-traffic-sa",
            "ciliumnetworkpolicy/synthetic-traffic-egress-allowlist",
            "--ignore-not-found=true",
        ],
        dry_run=dry_run,
    )


def delete_legacy_prometheus_alert_group(*, dry_run: bool) -> None:
    resource_group = get_env_value("AZURE_RESOURCE_GROUP")
    aks_name = get_env_value("AZURE_AKS_CLUSTER_NAME")
    if not resource_group or not aks_name:
        log(
            "AZURE_RESOURCE_GROUP or AZURE_AKS_CLUSTER_NAME missing; skipping legacy alert group cleanup"
        )
        return

    alert_group_name = f"app-operational-alerts-{aks_name}"
    show_args = [
        "az",
        "resource",
        "show",
        "--resource-group",
        resource_group,
        "--namespace",
        "Microsoft.AlertsManagement",
        "--resource-type",
        "prometheusRuleGroups",
        "--name",
        alert_group_name,
        "--query",
        "id",
        "--output",
        "tsv",
    ]
    if not dry_run and not resource_exists(show_args):
        log(f"legacy Prometheus alert group not found; skipping {alert_group_name}")
        return

    run_command(
        [
            "az",
            "resource",
            "delete",
            "--resource-group",
            resource_group,
            "--namespace",
            "Microsoft.AlertsManagement",
            "--resource-type",
            "prometheusRuleGroups",
            "--name",
            alert_group_name,
        ],
        dry_run=dry_run,
    )


def delete_legacy_availability_test(*, dry_run: bool) -> None:
    resource_group = get_env_value("AZURE_RESOURCE_GROUP")
    probe_name = get_env_value("AZURE_EXTERNAL_SLI_PROBE_NAME") or get_env_value(
        "AZURE_EXTERNAL_SLI_AVAILABILITY_TEST_NAME"
    )
    if not resource_group or not probe_name:
        log(
            "AZURE_RESOURCE_GROUP or AZURE_EXTERNAL_SLI_PROBE_NAME missing; skipping availability test cleanup"
        )
        return

    show_args = [
        "az",
        "resource",
        "show",
        "--resource-group",
        resource_group,
        "--namespace",
        "Microsoft.Insights",
        "--resource-type",
        "webtests",
        "--name",
        probe_name,
        "--query",
        "id",
        "--output",
        "tsv",
    ]
    if not dry_run and not resource_exists(show_args):
        log(f"legacy availability test not found; skipping {probe_name}")
        return

    run_command(
        [
            "az",
            "resource",
            "delete",
            "--resource-group",
            resource_group,
            "--namespace",
            "Microsoft.Insights",
            "--resource-type",
            "webtests",
            "--name",
            probe_name,
        ],
        dry_run=dry_run,
    )


def delete_legacy_smart_detector_alert(*, dry_run: bool) -> None:
    resource_group = get_env_value("AZURE_RESOURCE_GROUP")
    env_name = get_env_value("AZURE_ENV_NAME")
    if not resource_group or not env_name:
        log(
            "AZURE_RESOURCE_GROUP or AZURE_ENV_NAME missing; skipping legacy smart detector cleanup"
        )
        return

    detector_name = f"Failure Anomalies - appi-otlp-chaos-lab-{env_name}"
    show_args = [
        "az",
        "resource",
        "show",
        "--resource-group",
        resource_group,
        "--namespace",
        "microsoft.alertsmanagement",
        "--resource-type",
        "smartDetectorAlertRules",
        "--name",
        detector_name,
        "--query",
        "id",
        "--output",
        "tsv",
    ]
    if not dry_run and not resource_exists(show_args):
        log(f"legacy smart detector alert not found; skipping {detector_name}")
        return

    run_command(
        [
            "az",
            "resource",
            "delete",
            "--resource-group",
            resource_group,
            "--namespace",
            "microsoft.alertsmanagement",
            "--resource-type",
            "smartDetectorAlertRules",
            "--name",
            detector_name,
        ],
        dry_run=dry_run,
    )


def delete_legacy_sli_resources(*, dry_run: bool) -> None:
    service_group_id = get_env_value("AZURE_MONITOR_SLI_SERVICE_GROUP_ID")
    sli_names = [
        get_env_value("AZURE_MONITOR_AVAILABILITY_SLI_NAME"),
        get_env_value("AZURE_MONITOR_LATENCY_SLI_NAME"),
    ]
    if not service_group_id or not all(sli_names):
        log("SLI service group or SLI names missing; skipping SLI resource cleanup")
        return

    service_group_url = resource_url(service_group_id)
    for sli_name in sli_names:
        sli_url = (
            f"{service_group_url}/providers/Microsoft.Monitor/slis/"
            f"{sli_name}?api-version={SLI_API_VERSION}"
        )
        run_command(
            ["az", "rest", "--method", "delete", "--url", sli_url, "--output", "none"],
            dry_run=dry_run,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up legacy AKS-internal SLI signal resources."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete resources. Default is dry-run.",
    )
    parser.add_argument(
        "--delete-sli-resources",
        action="store_true",
        help="Delete existing Azure Monitor SLI resources so they can be recreated as request-based SLIs.",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    delete_kubernetes_legacy_resources(dry_run=dry_run)
    if shutil.which("az") is None:
        log("az not found; skipping Azure legacy cleanup")
        return 0

    delete_legacy_prometheus_alert_group(dry_run=dry_run)
    delete_legacy_availability_test(dry_run=dry_run)
    delete_legacy_smart_detector_alert(dry_run=dry_run)
    if args.delete_sli_resources:
        delete_legacy_sli_resources(dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
