from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tasks  # noqa: E402

HEAD = "3ddf814742c261872846af58b825fa75ae579459"
PROJECT = b'[tool.uv.workspace]\nmembers = ["src/api"]\n'
MEMBER = b'[project]\nname = "example"\nversion = "1.0"\n'
CURRENT = b"""version = 1

[options]
exclude-newer = "2020-01-01T00:00:00Z"

[[package]]
name = "example"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/example.tar.gz", hash = "sha256:abc" }
"""
PUBLIC_CANDIDATE = CURRENT.replace(b'version = "1.0"', b'version = "2.0"')
UNCOOLED_CANDIDATE = PUBLIC_CANDIDATE.replace(
    b'\n[options]\nexclude-newer = "2020-01-01T00:00:00Z"\n', b""
)
PRIVATE_CANDIDATE = CURRENT.replace(
    b"https://pypi.org/simple", b"https://approved.example/simple"
)


@pytest.fixture
def root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_bytes(PROJECT)
    (tmp_path / "src/api").mkdir(parents=True)
    (tmp_path / "src/api/pyproject.toml").write_bytes(MEMBER)
    (tmp_path / "uv.lock").write_bytes(CURRENT)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(tasks, "require_command", lambda command: None)

    def fail_on_uv(*args: object, **kwargs: object) -> None:
        pytest.fail("adopt-public-lock must not run uv")

    monkeypatch.setattr(tasks, "run", fail_on_uv)
    monkeypatch.setattr(tasks, "run_uv", fail_on_uv)
    return tmp_path


def install_git(
    monkeypatch: pytest.MonkeyPatch, dirty: frozenset[str] = frozenset()
) -> None:
    def git(*args: str) -> bytes:
        if args == ("rev-parse", "--verify", "HEAD"):
            return f"{HEAD}\n".encode()
        if args == ("show", f"{HEAD}:pyproject.toml"):
            return PROJECT
        if args[0] == "ls-files":
            return b"unmerged\n" if "conflict" in dirty else b""
        if args[0] == "diff" and "--cached" in args:
            return b"uv.lock\n" if "staged" in dirty else b""
        if args[0] == "diff" and "--stat" in args:
            return b" uv.lock | 2 +-\n"
        if args[0] == "diff":
            return b"uv.lock\n" if "worktree" in dirty else b""
        pytest.fail(f"Unexpected Git call: {args}")

    monkeypatch.setattr(tasks, "adopt_lock_git", git)


def install_gh(
    monkeypatch: pytest.MonkeyPatch,
    *,
    runs: list[dict[str, object]] | None = None,
    download: Callable[[Path], None] | None = None,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def gh(*args: str) -> bytes:
        calls.append(args)
        if args[1] == "list":
            return json.dumps([] if runs is None else runs).encode()
        if download is None:
            raise tasks.AdoptPublicLockError("artifact not found")
        download(Path(args[args.index("--dir") + 1]))
        return b""

    monkeypatch.setattr(tasks, "adopt_lock_gh", gh)
    return calls


def write_candidate(content: bytes) -> Callable[[Path], None]:
    def download(directory: Path) -> None:
        (directory / "uv.lock").write_bytes(content)

    return download


def test_adopts_the_artifact_built_for_the_current_commit(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_git(monkeypatch)
    calls = install_gh(
        monkeypatch,
        runs=[{"databaseId": 35671072663}],
        download=write_candidate(PUBLIC_CANDIDATE),
    )

    tasks.target_adopt_public_lock()

    assert (root / "uv.lock").read_bytes() == PUBLIC_CANDIDATE
    assert list((root / "tmp").iterdir()) == []
    assert HEAD in calls[0]
    assert calls[1][:3] == ("run", "download", "35671072663")


@pytest.mark.parametrize("state", ["conflict", "staged", "worktree"])
def test_unclean_lock_or_manifest_stops_before_download(
    root: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    install_git(monkeypatch, frozenset({state}))
    calls = install_gh(monkeypatch, runs=[{"databaseId": 1}])

    with pytest.raises(SystemExit):
        tasks.target_adopt_public_lock()

    assert calls == []
    assert (root / "uv.lock").read_bytes() == CURRENT


def test_missing_run_keeps_the_existing_lock(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_git(monkeypatch)
    install_gh(monkeypatch)

    with pytest.raises(SystemExit):
        tasks.target_adopt_public_lock()

    assert (root / "uv.lock").read_bytes() == CURRENT


def test_expired_or_failed_download_keeps_the_existing_lock(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_git(monkeypatch)
    install_gh(monkeypatch, runs=[{"databaseId": 1}])

    with pytest.raises(SystemExit):
        tasks.target_adopt_public_lock()

    assert (root / "uv.lock").read_bytes() == CURRENT
    assert list((root / "tmp").iterdir()) == []


def test_non_public_candidate_is_never_written(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_git(monkeypatch)
    install_gh(
        monkeypatch,
        runs=[{"databaseId": 1}],
        download=write_candidate(PRIVATE_CANDIDATE),
    )

    with pytest.raises(SystemExit):
        tasks.target_adopt_public_lock()

    assert (root / "uv.lock").read_bytes() == CURRENT
    assert list((root / "tmp").iterdir()) == []


def test_candidate_without_a_release_cooldown_is_never_written(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_git(monkeypatch)
    install_gh(
        monkeypatch,
        runs=[{"databaseId": 1}],
        download=write_candidate(UNCOOLED_CANDIDATE),
    )

    with pytest.raises(SystemExit):
        tasks.target_adopt_public_lock()

    assert (root / "uv.lock").read_bytes() == CURRENT
    assert list((root / "tmp").iterdir()) == []


def test_lock_changed_during_download_is_not_overwritten(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_git(monkeypatch)

    def download(directory: Path) -> None:
        (directory / "uv.lock").write_bytes(PUBLIC_CANDIDATE)
        (root / "uv.lock").write_bytes(PRIVATE_CANDIDATE)

    install_gh(monkeypatch, runs=[{"databaseId": 1}], download=download)

    with pytest.raises(SystemExit):
        tasks.target_adopt_public_lock()

    assert (root / "uv.lock").read_bytes() == PRIVATE_CANDIDATE
    assert list((root / "tmp").iterdir()) == []
