from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def load_tasks() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "review_repository_tasks", SCRIPTS_DIR / "tasks.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/tasks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tasks = load_tasks()
real_probe_review_tool = tasks.probe_review_tool


@pytest.fixture(autouse=True)
def assume_review_tools_pass_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "probe_review_tool", lambda _tool: None)


def test_review_targets_are_registered() -> None:
    assert tasks.TARGETS["review-repo-fast"] is tasks.target_review_repo_fast
    assert tasks.TARGETS["review-repo-full"] is tasks.target_review_repo_full
    assert (
        tasks.TARGETS["validate-bicep-parameters"]
        is tasks.target_validate_bicep_parameters
    )
    assert tasks.TARGETS["validate-helm-values"] is tasks.target_validate_helm_values


def test_kubernetes_validation_includes_nested_yaml_and_yml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "k8s/nested").mkdir(parents=True)
    (repository / "k8s/root.yaml").write_text("kind: Namespace\n", encoding="utf-8")
    (repository / "k8s/nested/resource.yml").write_text(
        "kind: ConfigMap\n", encoding="utf-8"
    )
    (repository / "k8s/ignored.txt").write_text("ignored\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "target_check_docker", lambda: None)
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: (
            calls.append(tuple(args)) or subprocess.CompletedProcess(args, 0)
        ),
    )

    tasks.target_lint_k8s()

    assert calls[0][-2:] == (
        "k8s/nested/resource.yml",
        "k8s/root.yaml",
    )
    assert "-skip" in calls[0]
    assert tasks.KUBECONFORM_SKIP in calls[0]


def test_helm_values_validation_uses_pinned_chart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    values = repository / tasks.CHAOS_MESH_VALUES
    values.parent.mkdir(parents=True)
    values.write_text("chaosDaemon:\n  runtime: containerd\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "require_command", lambda _command: None)
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **kwargs: (
            calls.append((tuple(args), kwargs)) or subprocess.CompletedProcess(args, 0)
        ),
    )

    tasks.target_validate_helm_values()

    assert calls[0][0] == (
        "helm",
        "repo",
        "add",
        "chaos-mesh",
        tasks.CHAOS_MESH_REPOSITORY,
    )
    command, render_options = calls[1]
    assert command[:4] == (
        "helm",
        "template",
        "chaos-mesh",
        "chaos-mesh/chaos-mesh",
    )
    assert tasks.CHAOS_MESH_CHART_VERSION in command
    assert str(values) in command
    assert render_options["timeout"] == 180
    assert render_options["env"] == calls[0][1]["env"]


def test_fast_review_timeout_is_unverified_and_next_check_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def run_check(
        args: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        target = args[2]
        calls.append(target)
        if target == "check-repo-health":
            raise subprocess.TimeoutExpired(args, tasks.REVIEW_CHECK_TIMEOUT_SECONDS)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run_isolated_review_command", run_check)

    results = tasks.run_fast_review_checks()

    assert calls == [check.target_name for check in tasks.FAST_REVIEW_CHECKS]
    assert [(result.name, result.status) for result in results] == [
        ("repo-health", "unverified"),
        ("uv-version", "pass"),
        ("public-lock", "pass"),
        ("publisher-requirements", "pass"),
    ]


def test_qa_app_cli_can_skip_publisher_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        tasks,
        "target_qa_app",
        lambda *, check_publisher_requirements=True: calls.append(
            check_publisher_requirements
        ),
    )

    result = tasks.main(["qa-app", "--skip-publisher-requirements"])

    assert result == 0
    assert calls == [False]


def test_review_repo_fast_reuses_offline_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    target_names = tuple(check.target_name for check in tasks.FAST_REVIEW_CHECKS)
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda args, **_kwargs: (
            calls.append(args[2]) or subprocess.CompletedProcess(args, 0)
        ),
    )

    tasks.target_review_repo_fast()

    assert calls == list(target_names)


def test_review_repo_fast_marks_missing_tools_unverified_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        tasks,
        "probe_review_tool",
        lambda command: f"{command} was not found" if command == "uv" else None,
    )
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda args, **_kwargs: (
            calls.append(args[2]) or subprocess.CompletedProcess(args, 0)
        ),
    )

    tasks.target_review_repo_fast()

    assert calls == [
        "check-repo-health",
        "check-public-lock",
        "check-publisher-requirements",
    ]
    output = capsys.readouterr()
    assert "[pass] repo-health: check passed" in output.out
    assert (
        "[unverified] uv-version: required tool preflight failed: uv was not found"
        in output.err
    )
    assert "[pass] public-lock: check passed" in output.out
    assert "Repository review completed with unverified checks" in output.out
    assert "Fast repository review passed" not in output.out


