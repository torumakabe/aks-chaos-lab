#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ACTIONLINT_IMAGE = "rhysd/actionlint:1.7.12"
KUBECONFORM_IMAGE = "ghcr.io/yannh/kubeconform:v0.7.0"
K8S_VERSION = "1.33.0"
KUBECONFORM_SKIP = "VerticalPodAutoscaler,CiliumNetworkPolicy,Kustomization,Gateway,HTTPRoute,Instrumentation"
SCRIPT_RUFF_IGNORES = "S104,S310,S603,T201"


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
    env.setdefault("PYTHONUTF8", "1")
    if extra:
        env.update(extra)
    return env


def pythonpath_env() -> dict[str, str]:
    env = child_env()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f".{os.pathsep}{existing}" if existing else "."
    return env


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
        env=env if env is not None else child_env(),
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


def run_uv_src(args: Sequence[str], *, env: dict[str, str] | None = None) -> None:
    run(["uv", "run", *args], cwd=SRC, env=env)


def run_uv_src_dev(args: Sequence[str], *, env: dict[str, str] | None = None) -> None:
    run(["uv", "run", "--group", "dev", *args], cwd=SRC, env=env)


def docker_mount(path: Path, target: str) -> str:
    return f"type=bind,source={path},target={target}"


def target_help() -> None:
    print("Usage: uv run scripts/tasks.py <target>")
    print()
    print("Targets:")
    for name in sorted(TARGETS):
        print(f"  {name}")


def target_install() -> None:
    print_step("Installing application development dependencies")
    run(["uv", "sync", "--all-groups"], cwd=SRC)
    print_success("Dependencies installed")


def target_sync() -> None:
    print_step("Syncing application dependencies")
    run(["uv", "sync"], cwd=SRC)
    print_success("Dependencies synced")


def target_sync_dev() -> None:
    print_step("Syncing application development dependencies")
    run(["uv", "sync", "--group", "dev"], cwd=SRC)
    print_success("Development dependencies synced")


def target_format() -> None:
    print_step("Formatting application code")
    run_uv_src_dev(["ruff", "format", "app/"])
    print_success("Application code formatted")


def target_format_check() -> None:
    print_step("Checking application code format")
    run_uv_src_dev(["ruff", "format", "app/", "--check"])
    print_success("Application format check passed")


def target_lint() -> None:
    print_step("Linting application code")
    run_uv_src_dev(["ruff", "check", "app/", "--fix"], env=pythonpath_env())
    print_success("Application lint passed")


def target_typecheck() -> None:
    print_step("Type checking application code")
    run_uv_src_dev(["ty", "check", "app/"], env=pythonpath_env())
    print_success("Application type check passed")


def target_test() -> None:
    print_step("Running unit tests")
    run_uv_src_dev(["pytest", "tests/unit/", "-q"], env=pythonpath_env())
    print_success("Unit tests passed")


