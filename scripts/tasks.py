#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import codecs
import hashlib
import importlib
import json
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
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, TextIO, cast

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Keep sibling imports available when this script is loaded through importlib.
from aks_connection import (  # noqa: E402
    AKSConnectionError,
    Deadline,
    aks_connection,
)
from aks_connection import (  # noqa: E402
    run_command as run_aks_command,
)
from approved_index_config import (  # noqa: E402
    UNSAFE_UV_ENVIRONMENT_VARIABLES,
    ApprovedIndexConfigError,
    approved_index_is_selected,
    config_sha256,
    user_uv_config_path,
    validate_approved_index_config,
)
from public_lock import (  # noqa: E402
    PublicLockError,
    public_lock_repair_content,
    validate_exported_requirements,
    validate_public_lock,
)
from repo_health import load_kubernetes_schema_excluded_kinds  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
API_DIR = SRC / "api"
PUBLISHER_DIR = SRC / "external-sli-publisher"
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
ACTIONLINT_IMAGE = "rhysd/actionlint:1.7.12"
KUBECONFORM_IMAGE = "ghcr.io/yannh/kubeconform:v0.8.0"
HELM_VERSION = "v4.2.4"
K8S_VERSION = "1.35.0"
KUBECONFORM_SKIP_KINDS = load_kubernetes_schema_excluded_kinds(
    ROOT / ".github" / "repo-health.toml"
)
KUBECONFORM_SKIP = ",".join(KUBECONFORM_SKIP_KINDS)
CHAOS_MESH_CHART = "chaos-mesh"
_CHAOS_MESH_CHART_VERSION_PATTERN = re.compile(
    r"^[ \t]+(?:-[ \t]*)?chart:[ \t]*chaos-mesh/chaos-mesh[ \t]*\r?\n"
    r"[ \t]+(?:-[ \t]*)?version:[ \t]*(?P<value>[0-9]+\.[0-9]+\.[0-9]+)[ \t]*$",
    re.MULTILINE,
)


def read_chaos_mesh_chart_version(path: Path) -> str:
    """Read the pinned Chaos Mesh chart version from azure.yaml.

    azure.yaml is the single source of truth for this pin because it is what
    `azd deploy chaos-mesh` actually uses. Deriving the constant from it here
    (instead of duplicating the value as a separate literal) keeps the two
    from drifting apart; `.github/renovate.json` targets this azure.yaml
    coordinate directly.
    """
    text = path.read_text(encoding="utf-8")
    matches = list(_CHAOS_MESH_CHART_VERSION_PATTERN.finditer(text))
    if len(matches) != 1:
        print(
            "error: expected exactly one chaos-mesh/chaos-mesh chart version "
            f"in {path}, found {len(matches)}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return matches[0].group("value")


CHAOS_MESH_CHART_VERSION = read_chaos_mesh_chart_version(ROOT / "azure.yaml")
CHAOS_MESH_REPOSITORY = "https://charts.chaos-mesh.org"
CHAOS_MESH_VALUES = Path("infra/helm/chaos-mesh-values.yaml")
RENOVATE_CONFIG_PATH = Path(".github/renovate.json")
# .github/renovate.json contains a customManagers regex entry that keeps this
# pin current; Renovate updates its own validator image through that entry.
RENOVATE_VALIDATOR_IMAGE = "renovate/renovate:44.82.4"
RENOVATE_EXTRACT_TIMEOUT_SECONDS = 300
RENOVATE_VALIDATOR_TIMEOUT_SECONDS = 300
# renovate-config-validator prints this line before it reports any schema
# error, so its presence separates "the validator ran and rejected the config"
# (a repository defect) from "the container never got that far" (evidence gap).
RENOVATE_VALIDATOR_RAN_MARKER = "Validating"
LEFTHOOK_CI_WORKFLOW = Path(".github/workflows/ci.yml")
LEFTHOOK_CHECKSUMS_URL_TEMPLATE = (
    "https://github.com/evilmartians/lefthook/releases/download/"
    "v{version}/lefthook_checksums.txt"
)
LEFTHOOK_LINUX_ASSET_TEMPLATE = "lefthook_{version}_Linux_x86_64.gz"
LEFTHOOK_NETWORK_TIMEOUT_SECONDS = 15
APPROVED_INDEX_CACHE_DIRECTORY = Path(".uv-state") / "cache"
REVIEW_CHECK_TIMEOUT_SECONDS = 300
REVIEW_TOOL_PREFLIGHT_TIMEOUT_SECONDS = 10
REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS = 10
REVIEW_GH_AW_COMPILE_TIMEOUT_SECONDS = 60
REVIEW_RESULTS_SCHEMA_VERSION = 1
REVIEW_LOG_TAIL_BYTES = 64 * 1024
REVIEW_LOG_STREAM_CHUNK_BYTES = 64 * 1024
REVIEW_GH_AW_LIST_COMMAND = ("gh", "extension", "list")
GH_AW_MANAGED_PATHS = (
    ".github/aw/actions-lock.json",
    ".github/dependabot.yml",
    ".github/workflows/agentics-maintenance.yml",
    ".github/workflows/*.lock.yml",
)
REVIEW_TOOL_VERSION_COMMANDS: dict[str, tuple[str, ...]] = {
    "uv": ("uv", "--version"),
    "git": ("git", "--version"),
    "docker": ("docker", "--version"),
    "az": ("az", "version"),
    "gh": ("gh", "--version"),
    "helm": ("helm", "version", "--short"),
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

    _approved_index_lock_file = acquire_file_lock(approved_index_lock_path())


def acquire_file_lock(lock_path: Path) -> BinaryIO:
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

    return lock_file


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


def public_lock_repair_lock_path() -> Path:
    path = os.path.normcase(str((ROOT / "uv.lock").resolve()))
    key = hashlib.sha256(path.encode()).hexdigest()
    return Path(tempfile.gettempdir()) / "aks-chaos-lab-uv-locks" / f"public-{key}.lock"


def public_lock_git(*args: str) -> bytes:
    result = subprocess.run(
        resolve_command(["git", *args]),
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise PublicLockError(
            "Cannot repair uv.lock: Git comparison is unavailable. File preserved."
        )
    return result.stdout


def public_lock_repair_snapshot() -> tuple[str, bytes]:
    head = public_lock_git("rev-parse", "--verify", "HEAD").decode().strip()
    baseline = public_lock_git("show", f"{head}:uv.lock")
    project_bytes = public_lock_git("show", f"{head}:pyproject.toml")
    project = tomllib.loads(project_bytes.decode("utf-8"))
    members = project["tool"]["uv"]["workspace"]["members"]
    manifests = ["pyproject.toml"]
    for member in members:
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts or any(c in member for c in "*?["):
            raise PublicLockError("Cannot repair uv.lock: unsupported workspace path.")
        manifests.append(str(path / "pyproject.toml"))
    paths = ["uv.lock", *manifests]
    if public_lock_git("ls-files", "--unmerged", "--", *paths):
        raise PublicLockError("Cannot repair uv.lock: lock or manifest has conflicts.")
    if public_lock_git("diff", "--cached", "--name-only", head, "--", *paths):
        raise PublicLockError("Cannot repair uv.lock: staged lock or manifest changes.")
    if public_lock_git("diff", "--name-only", head, "--", *manifests):
        raise PublicLockError("Cannot repair uv.lock: workspace manifests changed.")
    for name in manifests:
        if (ROOT / name).read_bytes() == public_lock_git("show", f"{head}:{name}"):
            continue
        if (
            public_lock_git("hash-object", f"--path={name}", name).strip()
            != public_lock_git("rev-parse", f"{head}:{name}").strip()
        ):
            raise PublicLockError("Cannot repair uv.lock: workspace manifests changed.")
    return head, baseline


def ensure_public_lock(*, allow_repair: bool = False) -> None:
    lock_path = ROOT / "uv.lock"
    project_path = ROOT / "pyproject.toml"
    try:
        try:
            validate_public_lock(project_path, lock_path)
            return
        except PublicLockError, tomllib.TOMLDecodeError, UnicodeError:
            if not allow_repair:
                raise PublicLockError(
                    "Invalid public uv.lock; file preserved. Run the explicit "
                    "sync-dev-approved-index task for bounded recovery."
                ) from None

        # Always acquire after the venv lock, when that lock is needed.
        with acquire_file_lock(public_lock_repair_lock_path()):
            try:
                validate_public_lock(project_path, lock_path)
                return
            except PublicLockError, tomllib.TOMLDecodeError, UnicodeError:
                pass
            if lock_path.is_symlink():
                raise PublicLockError("Cannot repair a symlinked uv.lock.")
            snapshot = public_lock_repair_snapshot()
            current = lock_path.read_bytes()
            recovered = public_lock_repair_content(project_path, snapshot[1], current)
            if (
                public_lock_repair_snapshot() != snapshot
                or lock_path.read_bytes() != current
            ):
                raise PublicLockError("Cannot repair uv.lock: inputs changed.")
            temporary_root = ROOT / "tmp"
            temporary_root.mkdir(exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="public-lock-", dir=temporary_root)
            temporary_path = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(recovered)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary_path.chmod(stat.S_IMODE(lock_path.stat().st_mode))
                if (
                    public_lock_repair_snapshot() != snapshot
                    or lock_path.read_bytes() != current
                ):
                    raise PublicLockError("Cannot repair uv.lock: inputs changed.")
                os.replace(temporary_path, lock_path)
            finally:
                temporary_path.unlink(missing_ok=True)
            validate_public_lock(project_path, lock_path)
            print_success(
                "Restored public uv.lock from Git HEAD "
                "(only index/artifact URLs and omitted artifact metadata differed)"
            )
    except (
        OSError,
        PublicLockError,
        tomllib.TOMLDecodeError,
        UnicodeError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


def selected_approved_index_config() -> Path | None:
    try:
        config_path = user_uv_config_path()
    except ApprovedIndexConfigError:
        return None
    if not approved_index_is_selected(config_path):
        return None
    try:
        validate_approved_index_config(config_path)
    except ApprovedIndexConfigError as error:
        raise SystemExit(f"error: {error}") from error
    return config_path


def approved_index_run_flags() -> list[str]:
    global _approved_index_environment_prepared

    if _approved_index_environment_prepared:
        return ["--no-sync"]
    if selected_approved_index_config() is None:
        return []

    target_sync_dev_approved_index()
    return ["--no-sync"]


def ensure_approved_index_not_selected() -> None:
    if selected_approved_index_config() is None:
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
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        resolve_command(args),
        cwd=cwd,
        env=child_env(env),
        check=False,
        timeout=timeout,
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
    timeout: float | None = None,
) -> str:
    completed = subprocess.run(
        resolve_command(args),
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if quiet_stderr else subprocess.PIPE,
        env=child_env(),
        text=True,
        timeout=timeout,
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
    timeout: float | None = None,
) -> list[str]:
    completed = subprocess.run(
        resolve_command(args),
        cwd=cwd,
        check=False,
        capture_output=True,
        env=child_env(),
        timeout=timeout,
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
    for name in sorted(
        {
            *TARGETS,
            "deploy-api-approved-index",
        }
    ):
        print(f"  {name}")


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


def target_sync_dev_approved_index(*, allow_lock_repair: bool = False) -> None:
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
        ensure_public_lock(allow_repair=allow_lock_repair)
        lock_digest = lock_sha256()
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


def target_qa_app(*, check_publisher_requirements: bool = True) -> None:
    target_format_check()
    target_lint_check()
    target_typecheck()
    target_test()
    if check_publisher_requirements:
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


def target_inventory_repo(output_format: str = "text") -> None:
    if output_format == "text":
        print_step("Inventorying tracked repository health coordinates")
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "repo_health.py"),
            "inventory",
            "--format",
            output_format,
        ]
    )
    if output_format == "text":
        print_success("Repository inventory completed")


def validate_review_output(path: Path, option_name: str) -> Path:
    if not path.is_absolute():
        print(f"error: {option_name} must be an absolute path", file=sys.stderr)
        raise SystemExit(1)
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT.resolve()):
        print(
            f"error: {option_name} must be outside the repository",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not resolved.parent.is_dir():
        print(
            f"error: inventory output directory does not exist: {resolved.parent}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return resolved


def generate_repo_health_check_json(destination: Path) -> int:
    completed = subprocess.run(
        resolve_command(
            [
                sys.executable,
                str(ROOT / "scripts" / "repo_health.py"),
                "check",
                "--format",
                "json",
            ]
        ),
        cwd=ROOT,
        env=child_env(),
        check=False,
        capture_output=True,
        timeout=REVIEW_CHECK_TIMEOUT_SECONDS,
    )
    if completed.stderr:
        sys.stderr.buffer.write(completed.stderr)
        sys.stderr.flush()
    if not completed.stdout:
        print("error: repository health check produced no JSON", file=sys.stderr)
        raise SystemExit(completed.returncode or 1)
    try:
        json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        print(
            f"error: repository health check produced invalid JSON: {error}",
            file=sys.stderr,
        )
        raise SystemExit(completed.returncode or 1) from error
    destination.write_bytes(completed.stdout)
    return completed.returncode


def render_repo_health_check(report_path: Path) -> None:
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "repo_health.py"),
            "report",
            str(report_path),
        ],
        timeout=REVIEW_CHECK_TIMEOUT_SECONDS,
    )