def test_review_repo_fast_marks_real_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def run_check(
        args: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        target_name = args[2]
        calls.append(target_name)
        if target_name == "check-repo-health":
            return subprocess.CompletedProcess(
                args,
                7,
                stdout=b"",
                stderr=b"AssertionError: repository health failed",
            )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run_isolated_review_command", run_check)

    with pytest.raises(SystemExit) as error:
        tasks.target_review_repo_fast()

    assert error.value.code == 1
    assert calls == [
        "check-repo-health",
        "check-uv-version",
        "check-public-lock",
        "check-publisher-requirements",
    ]
    assert "[fail] repo-health:" in capsys.readouterr().err


def test_review_repo_fast_marks_start_failure_unverified_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def run_check(
        args: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        target_name = args[2]
        calls.append(target_name)
        if target_name == "check-repo-health":
            raise OSError("cannot execute")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run_isolated_review_command", run_check)

    tasks.target_review_repo_fast()

    assert calls == [
        "check-repo-health",
        "check-uv-version",
        "check-public-lock",
        "check-publisher-requirements",
    ]
    assert (
        "[unverified] repo-health: check could not be started: cannot execute"
        in capsys.readouterr().err
    )


def test_review_repo_fast_propagates_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        tasks.target_review_repo_fast()


def test_review_checks_preclassify_each_required_tool() -> None:
    fast_tools = {
        check.name: check.required_tools for check in tasks.FAST_REVIEW_CHECKS
    }
    full_tools = {
        check.name: check.required_tools for check in tasks.FULL_REVIEW_CHECKS
    }

    assert fast_tools == {
        "repo-health": ("git",),
        "uv-version": ("uv",),
        "public-lock": (),
        "publisher-requirements": (),
    }
    assert full_tools == {
        "qa-app": ("git", "uv"),
        "test-hooks": ("git", "uv", "lefthook"),
        "build-bicep": ("git", "az"),
        "lint-k8s": ("git", "docker"),
        "validate-helm-values": ("helm",),
        "lint-workflows": ("git", "docker"),
        "compile-aw": ("git", "gh"),
    }
    assert tasks.FULL_REVIEW_CHECKS[0].target_arguments == (
        "--skip-publisher-requirements",
    )


def test_kubernetes_schema_exclusions_have_one_configuration_source() -> None:
    with (REPO_ROOT / ".github" / "repo-health.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    assert tuple(sorted(config["kubernetes_schema_excluded_kinds"])) == (
        tasks.KUBECONFORM_SKIP_KINDS
    )
    assert ",".join(tasks.KUBECONFORM_SKIP_KINDS) == tasks.KUBECONFORM_SKIP


def test_review_qa_app_skips_publisher_check_already_run_by_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    for target_name in (
        "target_format_check",
        "target_lint_check",
        "target_typecheck",
        "target_test",
        "target_check_publisher_requirements",
    ):
        monkeypatch.setattr(
            tasks,
            target_name,
            lambda name=target_name: calls.append(name),
        )

    tasks.target_qa_app(check_publisher_requirements=False)

    assert calls == [
        "target_format_check",
        "target_lint_check",
        "target_typecheck",
        "target_test",
    ]


def test_repository_health_targets_use_current_python(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: calls.append(tuple(args)),
    )

    tasks.target_inventory_repo()
    tasks.target_inventory_repo("json")

    script = str(tasks.ROOT / "scripts" / "repo_health.py")
    assert calls == [
        (sys.executable, script, "inventory", "--format", "text"),
        (sys.executable, script, "inventory", "--format", "json"),
    ]
    output = capsys.readouterr().out
    assert output.count("Inventorying tracked repository health coordinates") == 1
    assert output.count("Repository inventory completed") == 1


def test_review_repo_full_passes_one_inventory_output_to_fast_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "inventory.json"
    calls: list[object] = []
    monkeypatch.setattr(
        tasks,
        "run_fast_review_checks",
        lambda inventory_json=None: calls.append(inventory_json) or [],
    )
    monkeypatch.setattr(
        tasks,
        "run_review_targets_isolated",
        lambda checks: calls.append(tuple(check.name for check in checks)) or [],
    )

    tasks.target_review_repo_full(output_path)

    assert calls == [
        output_path,
        (
            "qa-app",
            "test-hooks",
            "build-bicep",
            "lint-k8s",
            "validate-helm-values",
            "lint-workflows",
            "compile-aw",
        ),
    ]


def test_review_repo_cli_accepts_inventory_json_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "inventory.json"
    calls: list[Path | None] = []
    monkeypatch.setattr(
        tasks,
        "target_review_repo_full",
        lambda inventory_json=None: calls.append(inventory_json),
    )

    assert (
        tasks.main(
            [
                "review-repo-full",
                "--inventory-json",
                str(output_path),
            ]
        )
        == 0
    )
    assert calls == [output_path]


def test_check_repo_health_generates_and_renders_one_json_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "inventory.json"
    calls: list[tuple[str, Path]] = []

    def generate(destination: Path) -> int:
        calls.append(("generate", destination))
        destination.write_text('{"schema_version": "2.1"}\n', encoding="utf-8")
        return 0

    monkeypatch.setattr(tasks, "generate_repo_health_check_json", generate)
    monkeypatch.setattr(
        tasks,
        "render_repo_health_check",
        lambda report: calls.append(("render", report)),
    )

    tasks.target_check_repo_health(output_path)

    assert calls == [
        ("generate", output_path),
        ("render", output_path),
    ]


def test_review_repo_fast_skips_only_uv_version_when_uv_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")

    def version_probe(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env, timeout
        if tuple(args) == tasks.REVIEW_TOOL_VERSION_COMMANDS["uv"]:
            return subprocess.CompletedProcess(args, 1)
        if tuple(args) == tasks.REVIEW_TOOL_VERSION_COMMANDS["git"]:
            return subprocess.CompletedProcess(args, 0)
        calls.append(args[2])
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run_isolated_review_command", version_probe)
    monkeypatch.setattr(tasks, "probe_review_tool", real_probe_review_tool)
    tasks.target_review_repo_fast()

    assert calls == [
        "check-repo-health",
        "check-public-lock",
        "check-publisher-requirements",
    ]
    output = capsys.readouterr()
    assert "[pass] repo-health: check passed" in output.out
    assert "[pass] public-lock: check passed" in output.out
    assert "[pass] publisher-requirements: check passed" in output.out
    assert (
        "[unverified] uv-version: required tool preflight failed: "
        "uv --version exited with code 1 during preflight"
    ) in output.err
    assert "Repository review completed with unverified checks" in output.out


@pytest.mark.parametrize(
    ("failure", "expected_detail"),
    [
        (
            subprocess.TimeoutExpired(("git", "--version"), 1),
            "git --version timed out during preflight",
        ),
        (
            OSError("cannot execute"),
            "git could not be started during preflight: cannot execute",
        ),
    ],
)
def test_review_tool_probe_failure_marks_check_unverified(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_detail: str,
) -> None:
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(tasks, "probe_review_tool", real_probe_review_tool)

    def fail_probe(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(tasks, "run_isolated_review_command", fail_probe)

    runnable, results = tasks.classify_review_tools(
        (tasks.ReviewCheck("check", "check", ("git",)),)
    )

    assert runnable == []
    assert [(result.status, result.detail) for result in results] == [
        ("unverified", f"required tool preflight failed: {expected_detail}")
    ]


def test_compile_aw_is_unverified_when_extension_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")

    def unavailable(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env
        calls.append((tuple(args), timeout))
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(tasks, "run_isolated_review_command", unavailable)

    runnable, results = tasks.classify_review_tools(
        (tasks.ReviewCheck("compile-aw", "compile-aw", ("git", "gh")),)
    )

    assert runnable == []
    assert [(result.name, result.status) for result in results] == [
        ("compile-aw", "unverified")
    ]
    assert calls == [
        (
            tasks.REVIEW_GH_AW_LIST_COMMAND,
            tasks.REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS,
        )
    ]


def test_compile_aw_preflight_timeout_is_unverified_and_does_not_block_next_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    system_which = tasks.shutil.which

    def available_tool(command: str) -> str | None:
        if command in {"powershell.exe", "pwsh.exe"}:
            return system_which(command)
        return f"mock-{command}"

    monkeypatch.setattr(tasks.shutil, "which", available_tool)
    monkeypatch.setattr(tasks, "REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS", 0.5)
    child_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    original_resolve_command = tasks.resolve_command

    def resolve_preflight(args: list[str] | tuple[str, ...]) -> list[str]:
        if tuple(args) == tasks.REVIEW_GH_AW_LIST_COMMAND:
            return [sys.executable, "-c", child_code]
        return original_resolve_command(args)

    monkeypatch.setattr(tasks, "resolve_command", resolve_preflight)

    runnable, results = tasks.classify_review_tools(
        (
            tasks.ReviewCheck("compile-aw", "compile-aw", ("git", "gh")),
            tasks.ReviewCheck("next", "next", ("git",)),
        )
    )

    assert [check.name for check in runnable] == ["next"]
    assert [(result.name, result.status) for result in results] == [
        ("compile-aw", "unverified")
    ]
    tmp_path.rmdir()
    assert not tmp_path.exists()


def test_review_repo_full_runs_available_checks_after_fast_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        tasks,
        "run_fast_review_checks",
        lambda: [tasks.ReviewResult("fast", "fail", "failed")],
    )
    monkeypatch.setattr(
        tasks,
        "run_review_targets_isolated",
        lambda checks: calls.append(tuple(check.name for check in checks)) or [],
    )

    with pytest.raises(SystemExit) as error:
        tasks.target_review_repo_full()

    assert error.value.code == 1
    assert calls == [
        (
            "qa-app",
            "test-hooks",
            "build-bicep",
            "lint-k8s",
            "validate-helm-values",
            "lint-workflows",
            "compile-aw",
        )
    ]


def test_isolated_review_copies_current_tracked_and_untracked_files_for_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    scripts_directory = repository / "scripts"
    scripts_directory.mkdir(parents=True)
    (scripts_directory / "tasks.py").write_text("# task runner\n", encoding="utf-8")
    (repository / "README.md").write_text(
        "# Modified uncommitted content\n", encoding="utf-8"
    )
    new_skill = (
        repository / ".github" / "skills" / "repository-freshness-checker" / "SKILL.md"
    )
    new_test = repository / "scripts" / "tests" / "test_review_repo_contract.py"
    new_skill.parent.mkdir(parents=True)
    new_test.parent.mkdir(parents=True)
    new_skill.write_text("# New skill\n", encoding="utf-8")
    new_test.write_text("# New test\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    git_outputs = {
        ("git", "ls-files", "-z"): ["scripts/tasks.py", "README.md"],
        (
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ): [
            ".github/skills/repository-freshness-checker/SKILL.md",
            "scripts/tests/test_review_repo_contract.py",
        ],
    }
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: git_outputs[tuple(args)],
    )
    monkeypatch.setattr(
        tasks,
        "command_output",
        lambda args, **_kwargs: (
            "https://github.com/example/repository.git"
            if tuple(args) == ("git", "remote", "get-url", "origin")
            else pytest.fail(f"unexpected command: {args}")
        ),
    )
    calls: list[tuple[tuple[str, ...], Path, dict[str, str] | None]] = []
    isolated_root: Path | None = None

    def record_run(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del check, timeout
        calls.append((tuple(args), cwd, env))
        return subprocess.CompletedProcess(args, 0)

    def record_isolated(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal isolated_root
        target_name = args[-1]
        if target_name == "prepare-review-python-env":
            assert timeout == tasks.REVIEW_PYTHON_ENVIRONMENT_TIMEOUT_SECONDS
            assert tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE not in env
        else:
            assert timeout == tasks.REVIEW_CHECK_TIMEOUT_SECONDS
            assert env[tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE] == "1"
        isolated_root = cwd
        calls.append((tuple(args), cwd, env))
        assert (
            cwd / ".github" / "skills" / "repository-freshness-checker" / "SKILL.md"
        ).is_file()
        assert (cwd / "scripts" / "tests" / "test_review_repo_contract.py").is_file()
        assert (cwd / "README.md").read_text(encoding="utf-8") == (
            "# Modified uncommitted content\n"
        )
        assert not cwd.is_relative_to(repository.resolve())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run", record_run)
    monkeypatch.setattr(tasks, "run_isolated_review_command", record_isolated)

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("qa-app", "qa-app", ("git", "uv")),
            tasks.ReviewCheck("test-hooks", "test-hooks", ("git", "uv", "lefthook")),
        )
    )

    assert len(calls) == 7
    assert calls[0][0][:2] == ("git", "init")
    assert calls[1][0] == (
        "git",
        "remote",
        "add",
        "origin",
        "https://github.com/example/repository.git",
    )
    assert calls[2][0] == ("git", "add", "--force", "--all")
    assert "commit" in calls[3][0]
    assert calls[4][0][-1] == "prepare-review-python-env"
    assert calls[5][0][-1] == "qa-app"
    assert calls[6][0][-1] == "test-hooks"
    assert all(call[1] != repository for call in calls)
    assert calls[6][2] is not None
    assert "UV_PROJECT_ENVIRONMENT" in calls[6][2]
    assert Path(calls[6][2]["UV_PROJECT_ENVIRONMENT"]).is_relative_to(calls[6][1])
    assert [(result.name, result.status) for result in results] == [
        ("qa-app", "pass"),
        ("test-hooks", "pass"),
    ]
    assert isolated_root is not None
    assert not isolated_root.exists()
    assert (repository / "README.md").read_text(encoding="utf-8") == (
        "# Modified uncommitted content\n"
    )
    assert not (repository / "tmp").exists()


def test_isolated_review_rejects_os_temporary_directory_inside_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    real_temporary_directory = tasks.tempfile.TemporaryDirectory
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks.tempfile,
        "TemporaryDirectory",
        lambda *, prefix: real_temporary_directory(prefix=prefix, dir=repository),
    )
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda _destination: pytest.fail("snapshot copy must not run"),
    )
    monkeypatch.setattr(
        tasks,
        "run",
        lambda *_args, **_kwargs: pytest.fail("git setup must not run"),
    )
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda *_args, **_kwargs: pytest.fail("full checks must not run"),
    )

    results = tasks.run_review_targets_isolated(
        (tasks.ReviewCheck("test-hooks", "test-hooks", ("git",)),)
    )

    assert [(result.name, result.status) for result in results] == [
        ("snapshot", "unverified"),
        ("test-hooks", "unverified"),
    ]
    assert results[0].detail == (
        "operating-system temporary directory is inside the repository"
    )
    assert not any(repository.iterdir())


