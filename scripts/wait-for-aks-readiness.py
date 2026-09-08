#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from aks_connection import AKSConnection, AKSConnectionError, aks_connection

DEFAULT_NAMESPACE = "chaos-lab"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_POLL_SECONDS = 10


def readiness_failures(connection: AKSConnection, namespace: str) -> list[str]:
    failures: list[str] = []
    checks = (
        ("get", "poddisruptionbudgets.policy"),
        ("create", "deployments.apps"),
        ("patch", "deployments.apps"),
        ("get", "services"),
    )
    if not connection.probe(["get", "namespace", "default", "-o", "name"]):
        failures.append("Kubernetes API: default namespace not found")
    for verb, resource in checks:
        output = connection.kubectl(["auth", "can-i", verb, resource, "-n", namespace])
        if output.lower() == "no":
            failures.append(
                f"authorization: {verb} {resource} in namespace {namespace}"
            )
        elif output.lower() != "yes":
            raise AKSConnectionError(
                "kubectl auth can-i returned an unexpected response"
            )
    return failures


def wait_until_ready(args: argparse.Namespace, connection: AKSConnection) -> None:
    while True:
        connection.deadline.remaining()
        try:
            failures = readiness_failures(connection, args.namespace)
        except AKSConnectionError as error:
            if error.category not in {"authorization", "connection"}:
                raise
            failures = [str(error)]
        if not failures:
            connection.deadline.remaining()
            print("ok: AKS Kubernetes API and Azure RBAC are ready")
            return
        connection.deadline.last_failure = ", ".join(failures)
        print(
            "waiting for AKS readiness: " + connection.deadline.last_failure,
            file=sys.stderr,
            flush=True,
        )
        connection.deadline.sleep(args.poll_seconds)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for AKS Kubernetes API and Azure RBAC readiness before applying manifests.",
    )
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Overall deadline including environment and connection preparation.",
    )
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("timeout-seconds and poll-seconds must be positive")
    return args


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        with aks_connection(args.timeout_seconds) as connection:
            wait_until_ready(args, connection)
    except AKSConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