def target_check_repo_health(inventory_json: Path | None = None) -> None:
    print_step("Checking repository health consistency")
    if inventory_json is not None:
        report_path = validate_review_output(inventory_json, "--inventory-json")
        return_code = generate_repo_health_check_json(report_path)
        render_repo_health_check(report_path)
    else:
        with tempfile.TemporaryDirectory(
            prefix="aks-chaos-lab-repo-health-"
        ) as temporary_directory:
            report_path = Path(temporary_directory) / "inventory.json"
            return_code = generate_repo_health_check_json(report_path)
            render_repo_health_check(report_path)
    if return_code != 0:
        raise SystemExit(return_code)
    print_success("Repository health checks passed")


@dataclass(frozen=True)
class ReviewCheck:
    name: str
    target_name: str
    required_tools: tuple[str, ...]
    timeout_seconds: int = REVIEW_CHECK_TIMEOUT_SECONDS
    target_arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewResult:
    name: str
    status: str
    detail: str
    reason_code: str

    def __post_init__(self) -> None:
        if self.status not in REVIEW_RESULT_STATUSES:
            raise ValueError(f"unknown review status: {self.status}")
        if not self.reason_code:
            raise ValueError("review result reason_code must not be empty")


REVIEW_RESULT_STATUSES = frozenset({"pass", "fail", "unverified", "excluded"})
REVIEW_REASON_CHECK_PASSED = "check-passed"
REVIEW_REASON_TOOL_PREFLIGHT_FAILED = "tool-preflight-failed"
REVIEW_REASON_CHECK_TIMEOUT = "check-timeout"
REVIEW_REASON_CHECK_START_FAILED = "check-start-failed"
REVIEW_REASON_REPOSITORY_FAILURE = "repository-failure"
REVIEW_REASON_ENVIRONMENT_FAILURE = "environment-failure"
REVIEW_REASON_CHECK_FAILED = "check-failed"


# The fast review is offline by contract. Every check here reaches its verdict
# from repository content alone, so it never queries a release API, a registry,
# or the Docker daemon, and it never re-discovers an update candidate that the
# update automation owns. Detecting newer versions belongs to Renovate or an
# explicit maintenance operation; fast verifies only the repository invariants
# those update procedures depend on.
FAST_REVIEW_CHECKS = (
    ReviewCheck("repo-health", "check-repo-health", ("git",)),
    ReviewCheck("uv-version", "check-uv-version", ("uv",)),
    ReviewCheck("public-lock", "check-public-lock", ()),
    ReviewCheck(
        "publisher-requirements",
        "check-publisher-requirements",
        (),
    ),
    ReviewCheck("version-pins", "check-version-pins", ()),
)

# The full review adds deterministic checks that need optional local tools. It
# runs in a caller-provided worktree and never repeats a fast check.
FULL_REVIEW_CHECKS = (
    ReviewCheck(
        "qa-app",
        "qa-app",
        ("git", "uv"),
        target_arguments=("--skip-publisher-requirements",),
    ),
    ReviewCheck("test-hooks", "test-hooks", ("git", "uv", "lefthook")),
    ReviewCheck("build-bicep", "build-bicep", ("git", "az")),
    ReviewCheck("lint-k8s", "lint-k8s", ("git", "docker")),
    ReviewCheck(
        "validate-helm-values",
        "validate-helm-values",
        ("helm",),
    ),
    ReviewCheck("lint-workflows", "lint-workflows", ("git", "docker")),
    ReviewCheck("compile-aw", "validate-aw", ("git", "gh"), 60),
)


def print_review_result(result: ReviewResult) -> None:
    stream = sys.stderr if result.status in {"fail", "unverified"} else sys.stdout
    reason = f" ({result.reason_code})" if result.reason_code else ""
    print(f"[{result.status}]{reason} {result.name}: {result.detail}", file=stream)


def probe_review_tool(tool: str) -> str | None:
    if shutil.which(tool) is None:
        return f"{tool} was not found"
    command = REVIEW_TOOL_VERSION_COMMANDS.get(tool)
    if command is None:
        return f"{tool} has no configured non-interactive version probe"
    try:
        completed = run_review_command(
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


def gh_aw_extension_listed(output: str | bytes) -> bool:
    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    return any(line.split()[:2] == ["gh", "aw"] for line in text.splitlines())


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
                REVIEW_REASON_TOOL_PREFLIGHT_FAILED,
            )
            print_review_result(result)
            results.append(result)
            continue
        if check.name == "compile-aw":
            try:
                completed = run_review_command(
                    REVIEW_GH_AW_LIST_COMMAND,
                    cwd=ROOT,
                    env={},
                    timeout=REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    "gh extension list timed out while checking gh aw",
                    REVIEW_REASON_CHECK_TIMEOUT,
                )
            except OSError as error:
                result = ReviewResult(
                    check.name,
                    "unverified",
                    f"gh aw is unavailable: {error}",
                    REVIEW_REASON_CHECK_START_FAILED,
                )
            else:
                if completed.returncode == 0 and gh_aw_extension_listed(
                    completed.stdout
                ):
                    runnable.append(check)
                    continue
                if completed.returncode == 0:
                    detail = "gh aw extension is not installed"
                else:
                    detail = (
                        "gh aw is unavailable (preflight exited with code "
                        f"{completed.returncode})"
                    )
                result = ReviewResult(
                    check.name,
                    "unverified",
                    detail,
                    REVIEW_REASON_TOOL_PREFLIGHT_FAILED,
                )
            print_review_result(result)
            results.append(result)
            continue
        runnable.append(check)
    return runnable, results