def test_isolated_review_times_out_one_check_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    scripts_directory = repository / "scripts"
    scripts_directory.mkdir(parents=True)
    (scripts_directory / "tasks.py").write_text("# task runner\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["scripts/tasks.py"] if tuple(args) == ("git", "ls-files", "-z") else []
        ),
    )
    child_calls: list[str] = []

    def record_run(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env, check, timeout
        return subprocess.CompletedProcess(args, 0)

    def record_isolated(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env
        child_calls.append(args[-1])
        assert timeout == tasks.REVIEW_CHECK_TIMEOUT_SECONDS
        if args[-1] == "slow":
            raise subprocess.TimeoutExpired(args, tasks.REVIEW_CHECK_TIMEOUT_SECONDS)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run", record_run)
    monkeypatch.setattr(tasks, "run_isolated_review_command", record_isolated)

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("slow", "slow", ("git",)),
            tasks.ReviewCheck("next", "next", ("git",)),
        )
    )

    assert child_calls == ["slow", "next"]
    assert [(result.name, result.status) for result in results] == [
        ("slow", "unverified"),
        ("next", "pass"),
    ]
    assert not (repository / "tmp").exists()


def test_python_environment_preparation_failure_preserves_separate_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "scripts").mkdir(parents=True)
    (repository / "scripts" / "tasks.py").write_text(
        "# task runner\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "classify_review_tools",
        lambda checks: (list(checks), []),
    )
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda destination: (
            (destination / "scripts").mkdir(parents=True),
            (destination / "scripts" / "tasks.py").write_text(
                "# task runner\n",
                encoding="utf-8",
            ),
            [],
        )[-1],
    )
    monkeypatch.setattr(tasks, "command_output", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0),
    )
    child_calls: list[str] = []

    def fail_preparation(
        args: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        child_calls.append(args[-1])
        return subprocess.CompletedProcess(
            args,
            1,
            stdout=b"",
            stderr=b"Failed to fetch package index: connection timed out",
        )

    monkeypatch.setattr(tasks, "run_isolated_review_command", fail_preparation)

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("qa-app", "qa-app", ("git", "uv")),
            tasks.ReviewCheck("test-hooks", "test-hooks", ("git", "uv", "lefthook")),
        )
    )

    assert child_calls == ["prepare-review-python-env"]
    assert [(result.name, result.status) for result in results] == [
        ("qa-app", "unverified"),
        ("test-hooks", "unverified"),
    ]
    assert all(
        result.detail.startswith("Python environment preparation failed:")
        for result in results
    )


