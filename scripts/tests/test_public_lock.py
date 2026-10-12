from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = b"[tool.uv.workspace]\nmembers = []\n"
VALID_LOCK = (
    b'version = 1\n[[package]]\nname = "example"\nversion = "1.0.0"\n'
    b'source = { registry = "https://pypi.org/simple" }\n'
)


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tasks = load_module("public_lock_tasks", ROOT / "scripts" / "tasks.py")
public_lock = sys.modules["public_lock"]


@pytest.fixture
def lock_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_bytes(PROJECT)
    lock_path = tmp_path / "uv.lock"
    lock_path.write_bytes(VALID_LOCK)
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    return lock_path


def test_valid_public_lock_is_preserved(lock_files: Path) -> None:
    tasks.ensure_public_lock()
    assert lock_files.read_bytes() == VALID_LOCK


def test_invalid_public_lock_is_preserved_and_rejected(lock_files: Path) -> None:
    invalid = VALID_LOCK.replace(
        b"https://pypi.org/simple", b"https://packages.example.test/simple"
    )
    lock_files.write_bytes(invalid)

    with pytest.raises(SystemExit):
        tasks.ensure_public_lock()

    assert lock_files.read_bytes() == invalid


@pytest.mark.parametrize(
    ("content", "expected_code"),
    (
        (f"example==1.0.0 \\\n    --hash=sha256:{'0' * 64}\n", 0),
        ("example==1.0.0\n", 1),
        ("example @ https://example.test/example.whl\n", 1),
    ),
)
def test_requirements_cli_validates_without_modifying_file(
    tmp_path: Path, content: str, expected_code: int
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(content, encoding="utf-8")

    assert public_lock.main(["requirements", str(requirements)]) == expected_code
    assert requirements.read_text(encoding="utf-8") == content
