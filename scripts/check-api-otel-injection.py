#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from typing import Any

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


def kubectl(args: Sequence[str], *, allow_failure: bool = False) -> str:
    executable = shutil.which("kubectl")
    if executable is None:
        print("error: kubectl not found", file=sys.stderr)
        raise SystemExit(1)
    completed = subprocess.run(
        [executable, *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and not allow_failure:
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        raise SystemExit(completed.returncode)
    return completed.stdout.strip()


def resource_exists(args: Sequence[str]) -> bool:
    return bool(kubectl(args, allow_failure=True))


def wait_until_ready(args: argparse.Namespace) -> None:
    deadline = time.monotonic() + args.timeout_seconds
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

    missing: list[str] = []
    while time.monotonic() < deadline:
        missing = [name for name, command in checks if not resource_exists(command)]
        if not missing:
            kubectl(
                [
                    "rollout",
                    "status",
                    f"deployment/{args.webhook_deployment}",
                    "-n",
                    args.webhook_namespace,
                    f"--timeout={args.rollout_timeout_seconds}s",
                ]
            )
            print("ok: API Instrumentation CR and app-monitoring webhook are ready")
            return
        time.sleep(args.poll_seconds)

    print(
        "error: API Instrumentation is not ready. Missing: " + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)


def require_instrumentation(args: argparse.Namespace) -> None:
    wait_until_ready(args)


def json_resource(args: Sequence[str]) -> dict[str, Any]:
    return json.loads(kubectl([*args, "-o", "json"]))


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
    if containers:
        return containers[0]
    print("error: no containers found", file=sys.stderr)
    raise SystemExit(1)


def missing_env(container: dict[str, Any]) -> list[str]:
    names = container_env_names(container)
    return [name for name in REQUIRED_OTEL_ENV if name not in names]


def deployment_selector(deployment: dict[str, Any]) -> str:
    labels = deployment["spec"]["selector"]["matchLabels"]
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def check_injected(args: argparse.Namespace) -> None:
    wait_until_ready(args)
    kubectl(
        [
            "rollout",
            "status",
            f"deployment/{args.deployment}",
            "-n",
            args.namespace,
            f"--timeout={args.rollout_timeout_seconds}s",
        ]
    )

    deployment = json_resource(
        ["get", "deployment", args.deployment, "-n", args.namespace]
    )
    containers = deployment["spec"]["template"]["spec"].get("containers", [])
    app_container = named_container(containers, args.container)
    deployment_missing = missing_env(app_container)
    if deployment_missing:
        print(
            "error: Deployment pod template is missing OTEL env: "
            + ", ".join(deployment_missing),
            file=sys.stderr,
        )
        raise SystemExit(1)

    selector = deployment_selector(deployment)
    pods = json_resource(["get", "pod", "-n", args.namespace, "-l", selector])
    pod_errors: list[str] = []
    for pod in pods.get("items", []):
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
        print(
            "error: API pods are not fully OTLP-injected:\n  "
            + "\n  ".join(pod_errors),
            file=sys.stderr,
        )
        raise SystemExit(1)

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
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--rollout-timeout-seconds", type=int, default=120)
    parser.add_argument("--poll-seconds", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.action == "wait-instrumentation":
        wait_until_ready(args)
    elif args.action == "require-instrumentation":
        require_instrumentation(args)
    elif args.action == "check-injected":
        check_injected(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