def run_fast_review_checks(
    inventory_json: Path | None = None,
) -> list[ReviewResult]:
    """Run the offline review layer once and report every verdict.

    Each check runs in its own subprocess so one failure never hides the rest.
    No check here reaches the network, a container registry, or the Docker
    daemon, so a non-zero exit means the repository broke one of its own
    invariants rather than that evidence was missing.
    """
    runnable, results = classify_review_tools(FAST_REVIEW_CHECKS)
    for check in runnable:
        arguments = [
            sys.executable,
            str(ROOT / "scripts" / "tasks.py"),
            check.target_name,
        ]
        if check.name == "repo-health" and inventory_json is not None:
            arguments.extend(["--inventory-json", str(inventory_json)])
        try:
            completed = run_review_command(
                arguments,
                cwd=ROOT,
                env={},
                timeout=check.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            result = ReviewResult(
                check.name,
                "unverified",
                f"check exceeded the {check.timeout_seconds}-second limit",
                REVIEW_REASON_CHECK_TIMEOUT,
            )
        except OSError as error:
            result = ReviewResult(
                check.name,
                "unverified",
                f"check could not be started: {error}",
                REVIEW_REASON_CHECK_START_FAILED,
            )
        else:
            if completed.returncode != 0:
                status, reason_code, detail = classify_review_failure(check, completed)
                result = ReviewResult(check.name, status, detail, reason_code)
            else:
                result = ReviewResult(
                    check.name,
                    "pass",
                    "check passed",
                    REVIEW_REASON_CHECK_PASSED,
                )
        print_review_result(result)
        results.append(result)
    return results


def review_results_document(
    mode: str, results: Sequence[ReviewResult]
) -> dict[str, Any]:
    """Build the machine-readable review result document.

    ``review-repo-full`` embeds the fast results it already produced, so the
    semantic layer reads structured ``status``/``reason_code`` values instead of
    re-interpreting review stdout or re-running the deterministic checks.
    """
    statuses = {result.status for result in results}
    unknown_statuses = statuses - REVIEW_RESULT_STATUSES
    if unknown_statuses:
        raise ValueError(f"unknown review statuses: {sorted(unknown_statuses)}")
    if any(not result.reason_code for result in results):
        raise ValueError("review result reason_code must not be empty")
    if not results:
        overall = "unverified"
    elif "fail" in statuses:
        overall = "fail"
    elif "unverified" in statuses:
        overall = "unverified"
    else:
        overall = "pass"
    return {
        "schema_version": REVIEW_RESULTS_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "status": overall,
        "coverage": {
            "total": len(results),
            "pass": sum(1 for result in results if result.status == "pass"),
            "fail": sum(1 for result in results if result.status == "fail"),
            "unverified": sum(1 for result in results if result.status == "unverified"),
            "excluded": sum(1 for result in results if result.status == "excluded"),
        },
        "checks": [
            {
                "name": result.name,
                "status": result.status,
                "reason_code": result.reason_code,
                "detail": result.detail,
            }
            for result in results
        ],
    }


def write_review_results(
    mode: str, results: Sequence[ReviewResult], output: Path | None
) -> None:
    if output is None:
        return
    document = review_results_document(mode, results)
    output.write_text(
        json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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

    powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
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


def run_review_command(
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
        except KeyboardInterrupt:
            stop_review_process_tree(process)
            raise

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
    (
        "Helm chart repository is unreachable",
        (
            r"failed to fetch .+chaos-mesh",
            r"looks like .+ is not a valid chart repository",
            r"could not download .+chaos-mesh",
        ),
    ),
    (
        "review subprocess exceeded its execution limit",
        (r"exceeded the \d+-second (?:isolation )?limit",),
    ),
)


def classify_review_failure(
    _check: ReviewCheck,
    completed: subprocess.CompletedProcess[bytes],
) -> tuple[str, str, str]:
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    log_tail = (stdout + b"\n" + stderr).decode(errors="replace").lower()
    if any(
        re.search(pattern, log_tail) for pattern in REVIEW_REPOSITORY_FAILURE_PATTERNS
    ):
        return (
            "fail",
            REVIEW_REASON_REPOSITORY_FAILURE,
            f"check exited with code {completed.returncode}; "
            "repository-related test, lint, build, or generated-file failure detected",
        )
    for reason, patterns in REVIEW_ENVIRONMENT_FAILURE_PATTERNS:
        if any(re.search(pattern, log_tail) for pattern in patterns):
            return (
                "unverified",
                REVIEW_REASON_ENVIRONMENT_FAILURE,
                f"check exited with code {completed.returncode}; {reason}",
            )
    return (
        "fail",
        REVIEW_REASON_CHECK_FAILED,
        f"check exited with code {completed.returncode}; "
        "no explicit environment or network failure was detected",
    )


def target_review_repo_fast(
    inventory_json: Path | None = None,
    results_json: Path | None = None,
) -> None:
    print_step("Running fast repository review")
    results = (
        run_fast_review_checks()
        if inventory_json is None
        else run_fast_review_checks(inventory_json)
    )
    write_review_results("fast", results, results_json)
    finish_review(
        results,
        "Fast repository review passed",
    )


def run_review_checks(checks: Sequence[ReviewCheck]) -> list[ReviewResult]:
    """Run review targets in the current dedicated worktree."""
    runnable, results = classify_review_tools(checks)
    config_path = selected_approved_index_config()
    task_environment = (
        {}
        if config_path is None
        else {
            "UV_CONFIG_FILE": str(config_path),
            **approved_index_credentials(config_path),
        }
    )
    for check in runnable:
        try:
            completed = run_review_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "tasks.py"),
                    check.target_name,
                    *check.target_arguments,
                ],
                cwd=ROOT,
                env=task_environment,
                timeout=check.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            result = ReviewResult(
                check.name,
                "unverified",
                f"check exceeded the {check.timeout_seconds}-second limit",
                REVIEW_REASON_CHECK_TIMEOUT,
            )
        except OSError as error:
            result = ReviewResult(
                check.name,
                "unverified",
                f"check could not be started: {error}",
                REVIEW_REASON_CHECK_START_FAILED,
            )
        else:
            if completed.returncode != 0:
                status, reason_code, detail = classify_review_failure(check, completed)
                result = ReviewResult(check.name, status, detail, reason_code)
            else:
                result = ReviewResult(
                    check.name,
                    "pass",
                    "check passed",
                    REVIEW_REASON_CHECK_PASSED,
                )
        print_review_result(result)
        results.append(result)
    return results


def target_review_repo_full(
    inventory_json: Path | None = None,
    results_json: Path | None = None,
) -> None:
    """Run the fast deterministic layer, then only what it cannot cover.

    The caller runs this target in a dedicated worktree. The semantic evaluation
    belongs to the review-repo agent, which consumes these structured results
    instead of repeating any check.
    """
    print_step("Running full repository review")
    results = (
        run_fast_review_checks()
        if inventory_json is None
        else run_fast_review_checks(inventory_json)
    )
    results.extend(run_review_checks(FULL_REVIEW_CHECKS))
    write_review_results("full", results, results_json)
    finish_review(results, "Full repository review passed")


def target_build_bicep() -> None:
    target_check_az()
    for template in ("infra/main.bicep", "infra/sli/main.bicep"):
        print_step(f"Building Bicep template {template}")
        command_output(["az", "bicep", "build", "--file", template, "--stdout"])
    print_success("Bicep build passed")


def target_validate_bicep_parameters() -> None:
    print_step("Validating Bicep parameter files")
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "repo_health.py"),
            "validate-bicep-parameters",
        ],
        timeout=REVIEW_CHECK_TIMEOUT_SECONDS,
    )
    print_success("Bicep parameter files passed")


def target_lint_k8s() -> None:
    target_check_docker()
    manifest_paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "k8s").rglob("*")
        if path.is_file() and path.suffix in {".yaml", ".yml"}
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


def target_validate_helm_values() -> None:
    require_command("helm")
    values_path = ROOT / CHAOS_MESH_VALUES
    if not values_path.is_file():
        print(
            f"error: Helm values file not found: {CHAOS_MESH_VALUES}", file=sys.stderr
        )
        raise SystemExit(1)
    print_step("Rendering the pinned Chaos Mesh Helm chart with repository values")
    with tempfile.TemporaryDirectory(
        prefix="aks-chaos-lab-chaos-mesh-"
    ) as temporary_directory:
        helm_root = Path(temporary_directory)
        helm_environment = {
            "HELM_CONFIG_HOME": str(helm_root / "config"),
            "HELM_CACHE_HOME": str(helm_root / "cache"),
            "HELM_DATA_HOME": str(helm_root / "data"),
        }
        run(
            ["helm", "repo", "add", CHAOS_MESH_CHART, CHAOS_MESH_REPOSITORY],
            env=helm_environment,
            timeout=60,
        )
        run(
            [
                "helm",
                "template",
                CHAOS_MESH_CHART,
                f"{CHAOS_MESH_CHART}/{CHAOS_MESH_CHART}",
                "--version",
                CHAOS_MESH_CHART_VERSION,
                "--namespace",
                "chaos-testing",
                "--values",
                str(values_path),
                "--output-dir",
                str(helm_root / "rendered"),
            ],
            env=helm_environment,
            timeout=180,
        )
    print_success("Chaos Mesh Helm values rendered successfully")