def test_compile_aw_is_unverified_without_origin_and_other_checks_continue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "scripts").mkdir(parents=True)
    (repository / "scripts" / "tasks.py").write_text(
        "# task runner\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "classify_review_tools",
        lambda checks: (list(checks), []),
    )
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda destination: (
            (destination / "scripts").mkdir(parents=True),
            (destination / "scripts" / "tasks.py").write_text(
                "# task runner\n",
                encoding="utf-8",
            ),
            [],
        )[-1],
    )
    monkeypatch.setattr(tasks, "command_output", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0),
    )
    child_calls: list[str] = []
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda args, **_kwargs: (
            child_calls.append(args[-1]) or subprocess.CompletedProcess(args, 0)
        ),
    )

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("compile-aw", "compile-aw", ("git", "gh")),
            tasks.ReviewCheck("next", "next", ("git",)),
        )
    )

    assert child_calls == ["next"]
    assert [(result.name, result.status) for result in results] == [
        ("compile-aw", "unverified"),
        ("next", "pass"),
    ]


def test_timed_out_process_tree_releases_isolation(tmp_path: Path) -> None:
    isolation = tmp_path / "isolation"
    isolation.mkdir()
    child_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        tasks.run_isolated_review_command(
            [sys.executable, "-c", child_code],
            cwd=isolation,
            env={},
            timeout=0.5,
        )

    isolation.rmdir()
    assert not isolation.exists()


