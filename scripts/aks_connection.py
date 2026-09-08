from __future__ import annotations

import ctypes
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REQUIRED_ENVIRONMENT = (
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "AZURE_AKS_CLUSTER_NAME",
)
TEMP_DIRECTORY = Path(__file__).resolve().parents[1] / "tmp"
STOP_TIMEOUT_SECONDS = 5


class AKSConnectionError(RuntimeError):
    """Command output is deliberately excluded from public diagnostics."""

    def __init__(self, message: str, *, category: str = "configuration") -> None:
        super().__init__(message)
        self.category = category


class DeadlineExceeded(AKSConnectionError):  # noqa: N818
    pass


class Deadline:
    def __init__(self, timeout_seconds: float) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise AKSConnectionError("timeout_seconds must be finite and positive")
        self.expires_at = time.monotonic() + timeout_seconds
        self.last_failure = ""

    def remaining(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            detail = f"; last failure: {self.last_failure}" if self.last_failure else ""
            raise DeadlineExceeded(
                f"AKS helper deadline exceeded{detail}", category="timeout"
            )
        return remaining

    def sleep(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            raise AKSConnectionError("poll_seconds must be finite and positive")
        time.sleep(min(seconds, self.remaining()))
        self.remaining()


def failure_category(output: str) -> str:
    text = output.lower()
    if any(
        marker in text
        for marker in (
            "unauthorized",
            "authentication",
            "unauthenticated",
            "aadsts",
            "please run 'az login'",
            "please run 'azd auth login'",
            "getting credentials",
            "provide credentials",
            "you must be logged in",
            "failed to refresh token",
            "failed to get token",
            "exec: executable",
        )
    ):
        return "authentication"
    if any(
        marker in text
        for marker in ("forbidden", "authorizationfailed", "permission denied")
    ):
        return "authorization"
    if any(
        marker in text
        for marker in (
            "notfound",
            "not found",
            "doesn't have a resource type",
            "could not find the requested resource",
        )
    ):
        return "not-found"
    if any(
        marker in text
        for marker in (
            "unable to connect",
            "connection refused",
            "no such host",
            "i/o timeout",
            "tls handshake",
            "certificate",
            "network is unreachable",
        )
    ):
        return "connection"
    return "command-failed"


class _WindowsJob:
    """A gated launcher joins this job before it can start authentication children."""

    kernel: Any

    def __init__(self) -> None:
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimit),
                ("io_counters", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        if sys.platform == "win32":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        else:
            raise AKSConnectionError("Windows command jobs require Windows")
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self.kernel.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise AKSConnectionError("Cannot create bounded Windows command job")
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise AKSConnectionError("Cannot configure bounded Windows command job")

    def assign(self, pid: int) -> None:
        handle = self.kernel.OpenProcess(0x0101, False, pid)
        if not handle:
            raise AKSConnectionError("Cannot open owned Windows command process")
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, handle):
                raise AKSConnectionError("Cannot isolate Windows command process tree")
        finally:
            self.kernel.CloseHandle(handle)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _stop_process(process: subprocess.Popen[bytes], job: _WindowsJob | None) -> None:
    if job is not None:
        job.close()
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        with suppress(OSError):
            process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=STOP_TIMEOUT_SECONDS)


def run_command(
    args: Sequence[str],
    *,
    deadline: Deadline,
    env: Mapping[str, str],
    operation: str,
) -> str:
    deadline.remaining()
    executable = shutil.which(args[0])
    if executable is None:
        raise AKSConnectionError(f"{operation}: required command {args[0]} is missing")
    command = [executable, *args[1:]]
    job = _WindowsJob() if os.name == "nt" else None
    if job is not None:
        command = [
            sys.executable,
            "-c",
            "import subprocess,sys; "
            "sys.stdin.buffer.read(1) == b'\\n' or sys.exit(125); "
            "sys.exit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))",
            *command,
        ]
    process: subprocess.Popen[bytes] | None = None
    try:
        deadline.remaining()
        process = subprocess.Popen(
            command,
            env=dict(env),
            stdin=subprocess.PIPE if job is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=job is None,
        )
        if job is not None:
            job.assign(process.pid)
        try:
            stdout, stderr = process.communicate(
                input=b"\n" if job is not None else None,
                timeout=deadline.remaining(),
            )
        except subprocess.TimeoutExpired:
            raise DeadlineExceeded(
                f"{operation}: command exceeded AKS helper deadline", category="timeout"
            ) from None
        deadline.remaining()
        if process.returncode:
            category = failure_category(
                stderr.decode("utf-8", errors="replace")
                + stdout.decode("utf-8", errors="replace")
            )
            if "can-i" in args and "auth" in args and stdout.strip().lower() == b"no":
                category = "authorization"
            raise AKSConnectionError(
                f"{operation}: {category} (exit {process.returncode}); "
                "command output withheld to protect credentials",
                category=category,
            )
        return stdout.decode("utf-8", errors="replace").strip()
    except OSError:
        raise AKSConnectionError(f"{operation}: unable to execute command") from None
    finally:
        if process is not None:
            _stop_process(process, job)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if job is not None:
            job.close()


