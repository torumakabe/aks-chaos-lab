from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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


def test_review_repo_fast_reuses_offline_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    target_names = (
        "target_check_repo_health",
        "target_check_uv_version",
        "target_check_public_lock",
        "target_check_publisher_requirements",
    )
    for target_name in target_names:
        monkeypatch.setattr(
            tasks,
            target_name,
            lambda name=target_name: calls.append(name),
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
        "target_check_repo_health",
        lambda: calls.append("repo-health"),
    )
    monkeypatch.setattr(
        tasks,
        "target_check_public_lock",
        lambda: calls.append("public-lock"),
    )
    monkeypatch.setattr(
        tasks,
        "target_check_publisher_requirements",
        lambda: calls.append("publisher-requirements"),
    )

    tasks.target_review_repo_fast()

    assert calls == ["repo-health", "public-lock", "publisher-requirements"]
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
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks,
        "target_check_repo_health",
        lambda: (_ for _ in ()).throw(SystemExit(7)),
    )
    for target_name in (
        "target_check_uv_version",
        "target_check_public_lock",
        "target_check_publisher_requirements",
    ):
        monkeypatch.setattr(
            tasks,
            target_name,
            lambda name=target_name: calls.append(name),
        )

    with pytest.raises(SystemExit) as error:
        tasks.target_review_repo_fast()

    assert error.value.code == 1
    assert calls == [
        "target_check_uv_version",
        "target_check_public_lock",
        "target_check_publisher_requirements",
    ]
    assert "[fail] repo-health: check exited with code 7" in capsys.readouterr().err


def test_review_repo_fast_marks_exception_as_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks,
        "target_check_repo_health",
        lambda: (_ for _ in ()).throw(RuntimeError("broken check")),
    )
    for target_name in (
        "target_check_uv_version",
        "target_check_public_lock",
        "target_check_publisher_requirements",
    ):
        monkeypatch.setattr(
            tasks,
            target_name,
            lambda name=target_name: calls.append(name),
        )

    with pytest.raises(SystemExit) as error:
        tasks.target_review_repo_fast()

    assert error.value.code == 1
    assert calls == [
        "target_check_uv_version",
        "target_check_public_lock",
        "target_check_publisher_requirements",
    ]
    assert (
        "[fail] repo-health: check raised RuntimeError: broken check"
        in capsys.readouterr().err
    )


def test_review_repo_fast_propagates_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks,
        "target_check_repo_health",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        tasks,
        "target_check_uv_version",
        lambda: calls.append("uv-version"),
    )

    with pytest.raises(KeyboardInterrupt):
        tasks.target_review_repo_fast()

    assert calls == []


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
        "qa-app-and-hooks": ("git", "uv", "lefthook"),
        "build-bicep": ("git", "az"),
        "lint-k8s": ("git", "docker"),
        "lint-workflows": ("git", "docker"),
        "compile-aw": ("git", "gh"),
    }
    assert {check.name: check.timeout_seconds for check in tasks.FULL_REVIEW_CHECKS}[
        "qa-app-and-hooks"
    ] == tasks.REVIEW_PYTHON_CHECK_TIMEOUT_SECONDS


def test_repository_health_targets_use_current_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: calls.append(tuple(args)),
    )

    tasks.target_inventory_repo()
    tasks.target_check_repo_health()

    script = str(tasks.ROOT / "scripts" / "repo_health.py")
    assert calls == [
        (sys.executable, script, "inventory", "--format", "text"),
        (sys.executable, script, "check", "--format", "text"),
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
        return subprocess.CompletedProcess(args, 1 if args[0] == "uv" else 0)

    monkeypatch.setattr(tasks, "run_isolated_review_command", version_probe)
    monkeypatch.setattr(tasks, "probe_review_tool", real_probe_review_tool)
    for target_name in (
        "target_check_repo_health",
        "target_check_public_lock",
        "target_check_publisher_requirements",
    ):
        monkeypatch.setattr(
            tasks,
            target_name,
            lambda name=target_name: calls.append(name),
        )
    monkeypatch.setattr(
        tasks,
        "target_check_uv_version",
        lambda: pytest.fail("uv-version must be skipped"),
    )

    tasks.target_review_repo_fast()

    assert calls == [
        "target_check_repo_health",
        "target_check_public_lock",
        "target_check_publisher_requirements",
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
            "qa-app-and-hooks",
            "build-bicep",
            "lint-k8s",
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
    ) -> subprocess.CompletedProcess[bytes]:
        del check
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
        assert timeout == tasks.REVIEW_CHECK_TIMEOUT_SECONDS
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
        (tasks.ReviewCheck("test-hooks", "test-hooks", ("git", "uv", "lefthook")),)
    )

    assert len(calls) == 5
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
    assert calls[4][0][-1] == "test-hooks"
    assert all(call[1] != repository for call in calls)
    assert calls[4][2] is not None
    assert "UV_PROJECT_ENVIRONMENT" in calls[4][2]
    assert Path(calls[4][2]["UV_PROJECT_ENVIRONMENT"]).is_relative_to(calls[4][1])
    assert results[-1].status == "pass"
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
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env, check
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


