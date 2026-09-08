from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import public_lock  # noqa: E402
import tasks  # noqa: E402

PROJECT = b'[tool.uv.workspace]\nmembers = ["src/api"]\n'
MEMBER = b'[project]\nname = "example"\nversion = "1.0"\n'
BASELINE = b"""version = 1
[[package]]
name = "example"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
dependencies = [{ name = "dependency", marker = "python_version >= '3.14'" }]
sdist = { url = "https://files.pythonhosted.org/example.tar.gz", hash = "sha256:abc", size = 10, upload-time = "2026-01-01" }
wheels = [
  { url = "https://files.pythonhosted.org/example.whl", hash = "sha256:def", size = 20, upload-time = "2026-01-02" },
]
"""
DRIFT = (
    BASELINE.replace(b"https://pypi.org/simple", b"https://approved.example/simple")
    .replace(b"https://files.pythonhosted.org/", b"https://approved.example/artifacts/")
    .replace(b', size = 10, upload-time = "2026-01-01"', b"")
    .replace(b', size = 20, upload-time = "2026-01-02"', b"")
)


@pytest.fixture
def root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_bytes(PROJECT)
    (tmp_path / "src/api").mkdir(parents=True)
    (tmp_path / "src/api/pyproject.toml").write_bytes(MEMBER)
    (tmp_path / "uv.lock").write_bytes(DRIFT)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)

    def git(*args: str) -> bytes:
        if args == ("rev-parse", "--verify", "HEAD"):
            return b"revision\n"
        if args == ("show", "revision:uv.lock"):
            return BASELINE
        if args == ("show", "revision:pyproject.toml"):
            return PROJECT
        if args == ("show", "revision:src/api/pyproject.toml"):
            return MEMBER
        if args[0] == "hash-object":
            return (tmp_path / args[-1]).read_bytes()
        if args[0] == "rev-parse" and args[1].startswith("revision:"):
            return PROJECT if args[1].endswith(":pyproject.toml") else MEMBER
        if args[0] in {"diff", "ls-files"}:
            return b""
        pytest.fail(f"Unexpected Git call: {args}")

    monkeypatch.setattr(tasks, "public_lock_git", git)
    return tmp_path


def test_repair_restores_exact_public_bytes(root: Path) -> None:
    tasks.ensure_public_lock(allow_repair=True)
    assert (root / "uv.lock").read_bytes() == BASELINE
    assert list((root / "tmp").iterdir()) == []


def test_default_validation_preserves_drift(root: Path) -> None:
    with pytest.raises(SystemExit):
        tasks.ensure_public_lock()
    assert (root / "uv.lock").read_bytes() == DRIFT


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'name = "example"', b'name = "other"'),
        (b'version = "1.0"', b'version = "2.0"'),
        (b"sha256:abc", b"sha256:changed"),
        (b"python_version >= '3.14'", b"python_version >= '3.15'"),
        (b'version = 1\n', b'version = 1.0\n'),
        (b'name = "dependency"', b'name = "dependency", size = 9'),
        (b'hash = "sha256:def"', b'hash = "sha256:def", extra = true'),
        (b'registry = "https://approved.example/simple"', b'registry = 7'),
    ],
)
def test_repair_rejects_other_changes(root: Path, old: bytes, new: bytes) -> None:
    changed = DRIFT.replace(old, new)
    assert changed != DRIFT
    (root / "uv.lock").write_bytes(changed)
    with pytest.raises(SystemExit):
        tasks.ensure_public_lock(allow_repair=True)
    assert (root / "uv.lock").read_bytes() == changed


@pytest.mark.parametrize(
    "change",
    [
        (b"size = 10", b"size = 11"),
        (b'upload-time = "2026-01-01"', b'upload-time = "2026-02-01"'),
        (b"size = 20", b"size = 20.0"),
    ],
)
def test_metadata_values_cannot_change(root: Path, change: tuple[bytes, bytes]) -> None:
    changed = BASELINE.replace(b"pypi.org/simple", b"approved.example/simple")
    with pytest.raises(public_lock.PublicLockError):
        public_lock.public_lock_repair_content(
            root / "pyproject.toml", BASELINE, changed.replace(*change)
        )


