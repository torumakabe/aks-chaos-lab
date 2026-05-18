#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence


def log(message: str) -> None:
    print(f"[sli-warmup] {message}", file=sys.stderr, flush=True)


def command_output(
    args: Sequence[str],
    *,
    allow_failure: bool = False,
    quiet_stderr: bool = False,
) -> str:
    resolved_args = list(args)
    executable = shutil.which(resolved_args[0])
    if executable:
        resolved_args[0] = executable

    completed = subprocess.run(
        resolved_args,
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


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        log(f"{name} must be an integer")
        raise SystemExit(1) from None


def request(base_url: str, path: str, host_header: str) -> bool:
    headers = {"Host": host_header} if host_header else {}
    http_request = urllib.request.Request(f"{base_url}{path}", headers=headers)
    try:
        with urllib.request.urlopen(http_request, timeout=10) as response:
            response.read()
            return 200 <= response.status < 400
    except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError):
        return False


def check_recording_metric(prometheus_endpoint: str, query: str) -> int:
    response = command_output(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--resource",
            "https://prometheus.monitor.azure.com",
            "--url",
            f"{prometheus_endpoint}/api/v1/query",
            "--url-parameters",
            f"query={query}",
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
    return sum(1 for item in results if item.get("metric", {}).get("cluster_name"))


def main() -> int:
    if os.environ.get("SKIP_AZURE_MONITOR_SLI_WARMUP", "false").lower() == "true":
        log("skipped by SKIP_AZURE_MONITOR_SLI_WARMUP=true")
        return 0

    service_group_id = get_env_value("AZURE_MONITOR_SLI_SERVICE_GROUP_ID")
    if not service_group_id:
        log("Azure Monitor SLI is not enabled; skipping warm-up")
        return 0

    ingress_fqdn = get_env_value("AZURE_INGRESS_FQDN")
    ingress_public_ip = get_env_value("AZURE_INGRESS_PUBLIC_IP")

    if ingress_fqdn:
        ingress_host = ingress_fqdn.split("://", 1)[-1].split("/", 1)[0]
        base_url = f"http://{ingress_host}"
    elif ingress_public_ip:
        base_url = f"http://{ingress_public_ip}"
    else:
        log("AZURE_INGRESS_FQDN or AZURE_INGRESS_PUBLIC_IP is required")
        return 1

    gateway_host_header = os.environ.get("GATEWAY_HOST_HEADER", "example.com")
    ready_attempts = env_int("SLI_WARMUP_READY_ATTEMPTS", 60)
    ready_sleep_seconds = env_int("SLI_WARMUP_READY_SLEEP_SECONDS", 10)
    duration_seconds = env_int("SLI_WARMUP_DURATION_SECONDS", 180)
    interval_seconds = env_int("SLI_WARMUP_INTERVAL_SECONDS", 2)
    recording_attempts = env_int("SLI_WARMUP_RECORDING_ATTEMPTS", 80)
    recording_sleep_seconds = env_int("SLI_WARMUP_RECORDING_SLEEP_SECONDS", 30)

    host_header = ""
    app_ready = False
    for attempt in range(1, ready_attempts + 1):
        if request(base_url, "/health", ""):
            app_ready = True
            break

        if request(base_url, "/health", gateway_host_header):
            host_header = gateway_host_header
            app_ready = True
            break

        log(
            f"waiting for app readiness ({attempt}/{ready_attempts}) at {base_url}/health"
        )
        time.sleep(ready_sleep_seconds)

    if not app_ready:
        log(f"app did not become ready at {base_url}/health")
        return 1

    log(f"app is ready; warming SLI input signals for {duration_seconds}s")
    elapsed = 0
    while elapsed < duration_seconds:
        request(base_url, "/", host_header)
        request(base_url, "/health", host_header)
        time.sleep(interval_seconds)
        elapsed += interval_seconds

    log("completed")

    if shutil.which("az") is None:
        log("az is required to verify Managed Prometheus recording rules")
        return 1

    prometheus_workspace_id = get_env_value("AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID")
    if not prometheus_workspace_id:
        log(
            "AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID is required to verify recording rules"
        )
        return 1

    prometheus_endpoint = command_output(
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

    for recording_attempt in range(1, recording_attempts + 1):
        availability_count = check_recording_metric(
            prometheus_endpoint,
            "gateway:chaos_app:http_request_total",
        )
        latency_count = check_recording_metric(
            prometheus_endpoint,
            "gateway:chaos_app:http_request_duration:le_1s_ratio",
        )

        if availability_count > 0 and latency_count > 0:
            log("Managed Prometheus recording rules are ready")
            return 0

        log(
            "waiting for recording rules "
            f"({recording_attempt}/{recording_attempts}): "
            f"availability={availability_count}, latency={latency_count}"
        )
        request(base_url, "/", host_header)
        request(base_url, "/health", host_header)
        request(base_url, "/", host_header)
        request(base_url, "/health", host_header)
        request(base_url, "/", host_header)
        time.sleep(recording_sleep_seconds)

    log("recording rules did not expose cluster_name before timeout")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