def target_test_cov() -> None:
    print_step("Running unit tests with coverage")
    run_uv_src_dev(
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
    print_step("Running integration tests")
    run_uv_src_dev(["pytest", "tests/integration/", "-q"], env=pythonpath_env())
    print_success("Integration tests passed")


def target_test_all() -> None:
    target_test()
    target_test_integration()


def target_format_scripts() -> None:
    print_step("Formatting repository scripts")
    run_uv_src_dev(["ruff", "format", "../scripts", "--config", "pyproject.toml"])
    print_success("Script formatting complete")


def target_format_scripts_check() -> None:
    print_step("Checking repository script format")
    run_uv_src_dev(
        ["ruff", "format", "../scripts", "--check", "--config", "pyproject.toml"]
    )
    print_success("Script format check passed")


def target_lint_scripts() -> None:
    print_step("Linting repository scripts")
    run_uv_src_dev(
        [
            "ruff",
            "check",
            "../scripts",
            "--config",
            "pyproject.toml",
            "--ignore",
            SCRIPT_RUFF_IGNORES,
            "--fix",
        ]
    )
    print_success("Script lint passed")


def target_typecheck_scripts() -> None:
    print_step("Type checking repository scripts")
    run_uv_src_dev(["ty", "check", "../scripts"], env=pythonpath_env())
    print_success("Script type check passed")


def target_qa_scripts() -> None:
    target_lint_scripts()
    target_format_scripts_check()
    target_typecheck_scripts()


def target_check() -> None:
    target_lint()
    target_typecheck()
    target_test()


def target_qa_app() -> None:
    target_format_check()
    target_lint()
    target_test()
    target_typecheck()
    print_success("Application QA passed")


def target_lint_bicep() -> None:
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
    target_lint_bicep()
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
    target_qa_scripts()
    print_success("All QA passed")


def target_check_docker() -> None:
    require_command("docker")


def target_check_az() -> None:
    require_command("az")


def target_check_gh_aw() -> None:
    require_command("gh")
    run(["gh", "aw", "--version"], check=True)


def target_install_tools() -> None:
    target_check_docker()
    target_check_az()
    target_check_gh_aw()
    print_success("All required external tools are available")


def target_check_uv_version() -> None:
    pyproject = (SRC / "pyproject.toml").read_text(encoding="utf-8")
    expected_match = re.search(r'required-version\s*=\s*">=([^"]+)"', pyproject)
    expected = expected_match.group(1) if expected_match else "not set"

    local_version_output = command_output(
        ["uv", "--version"], allow_failure=True, quiet_stderr=True
    )
    local_version = (
        local_version_output.split()[1] if local_version_output else "not installed"
    )

    dockerfile = (SRC / "Dockerfile").read_text(encoding="utf-8")
    docker_match = re.search(
        r"ghcr\.io/astral-sh/uv:([0-9]+\.[0-9]+\.[0-9]+)", dockerfile
    )
    docker_version = docker_match.group(1) if docker_match else "not found"

    print(f"  Expected (pyproject.toml required-version): {expected}")
    print(f"  Local uv:                                   {local_version}")
    print(f"  Docker uv:                                  {docker_version}")

    if docker_version != expected:
        print("error: Docker uv version mismatch with pyproject.toml", file=sys.stderr)
        raise SystemExit(1)

    if local_version != expected:
        print(f"warning: Local uv ({local_version}) differs from expected ({expected})")
        print(
            "  Patch version differences within the same minor version are compatible"
        )
    else:
        print_success("All uv versions match")


def target_clean() -> None:
    print_step("Cleaning Python caches")
    for base in (ROOT, SRC):
        for path in base.rglob("__pycache__"):
            shutil.rmtree(path, ignore_errors=True)
        for path in base.rglob("*.pyc"):
            path.unlink(missing_ok=True)
    for path in (
        ROOT / ".ruff_cache",
        SRC / ".pytest_cache",
        SRC / ".ruff_cache",
        SRC / "htmlcov",
    ):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    (SRC / ".coverage").unlink(missing_ok=True)
    print_success("Caches cleaned")


def target_build() -> None:
    print_step("Building local Docker image")
    run(
        ["docker", "build", "-f", "Dockerfile", "-t", "aks-chaos-lab:local", "."],
        cwd=SRC,
    )
    print_success("Docker image built")


def target_run() -> None:
    print_step("Starting app on http://localhost:8000")
    run_uv_src(
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

    env = child_env({"TEST_BASE_PATH": base_url})
    print(
        f"[load] profile={profile} users={users} spawn_rate={spawn_rate}/s "
        f"duration={duration}s host={base_url}",
        file=sys.stderr,
    )
    run_uv_src_dev(
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
    "check": target_check,
    "check-az": target_check_az,
    "check-docker": target_check_docker,
    "check-gh-aw": target_check_gh_aw,
    "check-uv-version": target_check_uv_version,
    "clean": target_clean,
    "compile-aw": target_compile_aw,
    "format": target_format,
    "format-check": target_format_check,
    "format-scripts": target_format_scripts,
    "format-scripts-check": target_format_scripts_check,
    "help": target_help,
    "install": target_install,
    "install-tools": target_install_tools,
    "lint": target_lint,
    "lint-bicep": target_lint_bicep,
    "lint-k8s": target_lint_k8s,
    "lint-scripts": target_lint_scripts,
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
    "run": target_run,
    "sync": target_sync,
    "sync-dev": target_sync_dev,
    "test": target_test,
    "test-all": target_test_all,
    "test-cov": target_test_cov,
    "test-integration": target_test_integration,
    "test-load": target_test_load,
    "typecheck": target_typecheck,
    "typecheck-scripts": target_typecheck_scripts,
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
            "Run 'uv run scripts/tasks.py help' for available targets.", file=sys.stderr
        )
        return 1

    handler()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