def target_qa_platform() -> None:
    target_validate_bicep_parameters()
    target_build_bicep()
    target_lint_k8s()
    target_validate_helm_values()
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


def run_gh_aw_compile(*arguments: str) -> None:
    command = ["gh", "aw", "compile", *arguments]
    if os.name != "nt":
        run(command)
        return

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if powershell is None:
        print(
            "error: PowerShell is required to run gh-aw on Windows",
            file=sys.stderr,
        )
        raise SystemExit(1)

    gh_path = resolve_command(["gh"])[0]
    powershell_arguments = ", ".join(
        "'" + argument.replace("'", "''") + "'" for argument in command[1:]
    )
    script = (
        "$process = Start-Process "
        "-FilePath $env:GH_AW_GH_PATH "
        f"-ArgumentList @({powershell_arguments}) "
        "-WorkingDirectory $env:GH_AW_ROOT "
        "-RedirectStandardInput $env:GH_AW_STDIN "
        "-RedirectStandardOutput $env:GH_AW_STDOUT "
        "-RedirectStandardError $env:GH_AW_STDERR "
        "-PassThru -Wait -NoNewWindow; "
        "exit $process.ExitCode"
    )
    with tempfile.TemporaryDirectory(prefix="aks-chaos-lab-gh-aw-") as temp_dir:
        temporary_root = Path(temp_dir)
        stdin_path = temporary_root / "stdin.txt"
        stdout_path = temporary_root / "stdout.txt"
        stderr_path = temporary_root / "stderr.txt"
        stdin_path.touch()
        completed = run_review_command(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            cwd=ROOT,
            env={
                "GH_AW_GH_PATH": gh_path,
                "GH_AW_ROOT": str(ROOT),
                "GH_AW_STDIN": str(stdin_path),
                "GH_AW_STDOUT": str(stdout_path),
                "GH_AW_STDERR": str(stderr_path),
            },
            timeout=REVIEW_GH_AW_COMPILE_TIMEOUT_SECONDS,
        )
        for output_path, stream in (
            (stdout_path, sys.stdout),
            (stderr_path, sys.stderr),
        ):
            if output_path.is_file():
                stream.buffer.write(output_path.read_bytes())
                stream.flush()
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def target_validate_aw() -> None:
    target_check_gh_aw()
    print_step("Validating agentic workflows without writing generated files")
    run_gh_aw_compile("--no-emit", "--no-check-update")
    print_success("gh-aw source validation passed")