@pytest.mark.parametrize("mutation", ["changed", "added", "removed", "unchanged"])
def test_compile_aw_detects_generated_artifact_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")

    def copy_snapshot(destination: Path) -> list[str]:
        (destination / "scripts").mkdir()
        (destination / "scripts" / "tasks.py").write_text(
            "# task runner\n",
            encoding="utf-8",
        )
        workflow_lock = destination / ".github" / "workflows" / "existing.lock.yml"
        workflow_lock.parent.mkdir(parents=True)
        workflow_lock.write_text("old\n", encoding="utf-8")
        return []

    def run_command(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del env, timeout
        if tuple(args) == tasks.REVIEW_GH_AW_LIST_COMMAND:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=b"gh aw\tgithub/gh-aw\tv0.79.6\n",
                stderr=b"",
            )
        existing = cwd / ".github" / "workflows" / "existing.lock.yml"
        if mutation == "changed":
            existing.write_text("new\n", encoding="utf-8")
        elif mutation == "added":
            actions_lock = cwd / ".github" / "aw" / "actions-lock.json"
            actions_lock.parent.mkdir(parents=True)
            actions_lock.write_text("{}\n", encoding="utf-8")
        elif mutation == "removed":
            existing.unlink()
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tasks, "copy_worktree_snapshot", copy_snapshot)
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0),
    )
    monkeypatch.setattr(tasks, "run_isolated_review_command", run_command)

    results = tasks.run_review_targets_isolated(
        (tasks.ReviewCheck("compile-aw", "compile-aw", ("git", "gh")),)
    )

    expected_status = "pass" if mutation == "unchanged" else "fail"
    assert results[-1].status == expected_status
    if mutation != "unchanged":
        assert mutation in results[-1].detail


def test_generated_gh_aw_artifacts_include_support_files(tmp_path: Path) -> None:
    generated_paths = (
        ".github/aw/actions-lock.json",
        ".github/dependabot.yml",
        ".github/workflows/agentics-maintenance.yml",
        ".github/workflows/example.lock.yml",
    )
    for relative_path in generated_paths:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative_path}\n", encoding="utf-8")

    hashes = tasks.generated_gh_aw_artifact_hashes(tmp_path)

    assert set(hashes) == set(generated_paths)


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
    assert "標準はfast" in frontmatter["description"]
    assert "review-repo-fast" in body
    assert "review-repo-full" in body
    assert "pass" in body
    assert "fail" in body
    assert "unverified" in body
    assert "excluded" in body
    assert "coverage" in body
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
    for subject in (
        "gh-aw",
        "Lefthook",
        "actionlint",
        "kubeconform",
        "azd",
        "Chaos Mesh Helm chart",
        "Docker base image",
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
    assert "ファイルの編集、自動更新" in body
    assert "Azure subscription" in body


def test_bicep_api_version_skill_exposes_non_editing_check_only_mode() -> None:
    _, body = frontmatter_and_body(
        REPO_ROOT / ".github" / "skills" / "bicep-api-version-updater" / "SKILL.md"
    )
    check_only = body.split("## check-onlyモード", 1)[1].split("## updateモード", 1)[0]

    assert "Azure認証を行わない" in check_only
    assert "subscriptionを照会しない" in check_only
    assert "公開情報を取得できない対象は`unverified`" in check_only
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