@pytest.mark.parametrize("field", ["size = 5", 'upload-time = "added"'])
def test_metadata_cannot_be_added(root: Path, field: str) -> None:
    source = BASELINE.replace(b", size = 10", b"").replace(
        b', upload-time = "2026-01-01"', b""
    )
    changed = DRIFT.replace(b'hash = "sha256:abc"', f'hash = "sha256:abc", {field}'.encode())
    with pytest.raises(public_lock.PublicLockError):
        public_lock.public_lock_repair_content(root / "pyproject.toml", source, changed)


@pytest.mark.parametrize(
    "changed",
    [
        DRIFT.split(b"wheels =")[0],
        DRIFT + b'\n[[package]]\nname = "extra"\nversion = "1.0"\n',
        DRIFT.replace(b'sdist = {', b'unexpected = {'),
        b"invalid TOML [",
    ],
)
def test_missing_or_extra_structure_is_preserved(root: Path, changed: bytes) -> None:
    (root / "uv.lock").write_bytes(changed)
    with pytest.raises(SystemExit):
        tasks.ensure_public_lock(allow_repair=True)
    assert (root / "uv.lock").read_bytes() == changed


def test_workspace_source_changes_are_not_repairable(root: Path) -> None:
    workspace = (
        b'\n[[package]]\nname = "local"\nversion = "1.0"\n'
        b'source = { editable = "src/api" }\n'
    )
    with pytest.raises(public_lock.PublicLockError):
        public_lock.public_lock_repair_content(
            root / "pyproject.toml",
            BASELINE + workspace,
            DRIFT + workspace.replace(b"editable", b"virtual"),
        )


@pytest.mark.parametrize("dirty", ["staged", "manifest", "conflict", "missing-head"])
def test_git_guards_preserve_lock(
    monkeypatch: pytest.MonkeyPatch, root: Path, dirty: str
) -> None:
    original = tasks.public_lock_git

    def git(*args: str) -> bytes:
        if dirty == "staged" and args[:2] == ("diff", "--cached"):
            return b"uv.lock\n"
        if dirty == "manifest" and args[:2] == ("diff", "--name-only"):
            return b"src/api/pyproject.toml\n"
        if dirty == "conflict" and args[0] == "ls-files":
            return b"conflict\n"
        if dirty == "missing-head":
            raise public_lock.PublicLockError("No HEAD")
        return original(*args)

    monkeypatch.setattr(tasks, "public_lock_git", git)
    with pytest.raises(SystemExit):
        tasks.ensure_public_lock(allow_repair=True)
    assert (root / "uv.lock").read_bytes() == DRIFT


def test_valid_lock_does_not_require_git_or_clean_manifests(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    (root / "uv.lock").write_bytes(BASELINE.replace(b'version = "1.0"', b'version = "2.0"'))
    (root / "src/api/pyproject.toml").write_bytes(MEMBER + b"description = 'edited'\n")
    monkeypatch.setattr(
        tasks, "public_lock_git", lambda *_: pytest.fail("Valid lock must not need Git")
    )
    tasks.ensure_public_lock(allow_repair=True)
    assert b'version = "2.0"' in (root / "uv.lock").read_bytes()


@pytest.mark.parametrize("changed", ["head", "lock"])
def test_concurrent_changes_are_preserved(
    monkeypatch: pytest.MonkeyPatch, root: Path, changed: str
) -> None:
    original = tasks.public_lock_repair_snapshot
    calls = 0

    def snapshot() -> tuple[str, bytes]:
        nonlocal calls
        calls += 1
        result = original()
        if calls == 2:
            if changed == "head":
                return "different-head", BASELINE
            (root / "uv.lock").write_bytes(DRIFT + b"\n# concurrent edit\n")
        return result

    monkeypatch.setattr(tasks, "public_lock_repair_snapshot", snapshot)
    with pytest.raises(SystemExit):
        tasks.ensure_public_lock(allow_repair=True)
    assert (root / "uv.lock").read_bytes().startswith(DRIFT)


def test_repair_lock_does_not_depend_on_venv(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(root / "first"))
    first = tasks.public_lock_repair_lock_path()
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(root / "second"))
    assert tasks.public_lock_repair_lock_path() == first


def test_only_explicit_sync_enables_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        tasks,
        "target_sync_dev_approved_index",
        lambda *, allow_lock_repair=False: calls.append(allow_lock_repair),
    )
    tasks.main(["sync-dev-approved-index"])
    tasks.target_sync_dev_approved_index()
    assert calls == [True, False]