def resolve_environment(
    deadline: Deadline, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    deadline.remaining()
    environment = dict(os.environ if environ is None else environ)
    environment_name = environment.get("AZURE_ENV_NAME", "").strip()
    if not environment_name:
        raise AKSConnectionError(
            "AZURE_ENV_NAME is required; use azd exec -e <environment>"
        )
    environment["AZURE_ENV_NAME"] = environment_name
    for name in REQUIRED_ENVIRONMENT:
        value = environment.get(name, "").strip()
        if not value:
            value = run_command(
                ["azd", "env", "get-value", name, "-e", environment_name],
                deadline=deadline,
                env=environment,
                operation=f"resolve {name}",
            )
        if not value:
            raise AKSConnectionError(f"Required environment value {name} is missing")
        environment[name] = value
    deadline.remaining()
    return environment


def _json_object(output: str, operation: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except ValueError, TypeError:
        raise AKSConnectionError(f"{operation}: invalid JSON response") from None
    if not isinstance(value, dict):
        raise AKSConnectionError(f"{operation}: expected a JSON object")
    return value


def _custom_kubeconfig(value: str) -> Path:
    if os.pathsep in value or ";" in value:
        raise AKSConnectionError("KUBECONFIG must contain one absolute file path")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise AKSConnectionError("KUBECONFIG must be an existing absolute file path")
    return path


def _selected_context(config: dict[str, Any], endpoints: set[str]) -> str:
    try:
        current = config["current-context"]
        contexts = config["contexts"]
        clusters = config["clusters"]
        if not isinstance(current, str) or not current.strip():
            raise ValueError
        context = next(item["context"] for item in contexts if item["name"] == current)
        cluster = next(
            item["cluster"] for item in clusters if item["name"] == context["cluster"]
        )
        server = urlsplit(cluster["server"])
        if (
            server.scheme != "https"
            or server.username
            or server.password
            or server.port not in (None, 443)
            or server.query
            or server.fragment
            or server.path not in ("", "/")
            or (server.hostname or "").lower().rstrip(".") not in endpoints
        ):
            raise AKSConnectionError(
                "KUBECONFIG selected API server does not match target AKS ARM endpoint"
            )
    except KeyError, TypeError, ValueError, StopIteration, AttributeError:
        raise AKSConnectionError("KUBECONFIG has an invalid selected context") from None
    return current


class AKSConnection:
    def __init__(
        self,
        deadline: Deadline,
        environment: dict[str, str],
        kubeconfig: Path,
        context: str,
    ) -> None:
        self.deadline = deadline
        self.environment = environment
        self.kubeconfig = kubeconfig
        self.context = context

    def kubectl(self, args: Sequence[str]) -> str:
        return run_command(
            [
                "kubectl",
                "--kubeconfig",
                str(self.kubeconfig),
                "--context",
                self.context,
                *args,
            ],
            deadline=self.deadline,
            env={**self.environment, "KUBECONFIG": str(self.kubeconfig)},
            operation="kubectl " + (args[0] if args else "command"),
        )

    def json_resource(self, args: Sequence[str]) -> dict[str, Any]:
        return _json_object(self.kubectl([*args, "-o", "json"]), "kubectl resource")

    def probe(self, args: Sequence[str]) -> bool:
        try:
            output = self.kubectl(args)
        except AKSConnectionError as error:
            if error.category != "not-found":
                raise
            self.deadline.last_failure = str(error)
            return False
        if not output:
            raise AKSConnectionError(
                "kubectl probe returned an empty successful response"
            )
        return True

    def rollout(self, deployment: str, namespace: str, timeout_seconds: float) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise AKSConnectionError("rollout timeout must be positive")
        timeout = min(timeout_seconds, self.deadline.remaining())
        self.kubectl(
            [
                "rollout",
                "status",
                f"deployment/{deployment}",
                "-n",
                namespace,
                f"--timeout={timeout:.3f}s",
            ]
        )


@contextmanager
def aks_connection(
    timeout_seconds: float = 600,
    *,
    deadline: Deadline | None = None,
    environ: Mapping[str, str] | None = None,
) -> Iterator[AKSConnection]:
    # The deadline starts before environment resolution, not after credentials exist.
    budget = deadline if deadline is not None else Deadline(timeout_seconds)
    environment = resolve_environment(budget, environ)
    configured = environment.get("KUBECONFIG", "")
    kubeconfig = _custom_kubeconfig(configured) if configured else None
    target_args = [
        "--subscription",
        environment["AZURE_SUBSCRIPTION_ID"],
        "--resource-group",
        environment["AZURE_RESOURCE_GROUP"],
        "--name",
        environment["AZURE_AKS_CLUSTER_NAME"],
    ]
    target = _json_object(
        run_command(
            [
                "az",
                "aks",
                "show",
                *target_args,
                "--query",
                "{id:id,fqdn:fqdn,privateFqdn:privateFqdn,tenantId:aadProfile.tenantId}",
                "--output",
                "json",
                "--only-show-errors",
            ],
            deadline=budget,
            env=environment,
            operation="AKS ARM lookup (Azure CLI authentication)",
        ),
        "AKS ARM lookup",
    )
    expected_id = (
        f"/subscriptions/{environment['AZURE_SUBSCRIPTION_ID']}"
        f"/resourceGroups/{environment['AZURE_RESOURCE_GROUP']}"
        f"/providers/Microsoft.ContainerService/managedClusters/"
        f"{environment['AZURE_AKS_CLUSTER_NAME']}"
    )
    if str(target.get("id", "")).lower() != expected_id.lower():
        raise AKSConnectionError("AKS ARM resource ID does not match requested target")
    endpoints = {
        value.lower().rstrip(".")
        for name in ("fqdn", "privateFqdn")
        if isinstance(value := target.get(name), str) and value
    }
    if not endpoints:
        raise AKSConnectionError("AKS ARM response has no supported API server FQDN")
    owned_path: Path | None = None
    try:
        if kubeconfig is None:
            budget.remaining()
            TEMP_DIRECTORY.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix="aks-", suffix=".kubeconfig", dir=TEMP_DIRECTORY, delete=False
            ) as temporary:
                owned_path = Path(temporary.name)
            kubeconfig = owned_path
            child_env = {**environment, "KUBECONFIG": str(kubeconfig)}
            run_command(
                [
                    "az",
                    "aks",
                    "get-credentials",
                    *target_args,
                    "--file",
                    str(kubeconfig),
                    "--overwrite-existing",
                    "--only-show-errors",
                ],
                deadline=budget,
                env=child_env,
                operation="AKS credentials retrieval (Azure CLI authentication)",
            )
            conversion = [
                "kubelogin",
                "convert-kubeconfig",
                "--login",
                "azd",
                "--kubeconfig",
                str(kubeconfig),
            ]
            tenant = target.get("tenantId")
            if isinstance(tenant, str) and tenant:
                conversion.extend(["--tenant-id", tenant])
            run_command(
                conversion,
                deadline=budget,
                env=child_env,
                operation="AKS credential conversion (azd authentication)",
            )
        config = _json_object(
            run_command(
                [
                    "kubectl",
                    "--kubeconfig",
                    str(kubeconfig),
                    "config",
                    "view",
                    "--minify",
                    "-o",
                    "json",
                ],
                deadline=budget,
                env={**environment, "KUBECONFIG": str(kubeconfig)},
                operation="KUBECONFIG target validation",
            ),
            "KUBECONFIG target validation",
        )
        context = _selected_context(config, endpoints)
        budget.remaining()
        yield AKSConnection(budget, environment, kubeconfig, context)
    finally:
        if owned_path is not None:
            owned_path.unlink(missing_ok=True)
