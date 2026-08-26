from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _required_command(name: str) -> str:
    command = shutil.which(name)
    if command is None:
        pytest.fail(f"{name} is required to run repository hook tests", pytrace=False)
    return command


def _fake_uv(tmp_path: Path) -> None:
    shell_executable = tmp_path / "uv"
    shell_executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOOK_TEST_LOG"\n',
        encoding="utf-8",
    )
    shell_executable.chmod(0o755)

    if os.name == "nt":
        cmd_executable = tmp_path / "uv.cmd"
        cmd_executable.write_text(
            '@echo off\r\necho %*>>"%HOOK_TEST_LOG%"\r\n',
            encoding="utf-8",
        )


def _assert_build_bicep_call(calls: list[str]) -> None:
    assert len(calls) == 1
    normalized = calls[0].replace("\\", "/")
    assert normalized.startswith("run --no-project ")
    assert normalized.endswith("/scripts/tasks.py build-bicep")


@pytest.mark.parametrize(
    ("file_path", "should_run"),
    [
        ("infra/main.bicep", True),
        ("infra/modules/redis.bicep", True),
        ("README.md", False),
    ],
)
def test_bicep_pre_commit_selection(
    tmp_path: Path,
    file_path: str,
    *,
    should_run: bool,
) -> None:
    lefthook = _required_command("lefthook")

    _fake_uv(tmp_path)
    log_path = tmp_path / "uv.log"
    env = os.environ.copy()
    env["HOOK_TEST_LOG"] = str(log_path)
    env["PATH"] = os.pathsep.join((str(tmp_path), env["PATH"]))

    completed = subprocess.run(
        [
            lefthook,
            "run",
            "pre-commit",
            "--file",
            file_path,
            "--no-tty",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode == 0, completed.stderr
    calls = (
        log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    )
    if should_run:
        _assert_build_bicep_call(calls)
    else:
        assert calls == []


def test_bicep_pre_commit_uses_staged_files(tmp_path: Path) -> None:
    git = _required_command("git")
    lefthook = _required_command("lefthook")
    repository = tmp_path / "repository"
    fake_bin = tmp_path / "bin"
    repository.mkdir()
    fake_bin.mkdir()

    subprocess.run(
        [git, "init", "--quiet"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    shutil.copyfile(ROOT / "lefthook.yml", repository / "lefthook.yml")
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    bicep_path = repository / "infra" / "main.bicep"
    bicep_path.parent.mkdir()
    bicep_path.write_text("targetScope = 'subscription'\n", encoding="utf-8")

    _fake_uv(fake_bin)
    log_path = tmp_path / "uv.log"
    env = os.environ.copy()
    env["HOOK_TEST_LOG"] = str(log_path)
    env["PATH"] = os.pathsep.join((str(fake_bin), env["PATH"]))

    subprocess.run(
        [git, "add", "README.md"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    unstaged_result = subprocess.run(
        [lefthook, "run", "pre-commit", "--no-tty"],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert unstaged_result.returncode == 0, unstaged_result.stderr
    assert not log_path.exists()

    subprocess.run(
        [git, "add", "infra/main.bicep"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    staged_result = subprocess.run(
        [lefthook, "run", "pre-commit", "--no-tty"],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert staged_result.returncode == 0, staged_result.stderr
    _assert_build_bicep_call(log_path.read_text(encoding="utf-8").splitlines())
