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
from datetime import UTC, datetime, timedelta


def log(message: str) -> None:
    print(f"[external-sli-wait] {message}", file=sys.stderr, flush=True)


def resolve_command(args: Sequence[str]) -> list[str]:
    resolved_args = list(args)
    executable = shutil.which(resolved_args[0])
    if executable:
        resolved_args[0] = executable
    return resolved_args


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
    if completed.returncode != 0:
        if not allow_failure:
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            raise SystemExit(completed.returncode)
        # On allowed failure, don't trust stdout — some tools (e.g. azd) write
        # error messages to stdout, which callers would otherwise treat as a
        # valid value.
        return ""
    return completed.stdout.strip()


def get_env_value(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    if shutil.which("azd") is None:
        return ""
    return command_output(
        ["azd", "env", "get-value", name],
        allow_failure=True,
        quiet_stderr=True,
    )


def management_token() -> str:
    return command_output(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            "https://management.azure.com",
            "--query",
            "accessToken",
            "--output",
            "tsv",
        ],
    )


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        log(f"{name} must be an integer")
        raise SystemExit(1) from None
    if parsed <= 0:
        log(f"{name} must be greater than zero")
        raise SystemExit(1)
    return parsed


def prometheus_endpoint(prometheus_workspace_id: str) -> str:
    return command_output(
        [
            "az",
            "resource",
            "show",
            "--ids",
            prometheus_workspace_id,
            "--api-version",
            "2023-04-03",
            "--query",
            "properties.metrics.prometheusQueryEndpoint",
            "-o",
            "tsv",
        ],
    )


def query_prometheus(endpoint: str, query: str) -> int:
    response = command_output(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--resource",
            "https://prometheus.monitor.azure.com",
            "--url",
            f"{endpoint}/api/v1/query?query={urllib.parse.quote(query, safe='')}",
            "--output",
            "json",
        ],
        allow_failure=True,
        quiet_stderr=True,
    )
    if not response:
        return 0
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return 0
    results = payload.get("data", {}).get("result", [])
    return len(results) if isinstance(results, list) else 0


def query_prometheus_metric_range(
    endpoint: str, metric_name: str, range_minutes: int
) -> int:
    query = f"count_over_time({{__name__={json.dumps(metric_name)}}}[{range_minutes}m])"
    return query_prometheus(endpoint, query)


def azure_monitor_workspace_prometheus_metric_name(
    metric_namespace: str, metric_name: str
) -> str:
    return f"ns::{metric_namespace}/m::{metric_name}".lower()


def get_sli_destination_metrics(sli_resource_id: str) -> list[dict[str, str]]:
    response = command_output(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            f"https://management.azure.com{sli_resource_id}?api-version=2025-03-01-preview",
            "--output",
            "json",
        ],
    )
    payload = json.loads(response)
    metrics = payload.get("properties", {}).get("destinationMetrics", [])
    if not isinstance(metrics, list):
        return []
    return [
        {
            "metricName": str(metric.get("metricName", "")),
            "metricNamespace": str(metric.get("metricNamespace", "")),
        }
        for metric in metrics
        if isinstance(metric, dict)
        and isinstance(metric.get("metricName"), str)
        and isinstance(metric.get("metricNamespace"), str)
    ]


def query_azure_metric_datapoints(
    *,
    resource_id: str,
    metric_namespace: str,
    metric_name: str,
    range_minutes: int,
    token: str,
) -> int:
    end = datetime.now(UTC)
    start = end - timedelta(minutes=range_minutes)
    timespan = f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}"
    url = (
        f"https://management.azure.com{resource_id}/providers/microsoft.insights/metrics"
        "?api-version=2023-10-01"
        f"&metricnamespace={urllib.parse.quote(metric_namespace, safe='')}"
        f"&metricnames={urllib.parse.quote(metric_name, safe='')}"
        f"&timespan={urllib.parse.quote(timespan, safe='')}"
        "&interval=PT5M"
        "&aggregation=Average"
    )
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        log(
            f"Azure Metrics query failed for {metric_namespace}/{metric_name}: {details}"
        )
        return 0
    except urllib.error.URLError as error:
        log(f"Azure Metrics query failed for {metric_namespace}/{metric_name}: {error}")
        return 0
    except json.JSONDecodeError:
        log(
            f"Azure Metrics query returned invalid JSON for {metric_namespace}/{metric_name}"
        )
        return 0
    datapoints = 0
    for metric in payload.get("value", []):
        for timeseries in metric.get("timeseries", []):
            for point in timeseries.get("data", []):
                if any(
                    point.get(field) is not None
                    for field in ("average", "total", "count", "minimum", "maximum")
                ):
                    datapoints += 1
    return datapoints


