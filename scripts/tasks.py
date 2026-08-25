#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import codecs
import hashlib
import importlib
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, TextIO, cast

from approved_index_config import (
    UNSAFE_UV_ENVIRONMENT_VARIABLES,
    ApprovedIndexConfigError,
    config_sha256,
    user_uv_config_path,
    validate_approved_index_config,
)
from public_lock import (
    PublicLockError,
    validate_exported_requirements,
    validate_public_lock,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
API_DIR = SRC / "api"
PUBLISHER_DIR = SRC / "external-sli-publisher"
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
ACTIONLINT_IMAGE = "rhysd/actionlint:1.7.12"
KUBECONFORM_IMAGE = "ghcr.io/yannh/kubeconform:v0.7.0"
K8S_VERSION = "1.33.0"
KUBECONFORM_SKIP = "VerticalPodAutoscaler,CiliumNetworkPolicy,Kustomization,Gateway,HTTPRoute,Instrumentation"
APPROVED_INDEX_CACHE_DIRECTORY = Path(".uv-state") / "cache"
API_LOCAL_IMAGE = "aks-chaos-lab:local"
REVIEW_MAX_UNTRACKED_FILE_BYTES = 10 * 1024 * 1024
REVIEW_CHECK_TIMEOUT_SECONDS = 300
REVIEW_TOOL_PREFLIGHT_TIMEOUT_SECONDS = 10
REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS = 10
REVIEW_LOG_TAIL_BYTES = 64 * 1024
REVIEW_LOG_STREAM_CHUNK_BYTES = 64 * 1024
REVIEW_TOOL_VERSION_COMMANDS: dict[str, tuple[str, ...]] = {
    "uv": ("uv", "--version"),
    "git": ("git", "--version"),
    "docker": ("docker", "--version"),
    "az": ("az", "version"),
    "gh": ("gh", "--version"),
    "lefthook": ("lefthook", "version"),
}
_approved_index_environment_prepared = False
_approved_index_lock_file: BinaryIO | None = None


def print_step(message: str) -> None:
    print(f"-> {message}")


def print_success(message: str) -> None:
    print(f"ok: {message}")


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        print(f"error: {command} not found", file=sys.stderr)
        raise SystemExit(1)


def resolve_command(args: Sequence[str]) -> list[str]:
    resolved_args = list(args)
    executable = shutil.which(resolved_args[0])
    if executable:
        resolved_args[0] = executable
    return resolved_args


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    for name in UNSAFE_UV_ENVIRONMENT_VARIABLES:
        env.pop(name, None)
    for name in tuple(env):
        normalized_name = name.upper()
        if normalized_name.startswith("UV_INDEX_") and normalized_name.endswith(
            ("_USERNAME", "_PASSWORD")
        ):
            env.pop(name, None)
    env.setdefault("PYTHONUTF8", "1")
    if extra:
        env.update(extra)
    return env


def project_environment_path() -> Path:
    configured = os.environ.get("UV_PROJECT_ENVIRONMENT")
    if not configured:
        return ROOT / ".venv"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else ROOT / path


def approved_index_cache_path() -> Path:
    return ROOT / APPROVED_INDEX_CACHE_DIRECTORY


def approved_index_credentials(config_path: Path) -> dict[str, str]:
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    index_name = config["index"][0]["name"]
    normalized_name = re.sub(r"[^A-Za-z0-9]", "_", index_name).upper()
    prefix = f"UV_INDEX_{normalized_name}"
    return {
        name: os.environ[name]
        for name in (f"{prefix}_USERNAME", f"{prefix}_PASSWORD")
        if name in os.environ
    }


def approved_index_lock_path() -> Path:
    environment = os.path.normcase(str(project_environment_path().resolve()))
    environment_key = hashlib.sha256(environment.encode()).hexdigest()
    return (
        Path(tempfile.gettempdir())
        / "aks-chaos-lab-uv-locks"
        / f"{environment_key}.lock"
    )


def acquire_approved_index_lock() -> None:
    global _approved_index_lock_file

    if _approved_index_lock_file is not None:
        return

    lock_path = approved_index_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
    lock_file.seek(0)

    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt").__dict__
        while True:
            try:
                msvcrt["locking"](lock_file.fileno(), msvcrt["LK_NBLCK"], 1)
                break
            except OSError:
                time.sleep(0.1)
    else:
        fcntl = importlib.import_module("fcntl").__dict__
        fcntl["flock"](lock_file.fileno(), fcntl["LOCK_EX"])

    _approved_index_lock_file = lock_file


def release_approved_index_lock() -> None:
    global _approved_index_lock_file

    if _approved_index_lock_file is None:
        return
    _approved_index_lock_file.close()
    _approved_index_lock_file = None


def lock_sha256() -> str:
    digest = hashlib.sha256()
    with (ROOT / "uv.lock").open("rb") as lock_file:
        for chunk in iter(lambda: lock_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approved_index_run_flags() -> list[str]:
    global _approved_index_environment_prepared

    if _approved_index_environment_prepared:
        return ["--no-sync"]

    try:
        config_path = user_uv_config_path()
        validate_approved_index_config(config_path, environ={})
    except ApprovedIndexConfigError:
        return []

    target_sync_dev_approved_index()
    return ["--no-sync"]


def ensure_approved_index_not_selected() -> None:
    try:
        validate_approved_index_config(user_uv_config_path(), environ={})
    except ApprovedIndexConfigError:
        return
    print(
        "error: Standard sync cannot use the approved package index. "
        'Run the "sync-dev-approved-index" target instead.',
        file=sys.stderr,
    )
    raise SystemExit(1)


def ensure_standard_sync_allowed() -> None:
    ensure_approved_index_not_selected()


def environment_python_path() -> Path:
    environment = project_environment_path()
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def environment_site_packages_path() -> Path:
    environment = project_environment_path()
    if os.name == "nt":
        return environment / "Lib" / "site-packages"
    return (
        environment
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


def run(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        resolve_command(args),
        cwd=cwd,
        env=child_env(env),
        check=False,
    )
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed


def command_output(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    allow_failure: bool = False,
    quiet_stderr: bool = False,
) -> str:
    completed = subprocess.run(
        resolve_command(args),
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if quiet_stderr else subprocess.PIPE,
        env=child_env(),
        text=True,
    )
    if completed.returncode != 0 and not allow_failure:
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        raise SystemExit(completed.returncode)
    return completed.stdout.strip()


def command_nul_output(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
) -> list[str]:
    completed = subprocess.run(
        resolve_command(args),
        cwd=cwd,
        check=False,
        capture_output=True,
        env=child_env(),
    )
    if completed.returncode != 0:
        if completed.stderr:
            print(os.fsdecode(completed.stderr), file=sys.stderr, end="")
        raise SystemExit(completed.returncode)

    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    return [os.fsdecode(field) for field in fields]


def run_uv(args: Sequence[str], *, env: dict[str, str] | None = None) -> None:
    """Run a command in the workspace venv from the repository root."""
    project_env = {
        "UV_PROJECT_ENVIRONMENT": str(project_environment_path()),
        **(env or {}),
    }
    run(
        [
            "uv",
            "run",
            "--project",
            str(ROOT),
            *approved_index_run_flags(),
            *args,
        ],
        cwd=ROOT,
        env=project_env,
    )


def run_uv_in(
    cwd: Path,
    args: Sequence[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Run a command in the workspace venv from a specific subdirectory.

    Used for pytest invocations that rely on the subpackage's pytest config and
    PYTHONPATH layout (e.g. `tests/` discovering `app/` via cwd-based imports).
    """
    project_env = {
        "UV_PROJECT_ENVIRONMENT": str(project_environment_path()),
        **(env or {}),
    }
    run(
        [
            "uv",
            "run",
            "--project",
            str(ROOT),
            *approved_index_run_flags(),
            *args,
        ],
        cwd=cwd,
        env=project_env,
    )


def pythonpath_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(extra or {})
    existing = os.environ.get("PYTHONPATH")
    env["PYTHONPATH"] = f".{os.pathsep}{existing}" if existing else "."
    return env


def docker_mount(path: Path, target: str) -> str:
    return f"type=bind,source={path},target={target}"


def target_help() -> None:
    print('Usage: uv run --no-project "${PWD}/scripts/tasks.py" <target>')
    print()
    print("Targets:")
    for name in sorted(TARGETS):
        print(f"  {name}")


def target_install() -> None:
    ensure_standard_sync_allowed()
    print_step("Installing workspace development dependencies")
    run(["uv", "sync", "--project", str(ROOT), "--all-packages", "--all-groups"])
    print_success("Dependencies installed")


def target_sync() -> None:
    ensure_standard_sync_allowed()
    print_step("Syncing workspace dependencies (runtime only)")
    run(["uv", "sync", "--project", str(ROOT), "--all-packages"])
    print_success("Dependencies synced")


def target_sync_dev() -> None:
    ensure_standard_sync_allowed()
    print_step("Syncing workspace development dependencies")
    run(["uv", "sync", "--project", str(ROOT), "--all-packages", "--all-groups"])
    print_success("Development dependencies synced")


def target_sync_dev_approved_index() -> None:
    global _approved_index_environment_prepared

    _approved_index_environment_prepared = False
    acquire_approved_index_lock()
    sync_succeeded = False
    print_step("Syncing workspace dependencies from the approved package index")
    try:
        environment = project_environment_path()
        try:
            config_path = user_uv_config_path()
            validate_approved_index_config(config_path)
        except ApprovedIndexConfigError as error:
            print(f"error: {error}", file=sys.stderr)
            raise SystemExit(1) from error
        lock_digest = lock_sha256()
        try:
            validate_public_lock(ROOT / "pyproject.toml", ROOT / "uv.lock")
        except (OSError, PublicLockError, tomllib.TOMLDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            raise SystemExit(1) from error
        with tempfile.TemporaryDirectory(prefix="aks-chaos-lab-uv-") as temporary_dir:
            requirements = Path(temporary_dir) / "requirements.txt"
            run(
                [
                    "uv",
                    "venv",
                    "--clear",
                    "--python",
                    "3.14",
                    str(environment),
                ]
            )
            run(
                [
                    "uv",
                    "export",
                    "--project",
                    str(ROOT),
                    "--quiet",
                    "--frozen",
                    "--all-packages",
                    "--all-groups",
                    "--no-emit-workspace",
                    "--no-emit-index-url",
                    "--output-file",
                    str(requirements),
                ]
            )
            try:
                validate_exported_requirements(requirements)
            except (OSError, PublicLockError) as error:
                print(f"error: {error}", file=sys.stderr)
                raise SystemExit(1) from error
            run(
                [
                    "uv",
                    "pip",
                    "sync",
                    "--require-hashes",
                    str(requirements),
                    "--python",
                    str(environment_python_path()),
                ],
                env={
                    "UV_CACHE_DIR": str(approved_index_cache_path()),
                    "UV_CONFIG_FILE": str(config_path),
                    **approved_index_credentials(config_path),
                },
            )
            site_packages = environment_site_packages_path()
            site_packages.mkdir(parents=True, exist_ok=True)
            (site_packages / "aks-chaos-lab-workspace.pth").write_text(
                f"{API_DIR.resolve()}\n{PUBLISHER_DIR.resolve()}\n",
                encoding="utf-8",
            )
        if lock_sha256() != lock_digest:
            print(
                "error: uv.lock changed while the approved-index environment was syncing. "
                "Run the sync target again.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        _approved_index_environment_prepared = True
        sync_succeeded = True
        print_success("Approved-index development dependencies synced")
    finally:
        if not sync_succeeded:
            release_approved_index_lock()


# ---------------------------------------------------------------------------
# Lint / format / typecheck — unified workspace invocations
# ---------------------------------------------------------------------------

LINT_PATHS = ["src", "scripts"]


def target_format() -> None:
    print_step("Formatting workspace Python code")
    run_uv(["ruff", "format", *LINT_PATHS])
    print_success("Code formatted")


def target_format_check() -> None:
    print_step("Checking workspace Python format")
    run_uv(["ruff", "format", "--check", *LINT_PATHS])
    print_success("Format check passed")


def target_lint() -> None:
    print_step("Linting workspace Python code")
    run_uv(["ruff", "check", "--fix", *LINT_PATHS])
    print_success("Lint passed")


def target_lint_check() -> None:
    print_step("Checking workspace Python lint")
    run_uv(["ruff", "check", *LINT_PATHS])
    print_success("Lint check passed")


def target_typecheck() -> None:
    print_step("Type checking workspace Python code")
    run_uv(["ty", "check", *LINT_PATHS])
    print_success("Type check passed")


# ---------------------------------------------------------------------------
# Tests — kept per subpackage because their pytest config and import roots differ
# ---------------------------------------------------------------------------


def target_test_api() -> None:
    print_step("Running API unit tests")
    run_uv_in(API_DIR, ["pytest", "tests/unit/", "-q"], env=pythonpath_env())
    print_success("API unit tests passed")


def target_test_publisher() -> None:
    print_step("Running external SLI publisher unit tests")
    run_uv_in(PUBLISHER_DIR, ["pytest", "tests/unit/", "-q"], env=pythonpath_env())
    print_success("External SLI publisher unit tests passed")


def target_test_hooks() -> None:
    print_step("Testing repository hooks")
    target_check_lefthook()
    run_uv(["pytest", "scripts/tests/", "-q"])
    print_success("Repository hook tests passed")


def target_test() -> None:
    target_test_api()
    target_test_publisher()
    print_success("Unit tests passed")


def target_test_cov() -> None:
    print_step("Running API unit tests with coverage")
    run_uv_in(
        API_DIR,
        [
            "pytest",
            "tests/unit/",
            "--cov=app",
            "--cov-report=term-missing",
            "--cov-report=html",
        ],
        env=pythonpath_env(),
    )
    print_success("Coverage report generated")


def target_test_integration() -> None:
    print_step("Running API integration tests")
    run_uv_in(API_DIR, ["pytest", "tests/integration/", "-q"], env=pythonpath_env())
    print_success("Integration tests passed")


def target_test_all() -> None:
    target_test()
    target_test_integration()


# ---------------------------------------------------------------------------
# QA aggregates
# ---------------------------------------------------------------------------


def target_check() -> None:
    target_lint()
    target_typecheck()
    target_test()


def target_qa_app() -> None:
    target_format_check()
    target_lint_check()
    target_typecheck()
    target_test()
    target_check_publisher_requirements()
    print_success("Application QA passed")


def target_qa_scripts() -> None:
    """Backward-compatible alias.

    Workspace ruff/ty already cover scripts/ via target_qa_app, but qa-scripts
    remains exposed for CI ergonomics and documentation continuity.
    """
    print_step("Running scripts QA (workspace lint + format + typecheck)")
    target_lint_check()
    target_format_check()
    target_typecheck()
    print_success("Scripts QA passed")


def target_inventory_repo() -> None:
    print_step("Inventorying tracked repository health coordinates")
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "repo_health.py"),
            "inventory",
            "--format",
            "text",
        ]
    )
    print_success("Repository inventory completed")


def target_check_repo_health() -> None:
    print_step("Checking repository health consistency")
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "repo_health.py"),
            "check",
            "--format",
            "text",
        ]
    )
    print_success("Repository health checks passed")


@dataclass(frozen=True)
class ReviewCheck:
    name: str
    target_name: str
    required_tools: tuple[str, ...]
    timeout_seconds: int = REVIEW_CHECK_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ReviewResult:
    name: str
    status: str
    detail: str


class ReviewSnapshotError(RuntimeError):
    pass


FAST_REVIEW_CHECKS = (
    ReviewCheck("repo-health", "target_check_repo_health", ("git",)),
    ReviewCheck("uv-version", "target_check_uv_version", ("uv",)),
    ReviewCheck("public-lock", "target_check_public_lock", ()),
    ReviewCheck(
        "publisher-requirements",
        "target_check_publisher_requirements",
        (),
    ),
)

FULL_REVIEW_CHECKS = (
    ReviewCheck("qa-app", "qa-app", ("git", "uv")),
    ReviewCheck("test-hooks", "test-hooks", ("git", "uv", "lefthook")),
    ReviewCheck("build-bicep", "build-bicep", ("git", "az")),
    ReviewCheck("lint-k8s", "lint-k8s", ("git", "docker")),
    ReviewCheck("lint-workflows", "lint-workflows", ("git", "docker")),
    ReviewCheck("compile-aw", "compile-aw", ("git", "gh"), 60),
)


def print_review_result(result: ReviewResult) -> None:
    stream = sys.stderr if result.status in {"fail", "unverified"} else sys.stdout
    print(f"[{result.status}] {result.name}: {result.detail}", file=stream)


def probe_review_tool(tool: str) -> str | None:
    if shutil.which(tool) is None:
        return f"{tool} was not found"
    command = REVIEW_TOOL_VERSION_COMMANDS.get(tool)
    if command is None:
        return f"{tool} has no configured non-interactive version probe"
    try:
        completed = run_isolated_review_command(
            command,
            cwd=ROOT,
            env={},
            timeout=REVIEW_TOOL_PREFLIGHT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"{' '.join(command)} timed out during preflight"
    except OSError as error:
        return f"{tool} could not be started during preflight: {error}"
    if completed.returncode != 0:
        return (
            f"{' '.join(command)} exited with code {completed.returncode} "
            "during preflight"
        )
    return None


def classify_review_tools(
    checks: Sequence[ReviewCheck],
) -> tuple[list[ReviewCheck], list[ReviewResult]]:
    runnable: list[ReviewCheck] = []
    results: list[ReviewResult] = []
    probe_failures: dict[str, str | None] = {}
    for check in checks:
        unavailable: list[str] = []
        for tool in check.required_tools:
            if tool not in probe_failures:
                probe_failures[tool] = probe_review_tool(tool)
            failure = probe_failures[tool]
            if failure is not None:
                unavailable.append(failure)
        if unavailable:
            result = ReviewResult(
                check.name,
                "unverified",
                "required tool preflight failed: " + "; ".join(unavailable),
            )
            print_review_result(result)
            results.append(result)
            continue
        if check.name == "compile-aw":
            try:
                completed = run_isolated_review_command(
                    ["gh", "aw", "--version"],
                    cwd=ROOT,
                    env={},
                    timeout=REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    "gh aw --version timed out during preflight",
                )
            except OSError as error:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    f"gh aw is unavailable: {error}",
                )
            else:
                if completed.returncode == 0:
                    runnable.append(check)
                    continue
                result = ReviewResult(
                    check.name,
                    "unverified",
                    f"gh aw is unavailable (preflight exited with code "
                    f"{completed.returncode})",
                )
            print_review_result(result)
            results.append(result)
            continue
        runnable.append(check)
    return runnable, results


def run_fast_review_checks() -> list[ReviewResult]:
    runnable, results = classify_review_tools(FAST_REVIEW_CHECKS)
    for check in runnable:
        action = globals().get(check.target_name)
        if not callable(action):
            result = ReviewResult(
                check.name,
                "unverified",
                f"review target is unavailable: {check.target_name}",
            )
        else:
            try:
                action()
            except KeyboardInterrupt:
                raise
            except SystemExit as error:
                result = ReviewResult(
                    check.name,
                    "fail",
                    f"check exited with code {error.code}",
                )
            except Exception as error:
                result = ReviewResult(
                    check.name,
                    "fail",
                    f"check raised {type(error).__name__}: {error}",
                )
            else:
                result = ReviewResult(check.name, "pass", "check passed")
        print_review_result(result)
        results.append(result)
    return results


def finish_review(results: Sequence[ReviewResult], success_message: str) -> None:
    if any(result.status == "fail" for result in results):
        raise SystemExit(1)
    if any(result.status == "unverified" for result in results):
        print("Repository review completed with unverified checks")
        return
    print_success(success_message)


def stop_review_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name != "nt":
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if powershell is not None:
        script = (
            f"$rootPid = {process.pid}; "
            "$all = [System.Collections.Generic.List[int]]::new(); "
            "$frontier = @($rootPid); "
            "while ($frontier.Count -gt 0) { "
            "$parents = $frontier; "
            "$frontier = @(Get-CimInstance Win32_Process | "
            "Where-Object { $parents -contains [int]$_.ParentProcessId } | "
            "ForEach-Object { [int]$_.ProcessId }); "
            "foreach ($childPid in $frontier) { $all.Add($childPid) } "
            "}; "
            "$items = $all.ToArray(); [array]::Reverse($items); "
            "foreach ($childPid in $items) { "
            "Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue "
            "}; "
            "Stop-Process -Id $rootPid -Force -ErrorAction SilentlyContinue"
        )
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
    if process.poll() is None:
        process.kill()
    process.wait()


def run_isolated_review_command(
    args: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    with (
        tempfile.TemporaryFile() as stdout_log,
        tempfile.TemporaryFile() as stderr_log,
    ):
        stdout_file = cast(BinaryIO, stdout_log)
        stderr_file = cast(BinaryIO, stderr_log)
        process = subprocess.Popen(
            resolve_command(args),
            cwd=cwd,
            env=child_env(env),
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
        timed_out = False
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_review_process_tree(process)
            timed_out = True
            return_code = process.returncode

        stdout_tail = read_review_log_tail(stdout_file)
        stderr_tail = read_review_log_tail(stderr_file)
        stream_review_log(stdout_file, sys.stdout)
        stream_review_log(stderr_file, sys.stderr)

    if timed_out:
        raise subprocess.TimeoutExpired(
            args,
            timeout,
            output=stdout_tail,
            stderr=stderr_tail,
        )
    return subprocess.CompletedProcess(
        args,
        return_code,
        stdout=stdout_tail,
        stderr=stderr_tail,
    )


def read_review_log_tail(
    log: BinaryIO,
    limit: int = REVIEW_LOG_TAIL_BYTES,
) -> bytes:
    log.flush()
    size = log.seek(0, os.SEEK_END)
    log.seek(max(0, size - limit))
    return log.read(limit)


def stream_review_log(log: BinaryIO, stream: TextIO) -> None:
    log.seek(0)
    decoder = codecs.getincrementaldecoder(stream.encoding or "utf-8")(errors="replace")
    while chunk := log.read(REVIEW_LOG_STREAM_CHUNK_BYTES):
        stream.write(decoder.decode(chunk))
    stream.write(decoder.decode(b"", final=True))
    stream.flush()


def generated_gh_aw_artifact_hashes(root: Path) -> dict[str, str]:
    generated = set((root / ".github" / "workflows").glob("*.lock.yml"))
    actions_lock = root / ".github" / "aw" / "actions-lock.json"
    if actions_lock.is_file():
        generated.add(actions_lock)
    hashes: dict[str, str] = {}
    for path in sorted(generated):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as generated_file:
            for chunk in iter(lambda: generated_file.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[path.relative_to(root).as_posix()] = digest.hexdigest()
    return hashes


def describe_generated_artifact_changes(
    before: dict[str, str],
    after: dict[str, str],
) -> str | None:
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(
        path for path in before.keys() & after.keys() if before[path] != after[path]
    )
    changes = [
        *(f"added {path}" for path in added),
        *(f"removed {path}" for path in removed),
        *(f"changed {path}" for path in changed),
    ]
    if not changes:
        return None
    return "gh-aw generated artifacts changed: " + "; ".join(changes)


REVIEW_REPOSITORY_FAILURE_PATTERNS = (
    r"\bassertionerror\b",
    r"(?m)^e\s+assert\b",
    r"(?m)^failed\s+.+::",
    r"short test summary info",
    r"\bsyntaxerror\b",
    r"\binvalid syntax\b",
    r"\bwould reformat\b",
    r"\bfound \d+ errors?\b",
    r"\berror bcp\d{3}\b",
    r"generated artifacts changed",
    r"lock(?:file| file|\.yml).*(?:changed|out of date)",
)

REVIEW_ENVIRONMENT_FAILURE_PATTERNS = (
    (
        "Docker daemon is unavailable",
        (
            r"cannot connect to the docker daemon",
            r"is the docker daemon running",
            r"error during connect:.*(?:docker_engine|docker daemon)",
            r"open //\./pipe/docker_engine",
        ),
    ),
    (
        "container image pull failed",
        (
            r"failed to pull image",
            r"image pull (?:failed|error)",
            r"error pulling image",
        ),
    ),
    (
        "DNS resolution failed",
        (
            r"temporary failure in name resolution",
            r"could not resolve host",
            r"name or service not known",
            r"\bno such host\b",
            r"getaddrinfo (?:failed|error)",
            r"dial tcp: lookup .+?:",
        ),
    ),
    (
        "TLS or certificate validation failed",
        (
            r"certificate verify failed",
            r"ssl certificate problem",
            r"unable to get local issuer certificate",
            r"\bx509: certificate",
            r"tls handshake (?:timeout|error|failed)",
        ),
    ),
    (
        "package index is unreachable",
        (
            r"(?:package|python) index.*(?:unreachable|unavailable|timed out)",
            r"failed to (?:fetch|query).*(?:package|python) index",
            r"failed to fetch.*(?:pypi|simple/)",
        ),
    ),
    (
        "network connection timed out or was refused",
        (
            r"\bconnection (?:timed out|timeout|refused)\b",
            r"\bconnect(?:ion)? timeout\b",
            r"\betimedout\b",
            r"\beconnrefused\b",
        ),
    ),
    (
        "Azure CLI could not acquire Bicep",
        (
            r"(?:failed|unable) to (?:download|install|retrieve).*\bbicep\b",
            r"error while (?:downloading|installing|retrieving).*\bbicep\b",
            r"\bbicep\b.*(?:download|install|retrieval) failed",
        ),
    ),
)


def classify_review_failure(
    _check: ReviewCheck,
    completed: subprocess.CompletedProcess[bytes],
) -> tuple[str, str]:
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    log_tail = (stdout + b"\n" + stderr).decode(errors="replace").lower()
    if any(
        re.search(pattern, log_tail) for pattern in REVIEW_REPOSITORY_FAILURE_PATTERNS
    ):
        return (
            "fail",
            f"check exited with code {completed.returncode}; "
            "repository-related test, lint, build, or generated-file failure detected",
        )
    for reason, patterns in REVIEW_ENVIRONMENT_FAILURE_PATTERNS:
        if any(re.search(pattern, log_tail) for pattern in patterns):
            return (
                "unverified",
                f"check exited with code {completed.returncode}; {reason}",
            )
    return (
        "fail",
        f"check exited with code {completed.returncode}; "
        "no explicit environment or network failure was detected",
    )


def target_review_repo_fast() -> None:
    print_step("Running fast repository review")
    finish_review(run_fast_review_checks(), "Fast repository review passed")


def safe_snapshot_path(relative_text: str) -> Path:
    posix_path = PurePosixPath(relative_text)
    windows_path = PureWindowsPath(relative_text)
    if (
        relative_text in {"", "."}
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        raise ReviewSnapshotError(f"unsafe repository path rejected: {relative_text!r}")
    return Path(relative_text)


def snapshot_file_paths() -> tuple[list[str], set[str]]:
    tracked = command_nul_output(["git", "ls-files", "-z"], cwd=ROOT)
    untracked = set(
        command_nul_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
        )
    )
    return sorted(set(tracked) | untracked), untracked


def copy_worktree_snapshot(destination: Path) -> list[str]:
    relative_texts, untracked = snapshot_file_paths()
    root_resolved = ROOT.resolve()
    destination_resolved = destination.resolve()
    issues: list[str] = []
    for relative_text in relative_texts:
        relative_path = safe_snapshot_path(relative_text)
        source = ROOT / relative_path
        if source.is_symlink():
            issues.append(
                f"symlink cannot be safely isolated and was skipped: {relative_text}"
            )
            continue
        if not source.exists():
            continue
        source_resolved = source.resolve()
        if not source_resolved.is_relative_to(root_resolved):
            raise ReviewSnapshotError(
                f"repository path resolves outside the worktree: {relative_text!r}"
            )
        source_stat = source.stat(follow_symlinks=False)
        if not stat.S_ISREG(source_stat.st_mode):
            issues.append(
                f"non-regular file cannot be safely isolated and was skipped: "
                f"{relative_text}"
            )
            continue
        if (
            relative_text in untracked
            and source_stat.st_size > REVIEW_MAX_UNTRACKED_FILE_BYTES
        ):
            issues.append(
                f"untracked file exceeds the "
                f"{REVIEW_MAX_UNTRACKED_FILE_BYTES}-byte snapshot limit and was "
                f"skipped: {relative_text}"
            )
            continue
        target = destination / relative_path
        target_resolved = target.resolve()
        if not target_resolved.is_relative_to(destination_resolved):
            raise ReviewSnapshotError(
                f"snapshot destination escapes isolation: {relative_text!r}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    return issues


def run_review_targets_isolated(
    checks: Sequence[ReviewCheck],
) -> list[ReviewResult]:
    runnable, results = classify_review_tools(checks)
    if not runnable:
        return results

    with tempfile.TemporaryDirectory(prefix="review-repo-") as temporary_directory:
        isolated_root = Path(temporary_directory).resolve()
        root_resolved = ROOT.resolve()
        if isolated_root.is_relative_to(root_resolved):
            result = ReviewResult(
                "snapshot",
                "unverified",
                "operating-system temporary directory is inside the repository",
            )
            print_review_result(result)
            results.append(result)
            for check in runnable:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    "check skipped because isolation was unavailable",
                )
                print_review_result(result)
                results.append(result)
            return results

        print_step(f"Preparing isolated review copy at {isolated_root}")
        try:
            snapshot_issues = copy_worktree_snapshot(isolated_root)
        except (OSError, ReviewSnapshotError, SystemExit) as error:
            result = ReviewResult(
                "snapshot",
                "unverified",
                f"isolated worktree could not be prepared: {error}",
            )
            print_review_result(result)
            results.append(result)
            for check in runnable:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    "check skipped because isolation was unavailable",
                )
                print_review_result(result)
                results.append(result)
            return results

        for issue in snapshot_issues:
            result = ReviewResult("snapshot", "unverified", issue)
            print_review_result(result)
            results.append(result)
        if snapshot_issues:
            for check in runnable:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    "check skipped because the isolated snapshot was incomplete",
                )
                print_review_result(result)
                results.append(result)
            return results
        try:
            # Staging the snapshot makes Lefthook include unrelated files in --file runs.
            run(["git", "init", "--quiet"], cwd=isolated_root)
        except (OSError, SystemExit) as error:
            result = ReviewResult(
                "snapshot",
                "unverified",
                f"isolated worktree could not be prepared: {error}",
            )
            print_review_result(result)
            results.append(result)
            for check in runnable:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    "check skipped because isolation was unavailable",
                )
                print_review_result(result)
                results.append(result)
            return results

        isolated_environment = {
            "PYTHONUNBUFFERED": "1",
            "UV_PROJECT_ENVIRONMENT": str(isolated_root / ".venv"),
        }
        for check in runnable:
            generated_before: dict[str, str] | None = None
            if check.name == "compile-aw":
                generated_before = generated_gh_aw_artifact_hashes(isolated_root)
            try:
                completed = run_isolated_review_command(
                    [
                        sys.executable,
                        str(isolated_root / "scripts" / "tasks.py"),
                        check.target_name,
                    ],
                    cwd=isolated_root,
                    env=isolated_environment,
                    timeout=check.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                status = "unverified"
                detail = (
                    f"check exceeded the {check.timeout_seconds}-second isolation limit"
                )
            else:
                if completed.returncode != 0:
                    status, detail = classify_review_failure(check, completed)
                elif generated_before is not None:
                    generated_after = generated_gh_aw_artifact_hashes(isolated_root)
                    generated_changes = describe_generated_artifact_changes(
                        generated_before,
                        generated_after,
                    )
                    if generated_changes is None:
                        status, detail = "pass", "check passed"
                    else:
                        status, detail = "fail", generated_changes
                else:
                    status, detail = "pass", "check passed"
            result = ReviewResult(check.name, status, detail)
            print_review_result(result)
            results.append(result)
    return results


def target_review_repo_full() -> None:
    print_step("Running full repository review")
    results = run_fast_review_checks()
    results.extend(run_review_targets_isolated(FULL_REVIEW_CHECKS))
    finish_review(results, "Full repository review passed")


def target_build_bicep() -> None:
    target_check_az()
    print_step("Building Bicep template infra/main.bicep")
    run(["az", "bicep", "build", "--file", "infra/main.bicep"])
    print_success("Bicep build passed")


def target_lint_k8s() -> None:
    target_check_docker()
    manifest_paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "k8s" / "apps" / "chaos-app").glob("*.yaml")
    )
    print_step("Validating Kubernetes manifests")
    run(
        [
            "docker",
            "run",
            "--rm",
            "--mount",
            docker_mount(ROOT, "/repo"),
            "-w",
            "/repo",
            "--entrypoint",
            "/kubeconform",
            KUBECONFORM_IMAGE,
            "-strict",
            "-summary",
            "-kubernetes-version",
            K8S_VERSION,
            "-skip",
            KUBECONFORM_SKIP,
            *manifest_paths,
        ]
    )
    print_success("Kubernetes manifest validation passed")


def target_qa_platform() -> None:
    target_build_bicep()
    target_lint_k8s()
    print_success("Platform QA passed")


def target_lint_workflows() -> None:
    target_check_docker()
    print_step("Linting GitHub Actions workflows")
    run(
        [
            "docker",
            "run",
            "--rm",
            "--mount",
            docker_mount(ROOT, "/repo"),
            "-w",
            "/repo",
            ACTIONLINT_IMAGE,
            "-color",
        ]
    )
    print_success("Workflow lint passed")


def target_compile_aw() -> None:
    target_check_gh_aw()
    print_step("Compiling agentic workflows")
    run(["gh", "aw", "compile"])
    lock_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".github" / "workflows").glob("*.lock.yml")
    )
    if not lock_files:
        print_success("No workflow lock files found")
        return
    diff = run(["git", "diff", "--quiet", "--", *lock_files], check=False)
    if diff.returncode == 0:
        print_success("gh-aw compile is clean")
        return
    if diff.returncode == 1:
        print(
            "error: gh-aw lock.yml is out of date. Commit the regenerated file.",
            file=sys.stderr,
        )
        run(["git", "--no-pager", "diff", "--stat", "--", *lock_files], check=False)
        raise SystemExit(1)
    raise SystemExit(diff.returncode)


def target_qa_workflows() -> None:
    target_lint_workflows()
    target_compile_aw()
    print_success("Workflow QA passed")


def target_qa() -> None:
    target_qa_workflows()
    target_qa_platform()
    target_qa_app()
    print_success("All QA passed")


# ---------------------------------------------------------------------------
# External tool checks
# ---------------------------------------------------------------------------


def target_check_docker() -> None:
    require_command("docker")


def target_check_az() -> None:
    require_command("az")


def target_check_gh_aw() -> None:
    require_command("gh")
    run(["gh", "aw", "--version"], check=True)


def target_check_lefthook() -> None:
    require_command("lefthook")
    run(["lefthook", "validate"])


def target_install_tools() -> None:
    target_check_docker()
    target_check_az()
    target_check_gh_aw()
    target_check_lefthook()
    print_success("All required external tools are available")


def target_check_uv_version() -> None:
    root_pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    required_version = root_pyproject["tool"]["uv"].get("required-version", "")
    required_match = re.fullmatch(
        r">=([0-9]+\.[0-9]+\.[0-9]+),<([0-9]+\.[0-9]+\.[0-9]+)",
        required_version,
    )
    if required_match is None:
        print(
            "error: [tool.uv].required-version must use "
            "a >=X.Y.Z,<X.Y.Z compatibility range",
            file=sys.stderr,
        )
        raise SystemExit(1)
    minimum_text, upper_bound_text = required_match.groups()
    minimum = tuple(int(part) for part in minimum_text.split("."))
    upper_bound = tuple(int(part) for part in upper_bound_text.split("."))
    expected_upper_bound = (minimum[0], minimum[1] + 1, 0)
    if upper_bound != expected_upper_bound:
        print(
            "error: [tool.uv].required-version must allow one uv minor series",
            file=sys.stderr,
        )
        raise SystemExit(1)

    root_uv_config = ROOT / "uv.toml"
    if root_uv_config.exists():
        uv_config = tomllib.loads(root_uv_config.read_text(encoding="utf-8"))
        if "required-version" in uv_config:
            print(
                "error: root uv.toml must not override the workspace required-version",
                file=sys.stderr,
            )
            raise SystemExit(1)

    local_version_output = command_output(
        ["uv", "--version"], allow_failure=True, quiet_stderr=True
    )
    local_version = (
        local_version_output.split()[1] if local_version_output else "not installed"
    )

    dockerfile = (API_DIR / "Dockerfile").read_text(encoding="utf-8")
    docker_match = re.search(
        r"^FROM\s+ghcr\.io/astral-sh/uv:"
        r"([0-9]+\.[0-9]+\.[0-9]+)\s+AS\s+uv\s*$",
        dockerfile,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    docker_version = docker_match.group(1) if docker_match else "not found"

    workflow_settings: list[
        tuple[Path, str | None, str | None, str | None, str | None]
    ] = []
    workflow_paths = sorted(
        (*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml"))
    )
    for workflow_path in workflow_paths:
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "uses: astral-sh/setup-uv@" not in line:
                continue
            action_indent = len(line) - len(line.lstrip())
            with_indent: int | None = None
            with_child_indent: int | None = None
            version: str | None = None
            version_file: str | None = None
            resolution_strategy: str | None = None
            working_directory: str | None = None
            for candidate in lines[index + 1 :]:
                stripped_candidate = candidate.strip()
                if not stripped_candidate:
                    continue
                candidate_indent = len(candidate) - len(candidate.lstrip())
                if candidate_indent <= action_indent and candidate.lstrip().startswith(
                    "- "
                ):
                    break
                if with_indent is None:
                    if stripped_candidate == "with:":
                        with_indent = candidate_indent
                    continue
                if candidate_indent <= with_indent:
                    break
                if with_child_indent is None:
                    with_child_indent = candidate_indent
                if candidate_indent != with_child_indent:
                    continue
                setting_match = re.match(r"([a-z-]+):(?:\s*(.*))?$", stripped_candidate)
                if setting_match is None:
                    continue
                name, raw_value = setting_match.groups()
                value = (
                    (raw_value or "").split(" #", maxsplit=1)[0].strip().strip("\"'")
                )
                if name == "version":
                    version = value
                elif name == "version-file":
                    version_file = value
                elif name == "resolution-strategy":
                    resolution_strategy = value
                elif name == "working-directory":
                    working_directory = value
            workflow_settings.append(
                (
                    workflow_path,
                    version,
                    version_file,
                    resolution_strategy,
                    working_directory,
                )
            )

    print(f"  Required workspace uv:  {required_version}")
    print(f"  Local uv:               {local_version}")
    print(f"  Pinned Docker uv:       {docker_version}")
    print("  Workflow uv:            pyproject.toml lower bound")

    if docker_version != minimum_text:
        print(
            f"error: Docker uv ({docker_version}) must remain pinned to "
            f"the minimum workspace version ({minimum_text})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    for (
        workflow_path,
        workflow_version,
        workflow_version_file,
        resolution_strategy,
        working_directory,
    ) in workflow_settings:
        if workflow_version is not None or workflow_version_file is not None:
            print(
                f"error: {workflow_path.relative_to(ROOT)} setup-uv "
                "must read required-version from the root pyproject.toml",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if working_directory is not None:
            print(
                f"error: {workflow_path.relative_to(ROOT)} setup-uv must resolve "
                "required-version from the repository root",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if resolution_strategy != "lowest":
            print(
                f"error: {workflow_path.relative_to(ROOT)} setup-uv must set "
                'resolution-strategy: "lowest"',
                file=sys.stderr,
            )
            raise SystemExit(1)

    local_match = re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", local_version)
    if local_match is None:
        print(
            f"error: Local uv version is unavailable or invalid ({local_version})",
            file=sys.stderr,
        )
        raise SystemExit(1)
    local = tuple(int(part) for part in local_version.split("."))
    if not minimum <= local < upper_bound:
        print(
            f"error: Local uv ({local_version}) does not satisfy {required_version}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print_success(
        "Local uv is compatible; CI and Docker use the workspace minimum version"
    )


def target_check_public_lock() -> None:
    print_step("Checking public uv.lock package sources")
    try:
        validate_public_lock(ROOT / "pyproject.toml", ROOT / "uv.lock")
    except (OSError, PublicLockError, tomllib.TOMLDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print_success("uv.lock contains only public PyPI package sources")


def normalized_dependency(value: str) -> str:
    return value.strip().lower()


def target_check_publisher_requirements() -> None:
    print_step("Checking external SLI publisher requirements")
    pyproject = tomllib.loads((PUBLISHER_DIR / "pyproject.toml").read_text("utf-8"))
    pyproject_deps = {
        normalized_dependency(dep) for dep in pyproject["project"]["dependencies"]
    }
    requirements = {
        normalized_dependency(line)
        for line in (PUBLISHER_DIR / "requirements.txt").read_text("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if pyproject_deps != requirements:
        only_pyproject = sorted(pyproject_deps - requirements)
        only_requirements = sorted(requirements - pyproject_deps)
        print(
            "error: Publisher pyproject.toml and requirements.txt differ",
            file=sys.stderr,
        )
        if only_pyproject:
            print(f"  only in pyproject.toml: {only_pyproject}", file=sys.stderr)
        if only_requirements:
            print(f"  only in requirements.txt: {only_requirements}", file=sys.stderr)
        raise SystemExit(1)
    print_success("External SLI publisher requirements match")


def target_clean() -> None:
    print_step("Cleaning Python caches")
    for base in (ROOT, SRC, API_DIR, PUBLISHER_DIR):
        for path in base.rglob("__pycache__"):
            shutil.rmtree(path, ignore_errors=True)
        for path in base.rglob("*.pyc"):
            path.unlink(missing_ok=True)
    for path in (
        ROOT / ".ruff_cache",
        ROOT / ".pytest_cache",
        SRC / ".pytest_cache",
        SRC / ".ruff_cache",
        API_DIR / ".pytest_cache",
        API_DIR / ".ruff_cache",
        API_DIR / "htmlcov",
        PUBLISHER_DIR / ".pytest_cache",
        PUBLISHER_DIR / ".ruff_cache",
    ):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    (API_DIR / ".coverage").unlink(missing_ok=True)
    (PUBLISHER_DIR / ".coverage").unlink(missing_ok=True)
    print_success("Caches cleaned")


def target_build() -> None:
    print_step("Building local Docker image (workspace context)")
    run(
        [
            "docker",
            "build",
            "-f",
            "src/api/Dockerfile",
            "-t",
            "aks-chaos-lab:local",
            ".",
        ],
        cwd=ROOT,
    )
    print_success("Docker image built")


def target_package_api_approved_index() -> None:
    print_step("Building the API image with the approved package index")
    config_path = user_uv_config_path()
    try:
        validate_approved_index_config(config_path)
    except ApprovedIndexConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    run(
        [
            "docker",
            "build",
            "--platform",
            "linux/arm64",
            "--build-arg",
            "UV_INDEX_MODE=approved-index",
            "--build-arg",
            f"UV_INDEX_CONFIG_SHA256={config_sha256(config_path)}",
            "--secret",
            f"id=uv-config,src={config_path}",
            "--file",
            "src/api/Dockerfile",
            "--tag",
            API_LOCAL_IMAGE,
            ".",
        ],
        cwd=ROOT,
    )
    print_success("API image built")


def target_deploy_api_approved_index() -> None:
    print_step("Deploying the prebuilt API image through azd")
    run(
        [
            "azd",
            "deploy",
            "api",
            "--from-package",
            API_LOCAL_IMAGE,
            "--no-prompt",
        ],
    )
    print_success("API deployed")


def target_run() -> None:
    print_step("Starting app on http://localhost:8000")
    run_uv_in(
        API_DIR,
        [
            "uvicorn",
            "app.main:app",
            "--reload",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        env=pythonpath_env(),
    )


def get_azd_env_value(name: str) -> str:
    if shutil.which("azd") is None:
        return ""
    return command_output(
        ["azd", "env", "get-value", name],
        allow_failure=True,
        quiet_stderr=True,
    )


def resolve_base_url() -> str:
    base_url = os.environ.get("BASE_URL", "")
    if base_url:
        return base_url.rstrip("/")

    ingress_fqdn = os.environ.get("AZURE_INGRESS_FQDN") or get_azd_env_value(
        "AZURE_INGRESS_FQDN"
    )
    if ingress_fqdn:
        ingress_host = ingress_fqdn.split("://", 1)[-1].split("/", 1)[0]
        base_url = f"http://{ingress_host}"
        print(f"BASE_URL auto-set from AZURE_INGRESS_FQDN: {base_url}", file=sys.stderr)
        return base_url

    gateway_name = os.environ.get("GATEWAY_NAME", "chaos-app")
    gateway_namespace = os.environ.get("GATEWAY_NS", "chaos-lab")
    gateway_service = f"{gateway_name}-approuting-istio"

    print(
        "BASE_URL not set, attempting to auto-detect from Gateway...", file=sys.stderr
    )
    print(
        f"  Gateway: {gateway_name} in namespace {gateway_namespace}", file=sys.stderr
    )
    require_command("kubectl")

    gateway_ip = command_output(
        [
            "kubectl",
            "get",
            "svc",
            "-n",
            gateway_namespace,
            gateway_service,
            "-o",
            "jsonpath={.status.loadBalancer.ingress[0].ip}",
        ],
        allow_failure=True,
        quiet_stderr=True,
    )
    if not gateway_ip:
        print(
            f"error: Could not get LoadBalancer IP from Gateway Service '{gateway_service}' "
            f"in namespace '{gateway_namespace}'.",
            file=sys.stderr,
        )
        print(
            "Set BASE_URL manually or check the Gateway configuration.", file=sys.stderr
        )
        raise SystemExit(1)

    base_url = f"http://{gateway_ip}"
    print(f"Auto-detected BASE_URL: {base_url}", file=sys.stderr)
    return base_url


def run_load_profile(profile: str) -> None:
    defaults = {
        "smoke": ("5", "2", "30"),
        "baseline": ("50", "5", "120"),
        "stress": ("200", "20", "300"),
        "spike": ("300", "100", "120"),
    }
    if profile not in defaults:
        print(f"error: Unknown load profile: {profile}", file=sys.stderr)
        raise SystemExit(1)

    default_users, default_spawn_rate, default_duration = defaults[profile]
    users = os.environ.get("USERS", default_users)
    spawn_rate = os.environ.get("SPAWN_RATE", default_spawn_rate)
    duration = os.environ.get("DURATION", default_duration)
    base_url = resolve_base_url()

    env = pythonpath_env({"TEST_BASE_PATH": base_url})
    print(
        f"[load] profile={profile} users={users} spawn_rate={spawn_rate}/s "
        f"duration={duration}s host={base_url}",
        file=sys.stderr,
    )
    run_uv_in(
        API_DIR,
        [
            "locust",
            "-f",
            "tests/load/locustfile.py",
            "--headless",
            "-u",
            users,
            "-r",
            spawn_rate,
            "--run-time",
            duration,
            "--host",
            base_url,
        ],
        env=env,
    )


def target_load_smoke() -> None:
    run_load_profile("smoke")


def target_load_baseline() -> None:
    run_load_profile("baseline")


def target_load_stress() -> None:
    run_load_profile("stress")


def target_load_spike() -> None:
    run_load_profile("spike")


def target_test_load() -> None:
    target_load_smoke()


TARGETS: dict[str, Callable[[], None]] = {
    "build": target_build,
    "build-bicep": target_build_bicep,
    "check": target_check,
    "check-az": target_check_az,
    "check-docker": target_check_docker,
    "check-gh-aw": target_check_gh_aw,
    "check-lefthook": target_check_lefthook,
    "check-publisher-requirements": target_check_publisher_requirements,
    "check-public-lock": target_check_public_lock,
    "check-repo-health": target_check_repo_health,
    "check-uv-version": target_check_uv_version,
    "clean": target_clean,
    "compile-aw": target_compile_aw,
    "deploy-api-approved-index": target_deploy_api_approved_index,
    "format": target_format,
    "format-check": target_format_check,
    "help": target_help,
    "install": target_install,
    "install-tools": target_install_tools,
    "inventory-repo": target_inventory_repo,
    "lint": target_lint,
    "lint-check": target_lint_check,
    "lint-k8s": target_lint_k8s,
    "lint-workflows": target_lint_workflows,
    "load-baseline": target_load_baseline,
    "load-smoke": target_load_smoke,
    "load-spike": target_load_spike,
    "load-stress": target_load_stress,
    "package-api-approved-index": target_package_api_approved_index,
    "qa": target_qa,
    "qa-app": target_qa_app,
    "qa-platform": target_qa_platform,
    "qa-scripts": target_qa_scripts,
    "qa-workflows": target_qa_workflows,
    "review-repo-fast": target_review_repo_fast,
    "review-repo-full": target_review_repo_full,
    "run": target_run,
    "sync": target_sync,
    "sync-dev": target_sync_dev,
    "sync-dev-approved-index": target_sync_dev_approved_index,
    "test": target_test,
    "test-all": target_test_all,
    "test-api": target_test_api,
    "test-cov": target_test_cov,
    "test-integration": target_test_integration,
    "test-hooks": target_test_hooks,
    "test-load": target_test_load,
    "test-publisher": target_test_publisher,
    "typecheck": target_typecheck,
}


def main(argv: Sequence[str]) -> int:
    if len(argv) == 0:
        target_help()
        return 0

    target = argv[0]
    if target == "load" and len(argv) > 1:
        run_load_profile(argv[1])
        return 0

    handler = TARGETS.get(target)
    if handler is None:
        print(f"error: Unknown target: {target}", file=sys.stderr)
        print(
            "Run 'uv run --no-project \"${PWD}/scripts/tasks.py\" help' "
            "for available targets.",
            file=sys.stderr,
        )
        return 1

    handler()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
