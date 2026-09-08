from __future__ import annotations

import os
import shutil
import subprocess
import sys
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


def _without_public_lock_call(calls: list[str]) -> list[str]:
    lock_calls = [call for call in calls if "check-public-lock.py" in call]
    assert len(lock_calls) == 1
    normalized = lock_calls[0].replace("\\", "/")
    assert normalized.startswith(
        "run --no-project --no-config --offline --no-python-downloads "
    )
    assert normalized.endswith("/.lefthook/pre-commit/check-public-lock.py")
    return [call for call in calls if call not in lock_calls]


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
            "--no-auto-install",
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
    calls = _without_public_lock_call(calls)
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
    shutil.copytree(ROOT / ".lefthook", repository / ".lefthook")
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
        [lefthook, "run", "pre-commit", "--no-tty", "--no-auto-install"],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert unstaged_result.returncode == 0, unstaged_result.stderr
    assert (
        _without_public_lock_call(log_path.read_text(encoding="utf-8").splitlines())
        == []
    )
    log_path.unlink()

    subprocess.run(
        [git, "add", "infra/main.bicep"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    staged_result = subprocess.run(
        [lefthook, "run", "pre-commit", "--no-tty", "--no-auto-install"],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert staged_result.returncode == 0, staged_result.stderr
    _assert_build_bicep_call(
        _without_public_lock_call(log_path.read_text(encoding="utf-8").splitlines())
    )


PROJECT = '[tool.uv.workspace]\nmembers = ["src/api"]\n'
PUBLIC_LOCK = """version = 1
[[package]]
name = "example"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://files.pythonhosted.org/example.whl", hash = "sha256:abc" }]
[[package]]
name = "app"
version = "1.0"
source = { virtual = "src/api" }
"""
PRIVATE_LOCK = PUBLIC_LOCK.replace(
    "https://pypi.org/simple", "https://approved.example.test/simple"
)


def _git(repository: Path, *args: str) -> bytes:
    return subprocess.run(
        [_required_command("git"), *args],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


@pytest.fixture
def staged_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Isolate fixture commits from the developer's signing and hook configuration.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    monkeypatch.delenv("LEFTHOOK", raising=False)
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Hook test")
    _git(repository, "config", "user.email", "hook@example.test")
    (repository / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/public_lock.py", repository / "scripts/public_lock.py"
    )
    shutil.copyfile(ROOT / "lefthook.yml", repository / "lefthook.yml")
    shutil.copytree(ROOT / ".lefthook", repository / ".lefthook")
    (repository / "pyproject.toml").write_text(PROJECT, encoding="utf-8")
    (repository / "uv.lock").write_text(PUBLIC_LOCK, encoding="utf-8")
    (repository / "README.md").write_text("example\n", encoding="utf-8")
    _git(repository, "add", ".")
    return repository


def _run_staged_check(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _required_command("uv"),
            "run",
            "--no-project",
            "--no-config",
            "--offline",
            "--no-python-downloads",
            str(repository / "scripts/public_lock.py"),
            "staged",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        encoding="utf-8",
    )


@pytest.mark.parametrize("staged_public", [False, True])
def test_public_lock_checks_index_not_worktree(
    staged_repository: Path, staged_public: bool
) -> None:
    repository = staged_repository
    lock = repository / "uv.lock"
    lock.write_text(PUBLIC_LOCK if staged_public else PRIVATE_LOCK, encoding="utf-8")
    _git(repository, "add", "uv.lock")
    lock.write_text(PRIVATE_LOCK if staged_public else PUBLIC_LOCK, encoding="utf-8")
    before_index = (repository / ".git/index").read_bytes()
    before_lock = lock.read_bytes()

    result = _run_staged_check(repository)

    assert (result.returncode == 0) == staged_public, result.stderr
    assert "approved.example.test" not in result.stdout + result.stderr
    assert (repository / ".git/index").read_bytes() == before_index
    assert lock.read_bytes() == before_lock
    assert not (repository / ".venv").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:example-secret@internal.example\uff0f/path.whl",
        "https://user:example-secret@[internal.example/path.whl",
    ],
)
def test_public_lock_url_parse_failure_does_not_disclose_input(
    staged_repository: Path, url: str
) -> None:
    repository = staged_repository
    lock = repository / "uv.lock"
    lock.write_text(
        PUBLIC_LOCK.replace("https://files.pythonhosted.org/example.whl", url),
        encoding="utf-8",
    )
    _git(repository, "add", "uv.lock")
    before_index = (repository / ".git/index").read_bytes()
    before_lock = lock.read_bytes()

    result = _run_staged_check(repository)

    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "Staged public lock validation failed" in output
    assert all(
        text not in output
        for text in ("example-secret", "internal.example", "Traceback")
    )
    assert (repository / ".git/index").read_bytes() == before_index
    assert lock.read_bytes() == before_lock


@pytest.mark.parametrize("staged_valid", [False, True])
def test_public_lock_reads_workspace_members_from_index(
    staged_repository: Path, staged_valid: bool
) -> None:
    manifest = staged_repository / "pyproject.toml"
    invalid = PROJECT.replace("src/api", "src/other")
    manifest.write_text(PROJECT if staged_valid else invalid, encoding="utf-8")
    _git(staged_repository, "add", "pyproject.toml")
    manifest.write_text(invalid if staged_valid else PROJECT, encoding="utf-8")
    result = _run_staged_check(staged_repository)
    assert (result.returncode == 0) == staged_valid, result.stderr


@pytest.mark.parametrize("name", ["uv.lock", "pyproject.toml"])
@pytest.mark.parametrize(
    "state", ["deleted", "renamed", "unmerged", "invalid-toml", "invalid-utf8"]
)
def test_public_lock_rejects_invalid_index_entries(
    staged_repository: Path, name: str, state: str
) -> None:
    repository = staged_repository
    if state == "deleted":
        _git(repository, "rm", "--cached", name)
    elif state == "renamed":
        _git(repository, "mv", name, f"{name}.old")
    elif state == "unmerged":
        oid = _git(repository, "rev-parse", f":0:{name}").decode().strip()
        entry = f"0 {'0' * len(oid)}\t{name}\n100644 {oid} 2\t{name}\n"
        subprocess.run(
            [_required_command("git"), "update-index", "--index-info"],
            input=entry.encode(),
            cwd=repository,
            check=True,
            capture_output=True,
        )
    elif state == "invalid-utf8":
        (repository / name).write_bytes(b"\xff")
        _git(repository, "add", name)
    else:
        (repository / name).write_text(
            'bad = "https://private.example', encoding="utf-8"
        )
        _git(repository, "add", name)
    before = (repository / ".git/index").read_bytes()
    result = _run_staged_check(repository)
    assert result.returncode == 1
    assert "Staged public lock validation failed" in result.stderr
    assert "private.example" not in result.stderr
    assert (repository / ".git/index").read_bytes() == before


def test_public_lock_honors_alternate_index(
    staged_repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = staged_repository
    alternate = tmp_path / "alternate-index"
    shutil.copyfile(repository / ".git/index", alternate)
    (repository / "uv.lock").write_text(PRIVATE_LOCK, encoding="utf-8")
    _git(repository, "add", "uv.lock")
    assert _run_staged_check(repository).returncode == 1
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate))
    assert _run_staged_check(repository).returncode == 0


@pytest.mark.parametrize("mode", ["staged", "all", "path"])
def test_public_lock_in_real_commits(staged_repository: Path, mode: str) -> None:
    repository = staged_repository
    subprocess.run(
        [_required_command("lefthook"), "install"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    # The first commit has no HEAD to use as a validation baseline.
    _git(repository, "commit", "--quiet", "-m", "Initial fixture")
    (repository / "uv.lock").write_text(PRIVATE_LOCK, encoding="utf-8")
    if mode == "staged":
        _git(repository, "add", "uv.lock")
        extra = []
    elif mode == "all":
        extra = ["-a"]
    else:
        # Git builds a temporary index for a path-limited commit.
        extra = ["--", "uv.lock"]
    result = subprocess.run(
        [_required_command("git"), "commit", "-m", "Reject private lock", *extra],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Staged public lock validation failed" in result.stdout + result.stderr
    assert "approved.example.test" not in result.stdout + result.stderr
    assert _git(repository, "rev-list", "--count", "HEAD").strip() == b"1"


def test_unrelated_commit_allows_unstaged_private_lock(staged_repository: Path) -> None:
    repository = staged_repository
    subprocess.run(
        [_required_command("lefthook"), "install"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    _git(repository, "commit", "--quiet", "-m", "Initial fixture")
    (repository / "uv.lock").write_text(PRIVATE_LOCK, encoding="utf-8")
    (repository / "README.md").write_text("updated\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--quiet", "-m", "Unrelated fixture change")
    assert _git(repository, "show", "HEAD:uv.lock").decode() == PUBLIC_LOCK
    assert (repository / "uv.lock").read_text(encoding="utf-8") == PRIVATE_LOCK


def test_empty_commit_checks_existing_private_lock(staged_repository: Path) -> None:
    repository = staged_repository
    (repository / "uv.lock").write_text(PRIVATE_LOCK, encoding="utf-8")
    _git(repository, "add", "uv.lock")
    # Simulate a bad lock committed before the hook was installed.
    _git(repository, "commit", "--quiet", "-m", "Legacy fixture")
    subprocess.run(
        [_required_command("lefthook"), "install"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [_required_command("git"), "commit", "--allow-empty", "-m", "Empty fixture"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Staged public lock validation failed" in result.stdout + result.stderr