def destination_value_metrics(sli_resource_id: str) -> list[dict[str, str]]:
    return [
        metric
        for metric in get_sli_destination_metrics(sli_resource_id)
        if metric["metricName"].endswith(":Value")
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for external SLI source metrics and optionally Azure Monitor SLI destination metrics.",
    )
    parser.add_argument(
        "--require-sli-destination",
        action="store_true",
        help="Also require Azure Monitor SLI destination Value metrics after the SLI layer is provisioned.",
    )
    parser.add_argument(
        "--skip-source",
        action="store_true",
        help="Skip the source metric check. Use only after the same workflow already waited for source metrics.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.skip_source and not args.require_sli_destination:
        log("--skip-source requires --require-sli-destination")
        return 1

    if get_env_value("AZURE_MONITOR_SLI_SERVICE_GROUP_ID") == "":
        log("Azure Monitor SLI is not enabled; skipping external SLI signal wait")
        return 0

    prometheus_workspace_id = get_env_value("AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID")
    probe_name = get_env_value("AZURE_EXTERNAL_SLI_PROBE_NAME") or get_env_value(
        "AZURE_EXTERNAL_SLI_AVAILABILITY_TEST_NAME"
    )
    env_name = get_env_value("AZURE_ENV_NAME")
    if not prometheus_workspace_id or (
        not args.skip_source and (not probe_name or not env_name)
    ):
        log("external SLI outputs are not available; skipping wait")
        return 0

    if shutil.which("az") is None:
        log("az is required to verify external SLI signals")
        return 1

    attempts = env_int("EXTERNAL_SLI_WAIT_ATTEMPTS", 90)
    sleep_seconds = env_int("EXTERNAL_SLI_WAIT_SLEEP_SECONDS", 30)
    endpoint = prometheus_endpoint(prometheus_workspace_id)
    if args.skip_source:
        log("skipping external SLI source metric wait")
    else:
        selector_inner = (
            f'environment="{env_name}",service="chaos-app",test="{probe_name}"'
        )
        latency_le = get_env_value("AZURE_MONITOR_LATENCY_SLI_THRESHOLD_LE") or "1"
        latency_good_metric = "chaos_app_external_latency_good"
        query_range_minutes = env_int("EXTERNAL_SLI_WAIT_RANGE_MINUTES", 45)
        availability_query = (
            f"count_over_time(chaos_app_external_availability_total"
            f"{{{selector_inner}}}[{query_range_minutes}m])"
        )
        latency_total_query = (
            f"count_over_time(chaos_app_external_latency_total"
            f"{{{selector_inner}}}[{query_range_minutes}m])"
        )
        # SLI provisioning validates that the bucket whose `le` label matches
        # the SLO threshold actually has samples for the partitioning
        # dimensions. Wait for that specific bucket label explicitly.
        latency_good_query = (
            f"count_over_time({latency_good_metric}"
            f'{{{selector_inner},le="{latency_le}"}}[{query_range_minutes}m])'
        )
        source_ready = False

        for attempt in range(1, attempts + 1):
            availability_count = query_prometheus(endpoint, availability_query)
            latency_total_count = query_prometheus(endpoint, latency_total_query)
            latency_good_count = query_prometheus(endpoint, latency_good_query)
            if (
                availability_count > 0
                and latency_total_count > 0
                and latency_good_count > 0
            ):
                log("external SLI signals are available in Managed Prometheus")
                source_ready = True
                break

            log(
                "waiting for external SLI signals "
                f"({attempt}/{attempts}): "
                f"availability={availability_count}, "
                f"latency_total={latency_total_count}, "
                f'{latency_good_metric}{{le="{latency_le}"}}={latency_good_count}'
            )
            time.sleep(sleep_seconds)

        if not source_ready:
            log("external SLI signals did not appear before timeout")
            return 1

    if not args.require_sli_destination:
        return 0

    availability_sli_id = get_env_value("AZURE_MONITOR_AVAILABILITY_SLI_ID")
    latency_sli_id = get_env_value("AZURE_MONITOR_LATENCY_SLI_ID")
    if not availability_sli_id or not latency_sli_id:
        log("Azure Monitor SLI resource IDs are not available")
        return 1

    destination_metrics = {
        "availability": destination_value_metrics(availability_sli_id),
        "latency": destination_value_metrics(latency_sli_id),
    }
    if not destination_metrics["availability"] or not destination_metrics["latency"]:
        log("Azure Monitor SLI destination Value metrics are not available from ARM")
        return 1

    destination_range_minutes = env_int(
        "EXTERNAL_SLI_DESTINATION_WAIT_RANGE_MINUTES", 60
    )
    token = management_token()
    for attempt in range(1, attempts + 1):
        destination_counts: dict[str, int] = {}
        for sli_name, metrics in destination_metrics.items():
            prometheus_count = sum(
                query_prometheus_metric_range(
                    endpoint,
                    azure_monitor_workspace_prometheus_metric_name(
                        metric["metricNamespace"],
                        metric["metricName"],
                    ),
                    destination_range_minutes,
                )
                for metric in metrics
            )
            destination_counts[sli_name] = prometheus_count
            if prometheus_count == 0:
                destination_counts[sli_name] += sum(
                    query_azure_metric_datapoints(
                        resource_id=prometheus_workspace_id,
                        metric_namespace=metric["metricNamespace"],
                        metric_name=metric["metricName"],
                        range_minutes=destination_range_minutes,
                        token=token,
                    )
                    for metric in metrics
                )
        if all(count > 0 for count in destination_counts.values()):
            log("Azure Monitor SLI destination metrics are available")
            return 0

        log(
            "waiting for Azure Monitor SLI destination metrics "
            f"({attempt}/{attempts}): "
            f"availability={destination_counts['availability']}, "
            f"latency={destination_counts['latency']}"
        )
        time.sleep(sleep_seconds)

    log("Azure Monitor SLI destination metrics did not appear before timeout")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