def test_isolated_command_streams_output_and_keeps_only_bounded_tail(
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    output = b"prefix-" + (b"x" * (tasks.REVIEW_LOG_TAIL_BYTES + 1024))
    code = (
        "import sys; sys.stdout.buffer.write("
        f"b'prefix-' + b'x' * {tasks.REVIEW_LOG_TAIL_BYTES + 1024})"
    )

    completed = tasks.run_isolated_review_command(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={},
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == output[-tasks.REVIEW_LOG_TAIL_BYTES :]
    assert len(completed.stdout) == tasks.REVIEW_LOG_TAIL_BYTES
    assert capsysbinary.readouterr().out == output


@pytest.mark.parametrize(
    ("log", "reason"),
    [
        ("Cannot connect to the Docker daemon. Is it running?", "Docker daemon"),
        (
            "Error pulling image: failed to pull image ghcr.io/example/test",
            "image pull",
        ),
        ("dial tcp: lookup ghcr.io: no such host", "DNS resolution"),
        ("SSL certificate problem: unable to get local issuer certificate", "TLS"),
        ("Failed to fetch package index: connection timed out", "package index"),
        ("Connection refused while contacting registry.example", "network connection"),
        ("Unable to download Bicep CLI", "Azure CLI"),
    ],
)
def test_explicit_environment_failures_are_unverified(
    log: str,
    reason: str,
) -> None:
    completed = subprocess.CompletedProcess(
        ["check"],
        1,
        stdout=b"",
        stderr=log.encode(),
    )

    status, detail = tasks.classify_review_failure(
        tasks.ReviewCheck("check", "check", ()),
        completed,
    )

    assert status == "unverified"
    assert reason in detail


def test_baseline_four_pytest_assertion_failures_remain_fail() -> None:
    completed = subprocess.CompletedProcess(
        ["check"],
        1,
        stdout=b"",
        stderr=(
            b"FAILED scripts/tests/test_lefthook.py::test_first - AssertionError\n"
            b"FAILED scripts/tests/test_lefthook.py::test_second - AssertionError\n"
            b"FAILED scripts/tests/test_lefthook.py::test_third - AssertionError\n"
            b"FAILED scripts/tests/test_uv_workflow.py::test_fourth - AssertionError\n"
            b"E       assert 4 == 0\n"
            b"4 failed, 106 passed\n"
        ),
    )

    status, detail = tasks.classify_review_failure(
        tasks.ReviewCheck("test-hooks", "test-hooks", ()),
        completed,
    )

    assert status == "fail"
    assert "repository-related" in detail


def test_compile_aw_fails_when_managed_files_differ_from_head(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tasks, "target_check_gh_aw", lambda: None)
    monkeypatch.setattr(
        tasks,
        "run_gh_aw_compile",
        lambda: calls.append("compile"),
    )
    monkeypatch.setattr(
        tasks,
        "command_output",
        lambda args, **_kwargs: " M .github/workflows/example.lock.yml",
    )

    with pytest.raises(SystemExit) as error:
        tasks.target_compile_aw()

    assert error.value.code == 1
    assert calls == ["compile"]
    output = capsys.readouterr()
    assert "differ from the reviewed repository state" in output.err


def test_compile_aw_checks_all_compiler_managed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(tasks, "target_check_gh_aw", lambda: None)
    monkeypatch.setattr(
        tasks,
        "run_gh_aw_compile",
        lambda: None,
    )

    def clean_status(args: list[str] | tuple[str, ...], **_kwargs: object) -> str:
        status_calls.append(tuple(args))
        return ""

    monkeypatch.setattr(tasks, "command_output", clean_status)

    tasks.target_compile_aw()

    assert status_calls == [
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *tasks.GH_AW_MANAGED_PATHS,
        )
    ]


def test_review_index_parser_preserves_nul_delimited_paths_and_stages() -> None:
    oid = b"a" * 40
    output = (
        b"100644 " + oid + b" 1\tconflict\nname.txt\0"
        b"100755 " + oid + b" 2\tconflict\nname.txt\0"
    )

    assert tasks.parse_review_index_entries(output) == [
        ("conflict\nname.txt", "100644", 1, "a" * 40),
        ("conflict\nname.txt", "100755", 2, "a" * 40),
    ]


def test_review_fingerprint_records_deleted_and_untracked_files(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    tracked = repository / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    git = tasks.resolve_command(["git"])[0]
    subprocess.run([git, "init", "--quiet"], cwd=repository, check=True)
    subprocess.run([git, "add", "tracked.txt"], cwd=repository, check=True)
    tracked.unlink()
    untracked = repository / "untracked name.txt"
    untracked.write_text("untracked\n", encoding="utf-8")

    fingerprint = tasks.capture_review_fingerprint(repository)

    assert "timestamp" not in fingerprint
    assert fingerprint["tracked_index"][0]["path"] == "tracked.txt"
    assert fingerprint["tracked_index"][0]["worktree_kind"] == "deleted"
    assert fingerprint["tracked_index"][0]["content_sha256"] is None
    assert fingerprint["untracked"][0]["path"] == "untracked name.txt"
    assert (
        fingerprint["untracked"][0]["content_sha256"]
        == hashlib.sha256(untracked.read_bytes()).hexdigest()
    )


def test_review_fingerprint_hashes_symlink_target_without_following(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFLNK,
        st_size=10,
        st_mtime_ns=1,
        st_ino=2,
    )

    class FakeSymlink:
        def lstat(self) -> SimpleNamespace:
            return metadata

    symlink = FakeSymlink()
    monkeypatch.setattr(
        tasks,
        "fingerprint_worktree_path",
        lambda _root, _relative: symlink,
    )
    monkeypatch.setattr(tasks.os, "readlink", lambda _path: "target.txt")

    kind, digest = tasks.fingerprint_worktree_content(tmp_path, "link.txt")

    assert kind == "symlink"
    assert digest == hashlib.sha256(os.fsencode("target.txt")).hexdigest()


def test_review_fingerprint_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(tasks.ReviewSnapshotError):
        tasks.fingerprint_worktree_path(tmp_path, "../outside.txt")


def test_review_fingerprint_compare_reports_only_paths_and_hashes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_entry = {
        "path": "secret.txt",
        "worktree_kind": "file",
        "content_sha256": "a" * 64,
    }
    before_entry["entry_sha256"] = tasks.fingerprint_sha256(before_entry)
    after_entry = {
        "path": "secret.txt",
        "worktree_kind": "file",
        "content_sha256": "b" * 64,
    }
    after_entry["entry_sha256"] = tasks.fingerprint_sha256(after_entry)
    before_content = {"tracked_index": [before_entry], "untracked": []}
    after_content = {"tracked_index": [after_entry], "untracked": []}
    before_path.write_text(
        json.dumps(
            {
                "schema_version": tasks.REVIEW_FINGERPRINT_SCHEMA_VERSION,
                "repository_root": "C:\\repo",
                **before_content,
                "fingerprint_sha256": tasks.fingerprint_sha256(before_content),
            }
        ),
        encoding="utf-8",
    )
    after_path.write_text(
        json.dumps(
            {
                "schema_version": tasks.REVIEW_FINGERPRINT_SCHEMA_VERSION,
                "repository_root": "C:\\repo",
                **after_content,
                "fingerprint_sha256": tasks.fingerprint_sha256(after_content),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        tasks.compare_review_fingerprints(before_path, after_path)

    assert error.value.code == 1
    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "fail"
    assert result["changes"][0]["path"] == "secret.txt"
    assert set(result["changes"][0]) == {
        "path",
        "before_sha256",
        "after_sha256",
    }


def test_review_fingerprint_rejects_tampered_entry(
    tmp_path: Path,
) -> None:
    fingerprint_path = tmp_path / "fingerprint.json"
    entry = {
        "path": "file.txt",
        "worktree_kind": "file",
        "content_sha256": "a" * 64,
    }
    entry["entry_sha256"] = tasks.fingerprint_sha256(entry)
    content = {"tracked_index": [], "untracked": [entry]}
    fingerprint = {
        "schema_version": tasks.REVIEW_FINGERPRINT_SCHEMA_VERSION,
        "repository_root": "C:\\repo",
        **content,
        "fingerprint_sha256": tasks.fingerprint_sha256(content),
    }
    entry["content_sha256"] = "b" * 64
    fingerprint_path.write_text(json.dumps(fingerprint), encoding="utf-8")

    with pytest.raises(tasks.ReviewSnapshotError, match="entry hash does not match"):
        tasks.load_review_fingerprint(fingerprint_path)


def test_review_fingerprint_cli_dispatches_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "fingerprint.json"
    calls: list[Path] = []
    monkeypatch.setattr(
        tasks,
        "target_review_fingerprint_capture",
        lambda output: calls.append(output),
    )

    assert (
        tasks.main(
            [
                "review-fingerprint",
                "capture",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    assert calls == [output_path]


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific gh-aw process boundary")
def test_windows_gh_aw_compile_uses_powershell_process_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        tasks.shutil,
        "which",
        lambda command: (
            "C:\\tools\\powershell.exe" if command == "powershell.exe" else None
        ),
    )
    monkeypatch.setattr(
        tasks,
        "resolve_command",
        lambda args: ["C:\\tools\\gh.exe"] if list(args) == ["gh"] else list(args),
    )

    def record_command(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        observed["args"] = tuple(args)
        observed["cwd"] = cwd
        observed["timeout"] = timeout
        observed["gh_path"] = env["GH_AW_GH_PATH"]
        observed["root"] = env["GH_AW_ROOT"]
        observed["stdin_empty"] = Path(env["GH_AW_STDIN"]).read_bytes() == b""
        observed["temporary_parent"] = Path(env["GH_AW_STDIN"]).parent
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tasks, "run_isolated_review_command", record_command)

    tasks.run_gh_aw_compile()

    command = observed["args"]
    assert isinstance(command, tuple)
    assert command[:3] == (
        "C:\\tools\\powershell.exe",
        "-NoProfile",
        "-NonInteractive",
    )
    script = command[-1]
    assert isinstance(script, str)
    assert "Start-Process" in script
    assert observed["cwd"] == tasks.ROOT
    assert observed["timeout"] == tasks.REVIEW_GH_AW_COMPILE_TIMEOUT_SECONDS
    assert observed["gh_path"] == "C:\\tools\\gh.exe"
    assert observed["root"] == str(tasks.ROOT)
    assert observed["stdin_empty"] is True
    temporary_parent = observed["temporary_parent"]
    assert isinstance(temporary_parent, Path)
    assert not temporary_parent.exists()


def test_command_nul_output_preserves_git_path_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = b" leading.txt\0trailing.txt \0line\nbreak.txt\0"

    def completed_run(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(["git"], 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(tasks.subprocess, "run", completed_run)

    assert tasks.command_nul_output(["git", "ls-files", "-z"]) == [
        " leading.txt",
        "trailing.txt ",
        "line\nbreak.txt",
    ]


def test_snapshot_rejects_path_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    destination = tmp_path / "snapshot"
    repository.mkdir()
    destination.mkdir()
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["../escape.txt"] if tuple(args) == ("git", "ls-files", "-z") else []
        ),
    )

    with pytest.raises(tasks.ReviewSnapshotError, match="unsafe repository path"):
        tasks.copy_worktree_snapshot(destination)

    assert not (tmp_path / "escape.txt").exists()


def test_snapshot_skips_oversized_untracked_file_as_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    destination = tmp_path / "snapshot"
    repository.mkdir()
    destination.mkdir()
    (repository / "large.bin").write_bytes(b"oversized")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "REVIEW_MAX_UNTRACKED_FILE_BYTES", 4)
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["large.bin"]
            if tuple(args)
            == ("git", "ls-files", "--others", "--exclude-standard", "-z")
            else []
        ),
    )

    issues = tasks.copy_worktree_snapshot(destination)

    assert not (destination / "large.bin").exists()
    assert issues == [
        "untracked file exceeds the 4-byte snapshot limit and was skipped: large.bin"
    ]


def test_snapshot_skips_symlink_as_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    destination = tmp_path / "snapshot"
    repository.mkdir()
    destination.mkdir()
    target = repository / "target.txt"
    link = repository / "link.txt"
    target.write_text("target\n", encoding="utf-8")
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is not available")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["link.txt"]
            if tuple(args)
            == ("git", "ls-files", "--others", "--exclude-standard", "-z")
            else []
        ),
    )

    issues = tasks.copy_worktree_snapshot(destination)

    assert not (destination / "link.txt").exists()
    assert issues == ["symlink cannot be safely isolated and was skipped: link.txt"]


@pytest.mark.parametrize(
    "snapshot_issue",
    [
        "symlink cannot be safely isolated and was skipped: link.txt",
        "untracked file exceeds the snapshot limit and was skipped: large.bin",
        "non-regular file cannot be safely isolated and was skipped: pipe",
    ],
)
def test_incomplete_snapshot_marks_all_full_checks_unverified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot_issue: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda destination: [snapshot_issue],
    )
    monkeypatch.setattr(
        tasks,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "git setup must not run for an incomplete snapshot"
        ),
    )
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda *_args, **_kwargs: pytest.fail(
            "full checks must not run for an incomplete snapshot"
        ),
    )

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("first", "first", ("git",)),
            tasks.ReviewCheck("second", "second", ("git",)),
        )
    )

    assert [(result.name, result.status) for result in results] == [
        ("snapshot", "unverified"),
        ("first", "unverified"),
        ("second", "unverified"),
    ]
    assert not (repository / "tmp").exists()


