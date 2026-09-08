#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from aks_connection import AKSConnection, AKSConnectionError, aks_connection

DEFAULT_NAMESPACE = "chaos-lab"
DEFAULT_DEPLOYMENT = "chaos-app"
DEFAULT_CONTAINER = "app"
DEFAULT_INSTRUMENTATION = "chaos-app-otel"
DEFAULT_WEBHOOK_DEPLOYMENT = "app-monitoring-webhook"
DEFAULT_WEBHOOK_NAMESPACE = "kube-system"
INSTRUMENTATION_CRD = "instrumentations.monitor.azure.com"
WEBHOOK_CONFIGURATION = "app-monitoring-webhook"
REQUIRED_OTEL_ENV = (
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
)


def wait_until_ready(args: argparse.Namespace, connection: AKSConnection) -> None:
    checks: tuple[tuple[str, Sequence[str]], ...] = (
        ("Instrumentation CRD", ["get", "crd", INSTRUMENTATION_CRD, "-o", "name"]),
        (
            "app-monitoring webhook configuration",
            [
                "get",
                "mutatingwebhookconfiguration",
                WEBHOOK_CONFIGURATION,
                "-o",
                "name",
            ],
        ),
        (
            "chaos-app Instrumentation",
            [
                "get",
                "instrumentation",
                args.instrumentation,
                "-n",
                args.namespace,
                "-o",
                "name",
            ],
        ),
    )
    while True:
        connection.deadline.remaining()
        missing = [name for name, command in checks if not connection.probe(command)]
        if not missing:
            connection.rollout(
                args.webhook_deployment,
                args.webhook_namespace,
                args.rollout_timeout_seconds,
            )
            print("ok: API Instrumentation CR and app-monitoring webhook are ready")
            return
        connection.deadline.last_failure = "Missing: " + ", ".join(missing)
        print(
            "waiting for API Instrumentation: " + ", ".join(missing),
            file=sys.stderr,
            flush=True,
        )
        connection.deadline.sleep(args.poll_seconds)


def require_instrumentation(
    args: argparse.Namespace, connection: AKSConnection
) -> None:
    wait_until_ready(args, connection)


def container_env_names(container: dict[str, Any]) -> set[str]:
    return {
        env["name"]
        for env in container.get("env", [])
        if isinstance(env, dict) and isinstance(env.get("name"), str)
    }


def named_container(containers: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for container in containers:
        if container.get("name") == name:
            return container
    raise AKSConnectionError(f"Required container {name} was not found")


def missing_env(container: dict[str, Any]) -> list[str]:
    names = container_env_names(container)
    return [name for name in REQUIRED_OTEL_ENV if name not in names]


def deployment_selector(deployment: dict[str, Any]) -> str:
    labels = deployment["spec"]["selector"]["matchLabels"]
    if not labels:
        raise AKSConnectionError("Deployment has no supported matchLabels selector")
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def check_injected(args: argparse.Namespace, connection: AKSConnection) -> None:
    wait_until_ready(args, connection)
    connection.rollout(args.deployment, args.namespace, args.rollout_timeout_seconds)
    deployment = connection.json_resource(
        ["get", "deployment", args.deployment, "-n", args.namespace]
    )
    containers = deployment["spec"]["template"]["spec"].get("containers", [])
    deployment_missing = missing_env(named_container(containers, args.container))
    if deployment_missing:
        raise AKSConnectionError(
            "Deployment pod template is missing OTEL env: "
            + ", ".join(deployment_missing)
        )

    selector = deployment_selector(deployment)
    pods = connection.json_resource(
        ["get", "pod", "-n", args.namespace, "-l", selector]
    )
    if not pods.get("items"):
        raise AKSConnectionError("API pod query returned no pods")
    pod_errors: list[str] = []
    for pod in pods["items"]:
        pod_name = pod["metadata"]["name"]
        phase = pod.get("status", {}).get("phase")
        if phase not in {"Running", "Succeeded"}:
            pod_errors.append(f"{pod_name}: phase={phase}")
            continue
        pod_container = named_container(
            pod["spec"].get("containers", []), args.container
        )
        pod_missing = missing_env(pod_container)
        if pod_missing:
            pod_errors.append(f"{pod_name}: missing {', '.join(pod_missing)}")
    if pod_errors:
        raise AKSConnectionError(
            "API pods are not fully OTLP-injected:\n  " + "\n  ".join(pod_errors)
        )
    connection.deadline.remaining()
    print("ok: API deployment and pods contain required OTEL exporter env vars")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate AKS Application Insights OTLP injection for chaos-app."
    )
    parser.add_argument(
        "action",
        choices=("wait-instrumentation", "require-instrumentation", "check-injected"),
    )
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--deployment", default=DEFAULT_DEPLOYMENT)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--instrumentation", default=DEFAULT_INSTRUMENTATION)
    parser.add_argument("--webhook-namespace", default=DEFAULT_WEBHOOK_NAMESPACE)
    parser.add_argument("--webhook-deployment", default=DEFAULT_WEBHOOK_DEPLOYMENT)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Overall deadline including environment, connection, rollout and final queries.",
    )
    parser.add_argument("--rollout-timeout-seconds", type=int, default=120)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args(argv)
    if min(args.timeout_seconds, args.rollout_timeout_seconds, args.poll_seconds) <= 0:
        parser.error(
            "timeout-seconds, rollout-timeout-seconds and poll-seconds must be positive"
        )
    return args


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        with aks_connection(args.timeout_seconds) as connection:
            if args.action == "wait-instrumentation":
                wait_until_ready(args, connection)
            elif args.action == "require-instrumentation":
                require_instrumentation(args, connection)
            elif args.action == "check-injected":
                check_injected(args, connection)
    except AKSConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