def gh_aw_managed_file_digests() -> dict[str, str]:
    """Hash every file the gh-aw compiler is allowed to write.

    Keyed by repository-relative POSIX path so a compile run can be compared
    before and after. Missing files are simply absent, which makes creation and
    deletion visible in the comparison.
    """
    digests: dict[str, str] = {}
    for pattern in GH_AW_MANAGED_PATHS:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file():
                continue
            digests[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digests


def target_compile_aw() -> None:
    target_check_gh_aw()
    print_step("Compiling agentic workflows")
    # Compare the compiler-managed files around the compile run instead of
    # against HEAD: an uncommitted edit the compiler never touches (a comment
    # in dependabot.yml, a workflow Markdown still being written) is not a
    # stale generated artifact, while any file the compile run itself rewrites
    # is. This keeps the check honest on a dirty working tree and identical on
    # a clean CI checkout.
    before = gh_aw_managed_file_digests()
    run_gh_aw_compile()
    after = gh_aw_managed_file_digests()
    changed = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    if not changed:
        print_success("gh-aw compile is clean")
        return
    print(
        "error: gh-aw generated artifacts changed during this compile run; "
        "commit the regenerated files.",
        file=sys.stderr,
    )
    for path in changed:
        print(f"  {path}", file=sys.stderr)
    raise SystemExit(1)


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
    if not gh_aw_extension_listed(command_output(REVIEW_GH_AW_LIST_COMMAND)):
        print("error: gh aw extension is not installed", file=sys.stderr)
        raise SystemExit(1)


def target_check_lefthook() -> None:
    require_command("lefthook")
    run(["lefthook", "validate"])


def target_install_tools() -> None:
    target_check_docker()
    target_check_az()
    target_check_gh_aw()
    target_check_lefthook()
    print_success("All required external tools are available")


def github_api_request_headers() -> dict[str, str]:
    """Build GitHub request headers for an explicit maintenance command."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aks-chaos-lab-maintenance",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def open_github_url(url: str, timeout: int) -> Any:
    request = urllib.request.Request(url, headers=github_api_request_headers())
    return urllib.request.urlopen(request, timeout=timeout)


_LEFTHOOK_VERSION_PATTERN = re.compile(
    r"^[ \t]*LEFTHOOK_VERSION=(?P<value>[0-9]+\.[0-9]+\.[0-9]+)[ \t]*\r?$",
    re.MULTILINE,
)
_LEFTHOOK_SHA256_PATTERN = re.compile(
    r"^[ \t]*LEFTHOOK_SHA256=(?P<value>[0-9a-f]{64})[ \t]*\r?$", re.MULTILINE
)
_LEFTHOOK_CHECKSUM_LINE_PATTERN = re.compile(
    r"^(?P<checksum>[0-9a-f]{64})[ \t]+(?P<filename>\S+)$", re.MULTILINE
)


class LefthookChecksumUnavailableError(RuntimeError):
    pass


def _lefthook_pin_matches(
    text: str,
) -> tuple[list[re.Match[str]], list[re.Match[str]]]:
    return (
        list(_LEFTHOOK_VERSION_PATTERN.finditer(text)),
        list(_LEFTHOOK_SHA256_PATTERN.finditer(text)),
    )


def _replace_pin_value(match: re.Match[str], value: str) -> str:
    """Rewrite only the ``value`` capture span of an already-located pin.

    The pin patterns anchor on the whole line, so their match includes the
    YAML block-scalar indentation. Substituting the whole match would delete
    that indentation and produce an invalid workflow, so this splices the new
    value into the original text and leaves every surrounding byte untouched.
    """
    text = match.string
    start, end = match.span("value")
    return text[:start] + value + text[end:]


def fetch_lefthook_checksum(version: str) -> str:
    """Fetch the official Linux x86_64 checksum for a Lefthook release.

    Reads evilmartians/lefthook's published `lefthook_checksums.txt` release
    asset. Network failures raise LefthookChecksumUnavailableError instead of
    falling back silently, so the maintenance command fails instead of writing
    an unverified checksum.
    """
    url = LEFTHOOK_CHECKSUMS_URL_TEMPLATE.format(version=version)
    try:
        with open_github_url(url, LEFTHOOK_NETWORK_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LefthookChecksumUnavailableError(
            f"could not resolve host or network unreachable while fetching {url}: "
            f"{error}"
        ) from error
    asset_name = LEFTHOOK_LINUX_ASSET_TEMPLATE.format(version=version)
    matches = [
        match
        for match in _LEFTHOOK_CHECKSUM_LINE_PATTERN.finditer(payload)
        if match.group("filename") == asset_name
    ]
    if len(matches) != 1:
        raise LefthookChecksumUnavailableError(
            f"expected exactly one checksum line for {asset_name} in {url}, "
            f"found {len(matches)}"
        )
    return matches[0].group("checksum")


def _atomic_write_text(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                temporary_path.unlink()


def target_update_lefthook_pin(version: str) -> None:
    print_step(f"Updating the Lefthook pin to v{version}")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        print(
            "error: --version must be a bare X.Y.Z Lefthook release version, got "
            f"{version!r}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        checksum = fetch_lefthook_checksum(version)
    except LefthookChecksumUnavailableError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    workflow_path = ROOT / LEFTHOOK_CI_WORKFLOW
    # newline="" keeps the workflow's own line endings byte-for-byte, so a pin
    # bump never rewrites a CRLF file as LF (or the reverse) as a side effect.
    text = workflow_path.read_text(encoding="utf-8", newline="")
    version_matches, checksum_matches = _lefthook_pin_matches(text)
    if len(version_matches) != 1 or len(checksum_matches) != 1:
        print(
            "error: expected exactly one LEFTHOOK_VERSION= and one LEFTHOOK_SHA256= "
            f"in {LEFTHOOK_CI_WORKFLOW}; found {len(version_matches)} and "
            f"{len(checksum_matches)}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    updated = _replace_pin_value(version_matches[0], version)
    checksum_match = _LEFTHOOK_SHA256_PATTERN.search(updated)
    if checksum_match is None:
        print(
            "error: the LEFTHOOK_SHA256 pin disappeared while rewriting "
            f"{LEFTHOOK_CI_WORKFLOW}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    updated = _replace_pin_value(checksum_match, checksum)
    _atomic_write_text(workflow_path, updated)
    print(f"  Lefthook version:  {version}")
    print(f"  Lefthook checksum: {checksum}")
    print_success(
        "Updated LEFTHOOK_VERSION and LEFTHOOK_SHA256 together; run test-hooks to "
        "verify the new pin"
    )


AZURE_YAML_PATH = Path("azure.yaml")
FUNCTIONS_HOST_JSON_PATH = Path("src/external-sli-publisher/host.json")
_AZD_REQUIRED_VERSION_RANGE_PATTERN = re.compile(
    r"^[ \t]+azd:[ \t]*['\"]?>=[ \t]*(?P<value>[0-9]+\.[0-9]+\.[0-9]+)['\"]?[ \t]*$",
    re.MULTILINE,
)
_FUNCTIONS_BUNDLE_RANGE_PATTERN = re.compile(
    r"^\[(?P<lower_major>[0-9]+)\.\*, ?(?P<upper_major>[0-9]+)\.0\.0\)$"
)


DEPENDABOT_CONFIG_PATH = Path(".github/dependabot.yml")


def azd_minimum_version_range() -> str:
    """Return the single azd minimum-version range declared in azure.yaml.

    The azd coordinate is a deliberate lower bound (``>= X.Y.Z``), not an exact pin,
    so it is never compared against a latest release. Only its syntax and
    uniqueness are repository invariants. Renovate owns update candidates for the
    lower bound.
    """
    text = (ROOT / AZURE_YAML_PATH).read_text(encoding="utf-8")
    matches = list(_AZD_REQUIRED_VERSION_RANGE_PATTERN.finditer(text))
    if len(matches) != 1:
        raise VersionPinError(
            "expected exactly one requiredVersions.azd range in "
            f"{AZURE_YAML_PATH}, found {len(matches)}"
        )
    return f">= {matches[0].group('value')}"


def functions_bundle_support_range() -> str:
    """Return the Azure Functions extension bundle support range."""
    host_json_path = ROOT / FUNCTIONS_HOST_JSON_PATH
    try:
        host_json = json.loads(host_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VersionPinError(
            f"could not read {FUNCTIONS_HOST_JSON_PATH}: {error}"
        ) from error
    extension_bundle = (
        host_json.get("extensionBundle") if isinstance(host_json, dict) else None
    )
    bundle_version = (
        extension_bundle.get("version") if isinstance(extension_bundle, dict) else None
    )
    if not isinstance(bundle_version, str):
        raise VersionPinError(
            f"{FUNCTIONS_HOST_JSON_PATH} is missing extensionBundle.version"
        )
    if _FUNCTIONS_BUNDLE_RANGE_PATTERN.match(bundle_version) is None:
        raise VersionPinError(
            f"extensionBundle.version in {FUNCTIONS_HOST_JSON_PATH} is not a "
            f"well-formed '[X.*, Y.0.0)' support range: {bundle_version!r}"
        )
    return bundle_version


def lefthook_pin_coordinates() -> tuple[str, str]:
    """Return the pinned Lefthook version and checksum from ci.yml.

    Only the shape of the pin is checked here: exactly one version and exactly
    one 64-hex checksum, so ``update-lefthook-pin`` always has an unambiguous
    pair to rewrite. ``update-lefthook-pin`` obtains the official checksum when
    a maintainer explicitly updates the pin.
    """
    text = (ROOT / LEFTHOOK_CI_WORKFLOW).read_text(encoding="utf-8")
    version_matches, checksum_matches = _lefthook_pin_matches(text)
    if len(version_matches) != 1 or len(checksum_matches) != 1:
        raise VersionPinError(
            "expected exactly one LEFTHOOK_VERSION= and one LEFTHOOK_SHA256= in "
            f"{LEFTHOOK_CI_WORKFLOW}; found {len(version_matches)} and "
            f"{len(checksum_matches)}"
        )
    return version_matches[0].group("value"), checksum_matches[0].group("value")


def dependabot_version_updates_stopped() -> None:
    """Reject a Dependabot version-update configuration.

    Renovate owns scheduled version updates. A Dependabot ``updates:`` block
    would silently open a second pull request for the same bump, so the
    configuration file must stay absent. GitHub's security features (Dependabot
    alerts and security updates) are repository settings and are unaffected.
    """
    if (ROOT / DEPENDABOT_CONFIG_PATH).exists():
        raise VersionPinError(
            f"{DEPENDABOT_CONFIG_PATH} exists; Renovate owns every scheduled "
            "version update, so Dependabot version updates must stay disabled"
        )


def target_check_version_pins() -> None:
    """Check every repository-local version-update invariant, offline.

    This is the deterministic half of the version-update contract: it decides
    nothing about which versions are current, only that this repository still
    states the coordinates, ownership, and range syntax the scheduled
    automation relies on. It performs no network, registry, or Docker access,
    so its verdict never depends on the environment it runs in.
    """
    print_step("Checking repository version-update invariants")
    violations: list[str] = []
    try:
        dependabot_version_updates_stopped()
        print("  Dependabot version updates: disabled")
    except VersionPinError as error:
        violations.append(str(error))
    try:
        violations.extend(renovate_contract_violations(load_renovate_config()))
        print(
            "  Renovate managers:          "
            f"{len(RENOVATE_ENABLED_MANAGERS)} built-in/custom groups, "
            f"{len(RENOVATE_MANAGER_EXPECTATIONS)} custom coordinates"
        )
    except VersionPinError as error:
        violations.append(str(error))
    try:
        version, checksum = lefthook_pin_coordinates()
        print(f"  Lefthook pin:               {version} ({checksum[:12]}...)")
    except VersionPinError as error:
        violations.append(str(error))
    try:
        print(f"  Chaos Mesh chart:           {CHAOS_MESH_CHART_VERSION}")
        print(f"  azd minimum version range:  {azd_minimum_version_range()}")
        print(f"  Functions bundle range:     {functions_bundle_support_range()}")
    except VersionPinError as error:
        violations.append(str(error))
    if violations:
        for violation in violations:
            print(f"error: {violation}", file=sys.stderr)
        raise SystemExit(1)
    print_success("Repository version-update invariants hold")


@dataclass(frozen=True)
class RenovateManagerExpectation:
    description: str
    target_path: str
    pattern: str
    expected_match_count: int
    dep_name_template: str
    datasource_template: str
    versioning_template: str
    registry_url_template: str | None = None
    extract_version_template: str | None = None


RENOVATE_MANAGER_EXPECTATIONS = (
    RenovateManagerExpectation(
        "chaos-mesh-chart-version",
        "azure.yaml",
        r"chart:\s*chaos-mesh/chaos-mesh\s*\n\s*version:\s*"
        r"(?P<currentValue>[0-9]+\.[0-9]+\.[0-9]+)",
        1,
        dep_name_template="chaos-mesh",
        datasource_template="helm",
        versioning_template="semver",
        registry_url_template="https://charts.chaos-mesh.org",
    ),
    RenovateManagerExpectation(
        "actionlint-docker-image",
        "scripts/tasks.py",
        r'ACTIONLINT_IMAGE\s*=\s*"rhysd/actionlint:(?P<currentValue>[0-9][^"]*)"',
        1,
        dep_name_template="rhysd/actionlint",
        datasource_template="docker",
        versioning_template="semver-coerced",
    ),
    RenovateManagerExpectation(
        "kubeconform-docker-image",
        "scripts/tasks.py",
        r'KUBECONFORM_IMAGE\s*=\s*"ghcr\.io/yannh/kubeconform:'
        r'(?P<currentValue>v[0-9][^"]*)"',
        1,
        dep_name_template="ghcr.io/yannh/kubeconform",
        datasource_template="docker",
        versioning_template="semver-coerced",
    ),
    RenovateManagerExpectation(
        "renovate-validator-image",
        "scripts/tasks.py",
        r'RENOVATE_VALIDATOR_IMAGE\s*=\s*"renovate/renovate:'
        r'(?P<currentValue>[0-9][^"]*)"',
        1,
        dep_name_template="renovate/renovate",
        datasource_template="docker",
        versioning_template="semver-coerced",
    ),
    RenovateManagerExpectation(
        "bicep-cli-version",
        ".github/workflows/ci.yml",
        r"BICEP_VERSION=v(?P<currentValue>[0-9]+\.[0-9]+\.[0-9]+)",
        1,
        dep_name_template="Azure/bicep",
        datasource_template="github-releases",
        versioning_template="semver",
        extract_version_template=r"^v(?P<version>.+)$",
    ),
    RenovateManagerExpectation(
        "azd-required-version",
        "azure.yaml",
        r"requiredVersions:\s*\n\s*azd:\s*'>=\s*"
        r"(?P<currentValue>[0-9]+\.[0-9]+\.[0-9]+)'",
        1,
        dep_name_template="Azure/azure-dev",
        datasource_template="github-releases",
        versioning_template="semver",
        extract_version_template=r"^azure-dev-cli_(?P<version>.+)$",
    ),
    # The uv lower bound and the Dockerfile uv image must stay equal. Renovate
    # groups both coordinates into one pull request, and check-uv-version fails
    # the pull request when the bounds still disagree, so no uv bump can land
    # half-applied.
    RenovateManagerExpectation(
        "uv-required-version",
        "pyproject.toml",
        r'required-version = ">=(?P<currentValue>[0-9]+\.[0-9]+\.[0-9]+),'
        r'<[0-9]+\.[0-9]+\.[0-9]+"',
        1,
        dep_name_template="uv",
        datasource_template="pypi",
        versioning_template="semver",
    ),
)

# Renovate owns every scheduled version update in this repository. The built-in
# managers cover the Python workspace, GitHub Actions, and Dockerfiles; the
# custom managers above cover the coordinates no built-in manager can read.
RENOVATE_ENABLED_MANAGERS = (
    "pep621",
    "github-actions",
    "dockerfile",
    "custom.regex",
)
# gh-aw compiles its own workflow locks and actions lock, so Renovate must not
# read them.
RENOVATE_IGNORE_PATHS = (".github/workflows/*.lock.yml", ".github/aw/**")
# Bound each hourly Renovate burst while allowing the usual weekly batch to
# surface without relying on Renovate's generic default.
RENOVATE_PR_HOURLY_LIMIT = 5
# Repository config must override any inherited top-level approval default.
# The pep621 package rule below is the only exception.
RENOVATE_DEPENDENCY_DASHBOARD_APPROVAL = False
# Repository-relative paths the enabled built-in managers already extract. A
# custom manager targeting one of them would duplicate an update candidate.
RENOVATE_BUILTIN_MANAGER_FILE_PATTERNS = (
    r".*/Dockerfile",
    r"Dockerfile",
    r"src/[^/]+/pyproject\.toml",
)
RENOVATE_PACKAGE_RULES: dict[str, dict[str, Any]] = {
    "gh-aw-compiler-owned": {
        "description": "gh-aw-compiler-owned",
        "matchPackageNames": ["github/gh-aw-actions", "github/gh-aw-actions/**"],
        "enabled": False,
    },
    # Renovate cannot regenerate uv.lock the way this repository needs it
    # (workspace members, lowest-resolution, public PyPI sources), so Python
    # dependency updates stay candidate detection on the Dependency Dashboard
    # and the lock itself is refreshed through refresh-uv-lock.yml.
    "python-workspace-candidate-detection-only": {
        "description": "python-workspace-candidate-detection-only",
        "matchManagers": ["pep621"],
        "dependencyDashboardApproval": True,
        "skipArtifactsUpdate": True,
    },
    "uv-single-pull-request": {
        "description": "uv-single-pull-request",
        "matchPackageNames": ["uv", "ghcr.io/astral-sh/uv"],
        "groupName": "uv",
    },
}


def _renovate_regex(python_pattern: str) -> str:
    """Translate a Python `(?P<name>...)` group into Renovate's `(?<name>...)`.

    Renovate's regex manager uses RE2 (no `(?P<name>...)` support), so the
    two config files cannot literally share one string. This keeps a single
    authored pattern per coordinate and derives the other dialect from it,
    instead of hand-maintaining two independently-typed regex strings.
    """
    return re.sub(r"\(\?P<([^>]+)>", r"(?<\1>", python_pattern)


def _expected_manager_file_pattern(target_path: str) -> str:
    """Derive the Renovate `managerFilePatterns` regex for a target path.

    Renovate's file-pattern regex is matched against the whole repository
    path, so pinning it here (instead of only regex-matching target_path's
    content) also catches a manager silently being repointed at a different
    coordinate while keeping the same matchStrings.
    """
    return f"/^{re.escape(target_path)}$/"


def _expected_manager_config(expectation: RenovateManagerExpectation) -> dict[str, Any]:
    config: dict[str, Any] = {
        "customType": "regex",
        "description": expectation.description,
        "managerFilePatterns": [
            _expected_manager_file_pattern(expectation.target_path)
        ],
        "matchStrings": [_renovate_regex(expectation.pattern)],
        "depNameTemplate": expectation.dep_name_template,
        "datasourceTemplate": expectation.datasource_template,
    }
    if expectation.registry_url_template is not None:
        config["registryUrlTemplate"] = expectation.registry_url_template
    if expectation.extract_version_template is not None:
        config["extractVersionTemplate"] = _renovate_regex(
            expectation.extract_version_template
        )
    config["versioningTemplate"] = expectation.versioning_template
    return config


class VersionPinError(RuntimeError):
    """A repository-local version-update invariant is broken."""


def load_renovate_config() -> dict[str, Any]:
    path = ROOT / RENOVATE_CONFIG_PATH
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VersionPinError(
            f"could not read {RENOVATE_CONFIG_PATH}: {error}"
        ) from error
    if not isinstance(config, dict):
        raise VersionPinError(f"{RENOVATE_CONFIG_PATH} must contain a JSON object")
    return cast(dict[str, Any], config)


def renovate_contract_violations(config: dict[str, Any]) -> list[str]:
    """Return every way ``renovate.json`` departs from its declared contract.

    Renovate is the single scheduled version-update mechanism, so the contract
    covers both what it must manage (the built-in managers plus one custom
    manager per coordinate a built-in manager cannot reach) and what it must not
    do (automerge, or re-extract a coordinate another manager already owns).
    Every violation is collected instead of stopping at the first, so one run
    reports the whole picture.
    """
    violations: list[str] = []
    if config.get("enabledManagers") != list(RENOVATE_ENABLED_MANAGERS):
        violations.append(
            "renovate.json enabledManagers must be exactly "
            f"{list(RENOVATE_ENABLED_MANAGERS)} so every scheduled version "
            "update has exactly one owner"
        )
    if config.get("automerge"):
        violations.append("renovate.json must not enable automerge")
    if config.get("prHourlyLimit") != RENOVATE_PR_HOURLY_LIMIT:
        violations.append(
            "renovate.json must set prHourlyLimit to "
            f"{RENOVATE_PR_HOURLY_LIMIT} so hourly pull request creation is "
            "explicitly bounded without relying on Renovate's default"
        )
    if config.get("dependencyDashboard") is not True:
        violations.append(
            "renovate.json must set dependencyDashboard to true so update "
            "candidates Renovate does not open a pull request for stay visible"
        )
    if (
        config.get("dependencyDashboardApproval")
        is not RENOVATE_DEPENDENCY_DASHBOARD_APPROVAL
    ):
        violations.append(
            "renovate.json must set dependencyDashboardApproval to false so "
            "routine non-Python updates create branches and pull requests "
            "without Dependency Dashboard approval"
        )
    if config.get("ignorePaths") != list(RENOVATE_IGNORE_PATHS):
        violations.append(
            "renovate.json ignorePaths must be exactly "
            f"{list(RENOVATE_IGNORE_PATHS)} so the gh-aw compiler keeps sole "
            "ownership of its generated lock workflows and actions lock"
        )
    violations.extend(_renovate_package_rule_violations(config))
    violations.extend(_renovate_custom_manager_violations(config))
    return violations


def _renovate_package_rule_violations(config: dict[str, Any]) -> list[str]:
    rules = config.get("packageRules")
    if not isinstance(rules, list):
        return ["renovate.json packageRules must be an array"]
    described = {
        rule.get("description"): rule
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("description"), str)
    }
    violations: list[str] = []
    if len(described) != len(rules):
        violations.append(
            "every renovate.json packageRules entry needs a string description "
            "used as its contract key"
        )
    for description, expected in RENOVATE_PACKAGE_RULES.items():
        actual = described.get(description)
        if actual is None:
            violations.append(f"renovate.json packageRules is missing {description!r}")
        elif actual != expected:
            violations.append(
                f"renovate.json packageRules[{description}] does not match its "
                f"expected configuration {expected}"
            )
    unexpected = sorted(set(described) - set(RENOVATE_PACKAGE_RULES))
    if unexpected:
        violations.append(
            f"renovate.json packageRules has unexpected entries: {unexpected}"
        )
    return violations


def _renovate_custom_manager_violations(config: dict[str, Any]) -> list[str]:
    custom_managers = config.get("customManagers")
    if not isinstance(custom_managers, list) or not custom_managers:
        return ["renovate.json customManagers must be a non-empty array"]
    described: dict[str, dict[str, Any]] = {}
    violations: list[str] = []
    for manager in custom_managers:
        if not isinstance(manager, dict) or not isinstance(
            manager.get("description"), str
        ):
            return [
                "every renovate.json customManagers entry needs a string "
                "description used as its contract key"
            ]
        described[cast(str, manager["description"])] = cast(dict[str, Any], manager)
    expected_descriptions = {item.description for item in RENOVATE_MANAGER_EXPECTATIONS}
    if set(described) != expected_descriptions:
        missing = sorted(expected_descriptions - set(described))
        unexpected = sorted(set(described) - expected_descriptions)
        violations.append(
            "renovate.json customManagers do not match the expected coordinates "
            f"(missing={missing}, unexpected={unexpected})"
        )
    for expectation in RENOVATE_MANAGER_EXPECTATIONS:
        manager = described.get(expectation.description)
        if manager is None:
            continue
        expected_manager = _expected_manager_config(expectation)
        if manager != expected_manager:
            all_keys = sorted(set(expected_manager) | set(manager))
            differing_fields = [
                key for key in all_keys if manager.get(key) != expected_manager.get(key)
            ]
            violations.append(
                f"renovate.json customManagers[{expectation.description}] does "
                "not match its expected configuration; keep it and "
                "RENOVATE_MANAGER_EXPECTATIONS in tasks.py synchronized "
                f"(differing fields: {differing_fields})"
            )
        target_text = (ROOT / expectation.target_path).read_text(encoding="utf-8")
        matches = list(re.finditer(expectation.pattern, target_text))
        if len(matches) != expectation.expected_match_count:
            violations.append(
                f"{expectation.description} expected exactly "
                f"{expectation.expected_match_count} match(es) in "
                f"{expectation.target_path}, found {len(matches)} "
                "(no-op or ambiguous-match risk)"
            )
    violations.extend(_renovate_manager_overlap_violations())
    return violations


def _renovate_manager_overlap_violations() -> list[str]:
    """Reject a custom manager that re-extracts a built-in manager's file.

    Two managers extracting the same coordinate would produce two pull requests
    for one update, so each custom manager must target a file no enabled
    built-in manager reads.
    """
    violations: list[str] = []
    seen: dict[str, str] = {}
    for expectation in RENOVATE_MANAGER_EXPECTATIONS:
        for pattern in RENOVATE_BUILTIN_MANAGER_FILE_PATTERNS:
            if re.fullmatch(pattern, expectation.target_path):
                violations.append(
                    f"{expectation.description} targets {expectation.target_path}, "
                    "which an enabled built-in manager already extracts"
                )
        previous = seen.get(expectation.dep_name_template)
        if previous is not None:
            violations.append(
                f"{expectation.description} and {previous} both extract "
                f"{expectation.dep_name_template}"
            )
        seen[expectation.dep_name_template] = expectation.description
    return violations


class RenovateConfigInvalidError(RuntimeError):
    """The official Renovate tooling rejected the configuration."""


class RenovateEvidenceUnavailableError(RuntimeError):
    pass


def run_renovate_config_validator() -> str:
    """Validate renovate.json with the official validator and return its output.

    Raises RenovateConfigInvalidError when the validator itself reported an
    invalid configuration (a repository defect) and
    RenovateEvidenceUnavailableError when Docker or the pinned image could not
    run the validator at all (an evidence gap). Distinguishing the two lets the
    same call site report ``fail`` for a broken config and ``unverified`` for a
    missing runtime, instead of collapsing both into one exit code.
    """
    if shutil.which("docker") is None:
        raise RenovateEvidenceUnavailableError(
            "docker is unavailable, so the official renovate-config-validator "
            "could not run"
        )
    try:
        completed = subprocess.run(
            resolve_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--mount",
                    f"{docker_mount(ROOT, '/repo')},readonly",
                    "-w",
                    "/repo",
                    "--entrypoint",
                    "renovate-config-validator",
                    RENOVATE_VALIDATOR_IMAGE,
                    "--no-global",
                    RENOVATE_CONFIG_PATH.as_posix(),
                ]
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            env=child_env(),
            timeout=RENOVATE_VALIDATOR_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RenovateEvidenceUnavailableError(
            f"the official renovate-config-validator could not run: {error}"
        ) from error
    output = (os.fsdecode(completed.stdout) + os.fsdecode(completed.stderr)).strip()
    if completed.returncode == 0:
        return output
    if RENOVATE_VALIDATOR_RAN_MARKER in output:
        raise RenovateConfigInvalidError(output)
    raise RenovateEvidenceUnavailableError(
        "the official renovate-config-validator exited with code "
        f"{completed.returncode} before validating the configuration: {output}"
    )


class RenovateExtractionError(RuntimeError):
    pass


def run_renovate_extraction() -> bytes:
    """Run Renovate's own RE2 extraction over this repository, network-free.

    Runs the pinned ``renovate/renovate`` image with ``--platform=local
    --dry-run=extract`` so Renovate's actual RE2/custom-manager engine (not a
    second Python regex) extracts the coordinates. ``--network=none`` and the
    read-only mount guarantee the run performs no datasource lookups and cannot
    write into the (possibly snapshot) working tree; ``LOG_FORMAT=json`` gives a
    machine-readable ``"Extracted dependencies"`` record to parse instead of a
    fragile grep over debug prose. Returns the captured stdout bytes.

    A missing Docker runtime, a container that could not start, or a non-zero
    Renovate exit raises RenovateEvidenceUnavailableError: the cross-check did
    not produce a result, which is an evidence gap rather than proof that the
    coordinates disagree.
    """
    if shutil.which("docker") is None:
        raise RenovateEvidenceUnavailableError(
            "docker is unavailable, so Renovate's own RE2 --dry-run=extract "
            "cross-check could not run"
        )
    try:
        completed = subprocess.run(
            resolve_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network=none",
                    "--mount",
                    f"{docker_mount(ROOT, '/repo')},readonly",
                    "-w",
                    "/repo",
                    "-e",
                    "LOG_LEVEL=debug",
                    "-e",
                    "LOG_FORMAT=json",
                    "-e",
                    "RENOVATE_PLATFORM=local",
                    "-e",
                    "RENOVATE_DRY_RUN=extract",
                    "-e",
                    f"RENOVATE_CONFIG_FILE=/repo/{RENOVATE_CONFIG_PATH.as_posix()}",
                    RENOVATE_VALIDATOR_IMAGE,
                ]
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            env=child_env(),
            timeout=RENOVATE_EXTRACT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RenovateEvidenceUnavailableError(
            f"Renovate's --dry-run=extract cross-check could not run: {error}"
        ) from error
    if completed.returncode != 0:
        raise RenovateEvidenceUnavailableError(
            "Renovate's --dry-run=extract cross-check exited with code "
            f"{completed.returncode}: {os.fsdecode(completed.stderr).strip()}"
        )
    return completed.stdout


def parse_renovate_extraction(stdout: bytes) -> set[tuple[str, str, str, str]]:
    """Parse Renovate's JSON extraction log into distinct dependency tuples.

    Returns the distinct ``(manager, packageFile, depName, currentValue)``
    tuples from every ``"Extracted dependencies"`` record. Renovate can emit
    the same extracted dependency more than once, so the set collapses exact
    duplicates while still surfacing any extra distinct coordinate as an
    over-extraction. A missing extraction record raises RenovateExtractionError
    (the run produced no parseable result), which is distinct from a record
    that legitimately extracted zero dependencies.
    """
    extracted: set[tuple[str, str, str, str]] = set()
    found_marker = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith(b"{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("msg") != "Extracted dependencies":
            continue
        package_files = obj.get("packageFiles")
        if not isinstance(package_files, dict):
            continue
        found_marker = True
        for manager, files in package_files.items():
            if not isinstance(files, list):
                continue
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                package_file = entry.get("packageFile")
                deps = entry.get("deps")
                if not isinstance(deps, list):
                    continue
                for dep in deps:
                    if not isinstance(dep, dict):
                        continue
                    extracted.add(
                        (
                            str(manager),
                            str(package_file),
                            str(dep.get("depName")),
                            str(dep.get("currentValue")),
                        )
                    )
    if not found_marker:
        raise RenovateExtractionError(
            "Renovate produced no 'Extracted dependencies' record to verify"
        )
    return extracted


def expected_renovate_extractions() -> set[tuple[str, str, str, str]]:
    """Build the expected extraction tuples from the actual repository files.

    The expected ``currentValue`` is read live from each target file (the
    single source of truth for the pin), so the assertion is Renovate's RE2
    engine agreeing with the real file contents -- not the expectation table
    checking itself. Renovate reports every custom.regex manager under the
    ``regex`` group.
    """
    expected: set[tuple[str, str, str, str]] = set()
    for expectation in RENOVATE_MANAGER_EXPECTATIONS:
        target_text = (ROOT / expectation.target_path).read_text(encoding="utf-8")
        matches = list(re.finditer(expectation.pattern, target_text))
        if len(matches) != expectation.expected_match_count:
            raise RenovateExtractionError(
                f"{expectation.description} expected exactly "
                f"{expectation.expected_match_count} match(es) in "
                f"{expectation.target_path}, found {len(matches)}"
            )
        for match in matches:
            expected.add(
                (
                    "regex",
                    expectation.target_path,
                    expectation.dep_name_template,
                    match.group("currentValue"),
                )
            )
    return expected


def renovate_extraction_violations(
    expected: set[tuple[str, str, str, str]],
    actual: set[tuple[str, str, str, str]],
) -> list[str]:
    """Compare Renovate's own extraction against the declared ownership.

    The custom managers must extract exactly the expected coordinates -- no
    silent no-op, no ambiguous extra match. The built-in managers are not
    enumerated coordinate by coordinate (their content changes with every
    dependency bump), so they are asserted structurally instead: each enabled
    manager must have produced at least one coordinate, no manager outside the
    enabled set may appear, and nothing may be extracted from an ignored path.
    """
    violations: list[str] = []
    custom = {item for item in actual if item[0] == "regex"}
    if custom != expected:
        missing = sorted(expected - custom)
        unexpected = sorted(custom - expected)
        violations.append(
            "Renovate's RE2 --dry-run=extract result does not match the "
            f"expected coordinates (missing={missing}, unexpected={unexpected})"
        )
    observed_managers = {item[0] for item in actual}
    allowed = {
        "regex" if manager == "custom.regex" else manager
        for manager in RENOVATE_ENABLED_MANAGERS
    }
    if not observed_managers <= allowed:
        violations.append(
            "Renovate extracted with managers outside the enabled set: "
            f"{sorted(observed_managers - allowed)}"
        )
    for manager in sorted(allowed - observed_managers):
        violations.append(
            f"the {manager} manager extracted nothing, so its ecosystem is not "
            "actually covered by Renovate"
        )
    ignored = sorted(
        {
            item[1]
            for item in actual
            if item[1].endswith(".lock.yml") or item[1].startswith(".github/aw/")
        }
    )
    if ignored:
        violations.append(f"Renovate extracted from gh-aw generated paths: {ignored}")
    return violations


def target_check_renovate_config() -> None:
    """Validate renovate.json with Renovate's own tooling.

    This is the online half of the Renovate contract and is deliberately kept
    out of the offline review layer: it pulls the pinned ``renovate/renovate``
    image to run the official ``renovate-config-validator`` and then Renovate's
    own RE2 extraction (``--platform=local --dry-run=extract``, with
    ``--network=none`` so it performs no datasource lookup). CI runs it as a
    dedicated job; ``check-version-pins`` already covers the static contract
    everywhere else.

    A contract, schema, or extraction defect exits 1. A missing Docker runtime
    also exits 1, because a target that cannot obtain its evidence must not
    report success.
    """
    print_step("Validating the Renovate configuration with official tooling")
    try:
        violations = renovate_contract_violations(load_renovate_config())
    except VersionPinError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    if violations:
        for violation in violations:
            print(f"error: {violation}", file=sys.stderr)
        raise SystemExit(1)
    print(f"  Static contract:  {len(RENOVATE_MANAGER_EXPECTATIONS)} custom managers")
    try:
        expected = expected_renovate_extractions()
        run_renovate_config_validator()
        actual = parse_renovate_extraction(run_renovate_extraction())
    except (
        RenovateConfigInvalidError,
        RenovateExtractionError,
        RenovateEvidenceUnavailableError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    violations = renovate_extraction_violations(expected, actual)
    if violations:
        for violation in violations:
            print(f"error: {violation}", file=sys.stderr)
        raise SystemExit(1)
    print("  Official schema:  accepted by renovate-config-validator")
    print(
        f"  RE2 extraction:   {len(actual)} coordinates from {len(RENOVATE_ENABLED_MANAGERS)} managers"
    )
    print_success(
        "Renovate configuration matches its declared contract, is schema-valid, "
        "and its RE2 extraction matches the expected coordinates"
    )


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
        r"([0-9]+\.[0-9]+\.[0-9]+)(?:@sha256:[0-9a-f]+)?\s+AS\s+uv\s*$",
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


def target_package_api_approved_index(*, allow_lock_repair: bool = False) -> None:
    from api_artifact import ApiArtifactError, build_api_image

    print_step("Building the API image with the approved package index")
    try:
        config_path = user_uv_config_path()
        validate_approved_index_config(config_path)
    except ApprovedIndexConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    ensure_public_lock(allow_repair=allow_lock_repair)
    try:
        image = build_api_image(ROOT, config_path, config_sha256(config_path))
    except ApiArtifactError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print_success("API image built; pass this reference to deploy --image:")
    print(image)


def deployment_environment_name(deadline: Deadline) -> str:
    name = os.environ.get("AZURE_ENV_NAME", "").strip()
    if not name:
        print(
            "error: run this task through azd exec -e <environment>.", file=sys.stderr
        )
        raise SystemExit(1)
    actual = run_aks_command(
        ["azd", "env", "get-value", "AZURE_ENV_NAME", "-e", name],
        deadline=deadline,
        env=os.environ,
        operation="resolve azd environment",
    )
    if actual != name:
        print(
            "error: azd environment does not match the task environment.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return name


def node_provisioning_enabled() -> bool:
    value = os.environ.get("AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING")
    if value is None or value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    print(
        "error: AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING must be true or false.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def target_deploy_api_approved_index(image: str) -> None:
    from api_artifact import (
        ApiArtifactError,
        inspect_local_image,
        inspect_published_image,
        verify_running_api,
    )

    try:
        local_id = inspect_local_image(image)
        environment = deployment_environment_name(Deadline(30))
    except (ApiArtifactError, AKSConnectionError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print_step("Deploying the prebuilt API image through azd")
    run(
        [
            "azd",
            "deploy",
            "api",
            "-e",
            environment,
            "--from-package",
            image,
            "--no-prompt",
        ],
    )
    try:
        deadline = Deadline(600)
        remote_ref = run_aks_command(
            ["azd", "env", "get-value", "SERVICE_API_IMAGE_NAME", "-e", environment],
            deadline=deadline,
            env=os.environ,
            operation="read published API image",
        )
        published = inspect_published_image(local_id, remote_ref, deadline=deadline)
        with aks_connection(deadline=deadline) as connection:
            connection.rollout("chaos-app", "chaos-lab", 600)
            deployment = connection.json_resource(
                ["get", "deployment", "chaos-app", "-n", "chaos-lab"]
            )
            replicasets = connection.json_resource(
                ["get", "replicasets", "-n", "chaos-lab", "-l", "app=chaos-app"]
            )
            pods = connection.json_resource(
                ["get", "pods", "-n", "chaos-lab", "-l", "app=chaos-app"]
            )
            verify_running_api(published, deployment, pods, replicasets)
            deadline.remaining()
    except (ApiArtifactError, AKSConnectionError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print_success(f"API is running the supplied image: {remote_ref}")


def target_deploy_node_provisioning() -> None:
    try:
        deadline = Deadline(600)
        environment = deployment_environment_name(deadline)
        if not node_provisioning_enabled():
            print_step("Skipping node-provisioning: NAP is disabled")
            return
        with aks_connection(deadline=deadline) as connection:
            for crd in (
                "nodepools.karpenter.sh",
                "aksnodeclasses.karpenter.azure.com",
            ):
                for condition in ("create", "condition=Established"):
                    connection.kubectl(
                        [
                            "wait",
                            f"--for={condition}",
                            f"crd/{crd}",
                            f"--timeout={deadline.remaining():.3f}s",
                        ]
                    )
        run(["azd", "deploy", "node-provisioning", "-e", environment, "--no-prompt"])
    except AKSConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print_success("Node provisioning declarations deployed")


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


TARGETS: dict[str, Callable[[], None]] = {
    "build": target_build,
    "build-bicep": target_build_bicep,
    "check-az": target_check_az,
    "check-docker": target_check_docker,
    "check-gh-aw": target_check_gh_aw,
    "check-lefthook": target_check_lefthook,
    "check-publisher-requirements": target_check_publisher_requirements,
    "check-public-lock": target_check_public_lock,
    "check-renovate-config": target_check_renovate_config,
    "check-repo-health": target_check_repo_health,
    "check-uv-version": target_check_uv_version,
    "check-version-pins": target_check_version_pins,
    "clean": target_clean,
    "compile-aw": target_compile_aw,
    "deploy-node-provisioning": target_deploy_node_provisioning,
    "format": target_format,
    "format-check": target_format_check,
    "help": target_help,
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
    "test-publisher": target_test_publisher,
    "typecheck": target_typecheck,
    "validate-aw": target_validate_aw,
    "validate-bicep-parameters": target_validate_bicep_parameters,
    "validate-helm-values": target_validate_helm_values,
}


def main(argv: Sequence[str]) -> int:
    if len(argv) == 0:
        target_help()
        return 0

    target = argv[0]
    if target in {"sync-dev-approved-index", "package-api-approved-index"}:
        parser = argparse.ArgumentParser(prog=f"tasks.py {target}")
        parser.parse_args(argv[1:])
        if target == "sync-dev-approved-index":
            target_sync_dev_approved_index(allow_lock_repair=True)
        else:
            target_package_api_approved_index(allow_lock_repair=True)
        return 0
    if target == "deploy-api-approved-index":
        parser = argparse.ArgumentParser(prog=f"tasks.py {target}")
        parser.add_argument("--image", required=True)
        args = parser.parse_args(argv[1:])
        target_deploy_api_approved_index(args.image)
        return 0
    if target == "load" and len(argv) > 1:
        run_load_profile(argv[1])
        return 0
    if target == "inventory-repo":
        parser = argparse.ArgumentParser(prog="tasks.py inventory-repo")
        parser.add_argument(
            "--format",
            choices=("json", "text"),
            default="text",
        )
        args = parser.parse_args(argv[1:])
        target_inventory_repo(args.format)
        return 0
    if target == "check-repo-health":
        parser = argparse.ArgumentParser(prog="tasks.py check-repo-health")
        parser.add_argument("--inventory-json", type=Path)
        args = parser.parse_args(argv[1:])
        target_check_repo_health(args.inventory_json)
        return 0
    if target == "update-lefthook-pin":
        parser = argparse.ArgumentParser(prog="tasks.py update-lefthook-pin")
        parser.add_argument("--version", required=True)
        args = parser.parse_args(argv[1:])
        target_update_lefthook_pin(args.version)
        return 0
    if target in {"review-repo-fast", "review-repo-full"}:
        parser = argparse.ArgumentParser(prog=f"tasks.py {target}")
        parser.add_argument("--inventory-json", type=Path)
        parser.add_argument("--results-json", type=Path)
        args = parser.parse_args(argv[1:])
        review_target = (
            target_review_repo_fast
            if target == "review-repo-fast"
            else target_review_repo_full
        )
        review_target(args.inventory_json, args.results_json)
        return 0
    if target == "qa-app":
        parser = argparse.ArgumentParser(prog="tasks.py qa-app")
        parser.add_argument(
            "--skip-publisher-requirements",
            action="store_true",
            help="skip the publisher dependency check when an enclosing task ran it",
        )
        args = parser.parse_args(argv[1:])
        target_qa_app(check_publisher_requirements=not args.skip_publisher_requirements)
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