def frontmatter_and_body(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw_frontmatter, body = text.split("---", 2)
    frontmatter = {}
    for line in raw_frontmatter.strip().splitlines():
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body


def test_review_repo_agent_contract() -> None:
    frontmatter, body = frontmatter_and_body(
        REPO_ROOT / ".github" / "agents" / "review-repo.agent.md"
    )

    assert frontmatter["name"] == "review-repo"
    assert "標準はfast（taskのみ）" in frontmatter["description"]
    assert "review-repo-fast" in body
    assert "review-repo-full" in body
    assert "scripts/tasks.py" in body
    assert "task target" in body
    assert "## 実行インターフェース" in body
    assert "## 検査の包含関係" in body
    assert 'review-repo-fast --inventory-json "<temp>/inventory.json"' in body
    assert 'review-repo-full --inventory-json "<temp>/inventory.json"' in body
    assert "review-fingerprint capture --output" in body
    assert "review-fingerprint compare --before" in body
    assert "inventory-repo --format json" in body
    assert "`review-repo-fast`の全検査" in body
    assert "Bicep parameter JSON" in body
    assert "全Kubernetes YAML" in body
    assert "Chaos Mesh chart" in body
    assert "`kubernetes-schema-exclusion`座標で`excluded`" in body
    assert "## fullモードの文書とAI運用資産の評価基準" in body
    assert "文書とAI運用資産の意味評価および専門skillは実行しない" in body
    assert "文書とAI運用資産の意味評価を実行する" in body
    for document_type in (
        "`.github/copilot-instructions.md`",
        "| agent |",
        "| skill |",
        "| ADR |",
        "| Feature Document |",
        "| `docs/workarounds.md` |",
        "| READMEと運用文書 |",
        "| workflow sourceと生成物 |",
    ):
        assert document_type in body
    assert "inventoryへの出現だけで`pass`にしない" in body
    assert "`disable-model-invocation: true`のdispatcherはnameを必須とせず" in body
    execution_steps = body.split("## 実行手順", 1)[1].split(
        "## 既存経路との責務境界",
        1,
    )[0]
    assert "`inventory-repo`や`check-repo-health`を重複実行しない" in execution_steps
    assert "repo_health.py" not in execution_steps
    assert "fullの場合だけ" in execution_steps
    assert "fastではfull専用検査を個別の`unverified`として列挙せず" in execution_steps
    assert "`repository-freshness-checker`" in execution_steps
    assert "`bicep-api-version-updater`のcheck-onlyモード" in execution_steps
    assert "手順2と同じinventory JSON" in execution_steps
    assert "`documentation-external-link`座標を全件処理" in execution_steps
    assert "Bicep resource APIの結果が返らない場合" in execution_steps
    assert "その領域を`unverified`とする" in execution_steps
    assert "pass" in body
    assert "fail" in body
    assert "unverified" in body
    assert "excluded" in body
    assert "coverage" in body
    for category in (
        "covered_by_other_check",
        "intentionally_excluded",
        "true_gap",
    ):
        assert category in body
    assert "今回の走査範囲では" in body
    assert "追跡ファイルを編集しない" in body
    assert "git status --short" not in body
    assert "tracked/index" in body
    assert "未追跡ファイル一覧" in body
    assert "SHA-256" in body
    assert "内容を表示しない" in body
    assert "Azure subscription" in body
    assert "AKS cluster" in body
    assert "Fleet" in body
    assert "bicep-version-check.yml" in body
    assert "aks-updates-analyzer" in body
    assert "bicep-api-version-updater" in body
    assert "bicep-api-version-check.md" in body
    for product_command in ("az feature show", "az provider show", "gh api", "curl"):
        assert product_command not in body


def test_repository_freshness_skill_contract() -> None:
    skill_path = (
        REPO_ROOT / ".github" / "skills" / "repository-freshness-checker" / "SKILL.md"
    )
    frontmatter, body = frontmatter_and_body(skill_path)

    assert frontmatter["name"] == "repository-freshness-checker"
    assert "review-repo full" in frontmatter["description"]
    assert "check-only" in body
    assert "repo health inventory JSON" in body
    assert "review-repo-full --inventory-json <absolute-path>" in body
    assert "inventory-repo --format json" in body
    assert "別のinventory生成コマンドを実行しない" in body
    for subject in (
        "gh-aw",
        "Lefthook",
        "actionlint",
        "kubeconform",
        "azd",
        "Chaos Mesh Helm chart",
        "Docker base image",
        "Azure Functions extension bundle",
    ):
        assert subject in body
    for boundary in (
        "bicep-version-check.yml",
        "aks-updates-analyzer",
        "bicep-api-version-updater",
        "Dependabot",
    ):
        assert boundary in body
    for status in ("pass", "fail", "unverified", "excluded"):
        assert status in body
    assert "Microsoft Learn MCP" in body
    assert "mslearn" in body
    assert "`documentation-external-link`" in body
    assert "`404`と`410`は`fail`" in body
    assert "ファイルの編集、自動更新" in body
    assert "Azure subscription" in body


def test_bicep_api_version_skill_exposes_non_editing_check_only_mode() -> None:
    _, body = frontmatter_and_body(
        REPO_ROOT / ".github" / "skills" / "bicep-api-version-updater" / "SKILL.md"
    )
    check_only = body.split("## check-onlyモード", 1)[1].split("## updateモード", 1)[0]

    assert "Azure認証を行わない" in check_only
    assert "`bicep-api-version-check` workflow" in check_only
    assert "subscriptionを照会しない" in check_only
    assert "Microsoft Learnの公開APIリファレンス" in check_only
    assert "Azure/azure-rest-api-specs" in check_only
    assert "どちらからも公開情報を取得できない対象は`unverified`" in check_only
    assert "az provider show" not in check_only
    assert "`edit`" not in check_only
    assert "ファイルを更新" not in check_only
    assert "## updateモード" in body
    assert "ユーザーの明示承認" in body
    assert "az provider show" in body.split("## updateモード", 1)[1]


def test_each_skill_directory_contains_skill_document() -> None:
    skills_directory = REPO_ROOT / ".github" / "skills"
    missing = sorted(
        path.name
        for path in skills_directory.iterdir()
        if path.is_dir() and not (path / "SKILL.md").is_file()
    )

    assert missing == []
