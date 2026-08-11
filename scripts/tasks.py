#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path

from approved_index_config import (
    UNSAFE_UV_ENVIRONMENT_VARIABLES,
    ApprovedIndexConfigError,
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
APPROVED_INDEX_STATE_DIRECTORY = ".uv-state"
APPROVED_INDEX_ENVIRONMENT_STATE_FILENAME = ".approved-index-sync.json"
APPROVED_INDEX_STATE_VERSION = 3


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


def approved_index_state_path() -> Path:
    environment = str(project_environment_path().resolve())
    environment_key = hashlib.sha256(environment.encode()).hexdigest()[:16]
    return (
        ROOT / APPROVED_INDEX_STATE_DIRECTORY / f"approved-index-{environment_key}.json"
    )


def approved_index_cache_path() -> Path:
    return ROOT / APPROVED_INDEX_STATE_DIRECTORY / "cache"


def approved_index_environment_state_path() -> Path:
    return project_environment_path() / APPROVED_INDEX_ENVIRONMENT_STATE_FILENAME


def lock_sha256() -> str:
    digest = hashlib.sha256()
    with (ROOT / "uv.lock").open("rb") as lock_file:
        for chunk in iter(lambda: lock_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_approved_index_state(status: str, lock_digest: str | None = None) -> None:
    state = {
        "schema": APPROVED_INDEX_STATE_VERSION,
        "status": status,
        "lock_sha256": lock_digest or lock_sha256(),
    }

    def write_state(state_path: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = state_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(state_path)

    if status == "ready":
        write_state(approved_index_environment_state_path())
    state_path = approved_index_state_path()
    write_state(state_path)


def read_approved_index_state() -> dict[str, object] | None:
    state_path = approved_index_state_path()
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: Invalid approved-index sync state: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    if not isinstance(state, dict):
        print("error: Invalid approved-index sync state format", file=sys.stderr)
        raise SystemExit(1)
    return state


def approved_index_run_flags() -> list[str]:
    state = read_approved_index_state()
    if state is None:
        ensure_approved_index_not_selected()
        return []
    if (
        state.get("schema") != APPROVED_INDEX_STATE_VERSION
        or state.get("status") != "ready"
    ):
        print(
            "error: The approved-index environment is incomplete. "
            'Run \'uv run --no-project "${PWD}/scripts/tasks.py" '
            "sync-dev-approved-index'.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if state.get("lock_sha256") != lock_sha256():
        print(
            "error: uv.lock changed after the approved-index environment was synced. "
            'Run \'uv run --no-project "${PWD}/scripts/tasks.py" '
            "sync-dev-approved-index'.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    environment_state_path = approved_index_environment_state_path()
    try:
        environment_state = json.loads(
            environment_state_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        print(
            f"error: Invalid approved-index environment provenance: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1) from error
    if environment_state != state:
        print(
            "error: The virtual environment does not match its approved-index state. "
            'Run \'uv run --no-project "${PWD}/scripts/tasks.py" '
            "sync-dev-approved-index'.",
            file=sys.stderr,
        )
        raise SystemExit(1)
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
    if approved_index_state_path().exists():
        print(
            "error: This environment is managed by the approved-index workflow. "
            'Run \'uv run --no-project "${PWD}/scripts/tasks.py" '
            "sync-dev-approved-index' "
            "to refresh it. To return to standard sync, remove the virtual environment "
            "and run the reset-approved-index-state target.",
            file=sys.stderr,
        )
        raise SystemExit(1)
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
    print_step("Syncing workspace dependencies from the approved package index")
    environment = project_environment_path()
    config_path = user_uv_config_path()
    try:
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
    write_approved_index_state("in-progress", lock_digest)
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
    write_approved_index_state("ready", lock_digest)
    print_success("Approved-index development dependencies synced")


def target_reset_approved_index_state() -> None:
    environment = project_environment_path()
    if environment.exists():
        print(
            f"error: Remove the virtual environment first: {environment}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    state_path = approved_index_state_path()
    state_path.unlink(missing_ok=True)
    with suppress(OSError):
        state_path.parent.rmdir()
    print_success("Approved-index state reset")


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

    workflow_versions: list[tuple[Path, str]] = []
    workflow_paths = sorted(
        (*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml"))
    )
    for workflow_path in workflow_paths:
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "uses: astral-sh/setup-uv@" not in line:
                continue
            action_indent = len(line) - len(line.lstrip())
            pinned_version = "not pinned"
            with_indent: int | None = None
            with_child_indent: int | None = None
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
                version_match = re.fullmatch(
                    r"""version:\s*["']?([0-9]+\.[0-9]+\.[0-9]+)["']?""",
                    stripped_candidate,
                )
                if version_match is not None:
                    pinned_version = version_match.group(1)
                    break
            workflow_versions.append((workflow_path, pinned_version))

    print(f"  Required workspace uv:  {required_version}")
    print(f"  Local uv:               {local_version}")
    print(f"  Pinned Docker uv:       {docker_version}")
    print(f"  Pinned workflow uv:     {minimum_text}")

    if docker_version != minimum_text:
        print(
            f"error: Docker uv ({docker_version}) must remain pinned to "
            f"the minimum workspace version ({minimum_text})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    for workflow_path, workflow_version in workflow_versions:
        if workflow_version != minimum_text:
            print(
                f"error: {workflow_path.relative_to(ROOT)} setup-uv "
                f"({workflow_version}) must remain pinned to "
                f"the minimum workspace version ({minimum_text})",
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
    print_success("Local uv is compatible and reproducible environments remain pinned")


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
    "check-uv-version": target_check_uv_version,
    "clean": target_clean,
    "compile-aw": target_compile_aw,
    "format": target_format,
    "format-check": target_format_check,
    "help": target_help,
    "install": target_install,
    "install-tools": target_install_tools,
    "lint": target_lint,
    "lint-check": target_lint_check,
    "lint-k8s": target_lint_k8s,
    "lint-workflows": target_lint_workflows,
    "load-baseline": target_load_baseline,
    "load-smoke": target_load_smoke,
    "load-spike": target_load_spike,
    "load-stress": target_load_stress,
    "qa": target_qa,
    "qa-app": target_qa_app,
    "qa-platform": target_qa_platform,
    "qa-scripts": target_qa_scripts,
    "qa-workflows": target_qa_workflows,
    "reset-approved-index-state": target_reset_approved_index_state,
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
