# /// script
# requires-python = ">=3.14"
# ///
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from shutil import which
from typing import Any, TypeGuard
from urllib.parse import urlparse

PUBLIC_ARTIFACT_HOST = "files.pythonhosted.org"
PUBLIC_PYPI_INDEX = "https://pypi.org/simple"
HASH_OPTION = re.compile(r"^--hash=sha256:[0-9a-f]{64}$")
PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.\-\[\],]+==[^\s;]+(?:\s*;\s*.+)?$")


class PublicLockError(ValueError):
    pass


def workspace_members(project: dict[str, Any]) -> set[str]:
    table = project
    for key in ("tool", "uv", "workspace"):
        value = table.get(key)
        if not is_toml_table(value):
            raise PublicLockError("pyproject.toml must define tool.uv.workspace.")
        table = value
    members = table.get("members")
    if not isinstance(members, list):
        raise PublicLockError("Workspace members must be a list of paths.")
    paths: set[str] = set()
    for member in members:
        if not isinstance(member, str):
            raise PublicLockError("Workspace members must be a list of paths.")
        paths.add(member)
    return paths


def validate_artifact_url(url: object) -> None:
    if not isinstance(url, str):
        raise PublicLockError("uv.lock contains a non-string artifact URL.")
    try:
        parsed = urlparse(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == PUBLIC_ARTIFACT_HOST
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        valid = False
    if not valid:
        raise PublicLockError("uv.lock contains an artifact URL outside public PyPI.")


def validate_public_lock(pyproject_path: Path, lock_path: Path) -> None:
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    validate_public_lock_data(project, lock)


def validate_public_lock_data(project: dict[str, Any], lock: dict[str, Any]) -> None:
    allowed_workspace_sources = workspace_members(project) | {"."}
    registry_packages = 0
    packages = lock.get("package", [])
    if not isinstance(packages, list):
        raise PublicLockError("uv.lock packages must be a list of tables.")
    for package in packages:
        if not is_toml_table(package):
            raise PublicLockError("uv.lock packages must be a list of tables.")
        source = package.get("source", {})
        if not isinstance(source, dict):
            source = {}
        if source == {"registry": PUBLIC_PYPI_INDEX}:
            registry_packages += 1
        elif (
            len(source) == 1
            and next(iter(source), None) in {"editable", "virtual"}
            and next(iter(source.values()), None) in allowed_workspace_sources
        ):
            pass
        else:
            raise PublicLockError(
                f"uv.lock package {package.get('name', '<unknown>')} "
                "has an unsupported source type."
            )

        sdist = package.get("sdist")
        if isinstance(sdist, dict) and "url" in sdist:
            validate_artifact_url(sdist["url"])
        wheels = package.get("wheels", [])
        if not isinstance(wheels, list):
            raise PublicLockError("uv.lock wheels must be a list.")
        for wheel in wheels:
            if isinstance(wheel, dict) and "url" in wheel:
                validate_artifact_url(wheel["url"])

    if registry_packages == 0:
        raise PublicLockError("uv.lock does not contain public PyPI registry packages.")


def read_index_toml(root: Path, name: str) -> dict[str, Any]:
    git = which("git")
    if git is None:
        raise PublicLockError("Git is required to inspect the commit index.")
    # Inherit GIT_INDEX_FILE: path-limited commits use a temporary index.
    try:
        result = subprocess.run(
            [git, "--no-pager", "show", f":0:{name}"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicLockError(f"Cannot read staged {name} using Git.") from error
    if result.returncode:
        raise PublicLockError(
            f"Cannot read staged {name}; it must exist without merge conflicts."
        )
    try:
        return tomllib.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise PublicLockError(f"Staged {name} is not valid UTF-8 TOML.") from error


def validate_staged_public_lock(root: Path) -> None:
    project = read_index_toml(root, "pyproject.toml")
    lock = read_index_toml(root, "uv.lock")
    validate_public_lock_data(project, lock)


def is_toml_table(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def same_toml_value(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if is_toml_table(left) and is_toml_table(right):
        return left.keys() == right.keys() and all(
            same_toml_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            same_toml_value(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def public_lock_repair_content(
    pyproject_path: Path, baseline: bytes, current: bytes
) -> bytes:
    """Accept only the approved-index URL rewrite and metadata omission."""
    original = tomllib.loads(baseline.decode("utf-8"))
    candidate = tomllib.loads(current.decode("utf-8"))
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    validate_public_lock_data(project, original)
    original_packages = original.get("package", [])
    candidate_packages = candidate.get("package", [])
    if not isinstance(candidate_packages, list) or len(original_packages) != len(
        candidate_packages
    ):
        raise PublicLockError("Cannot repair uv.lock: package count changed.")

    def restore_artifact(source: object, changed: object) -> None:
        if not is_toml_table(source) or not is_toml_table(changed):
            return
        if isinstance(source.get("url"), str) and isinstance(changed.get("url"), str):
            changed["url"] = source["url"]
        for name in ("size", "upload-time"):
            if name in source and name not in changed:
                changed[name] = source[name]

    for source, changed in zip(original_packages, candidate_packages, strict=True):
        if not is_toml_table(changed):
            raise PublicLockError("Cannot repair uv.lock: invalid package.")
        old_source = source.get("source", {})
        new_source = changed.get("source", {})
        if (
            is_toml_table(new_source)
            and "registry" in old_source
            and isinstance(new_source.get("registry"), str)
        ):
            new_source["registry"] = old_source["registry"]
        restore_artifact(source.get("sdist"), changed.get("sdist"))
        old_wheels = source.get("wheels", [])
        new_wheels = changed.get("wheels", [])
        if not isinstance(new_wheels, list) or len(old_wheels) != len(new_wheels):
            raise PublicLockError("Cannot repair uv.lock: artifact count changed.")
        for old_wheel, new_wheel in zip(old_wheels, new_wheels, strict=True):
            restore_artifact(old_wheel, new_wheel)

    if not same_toml_value(original, candidate):
        raise PublicLockError(
            "Cannot repair uv.lock: changes exceed registry/artifact URLs "
            "and omitted artifact size/upload-time. The file was preserved."
        )
    return baseline


def validate_exported_requirements(
    requirements_path: Path,
) -> None:
    pending_requirement_line: int | None = None
    pending_requirement_has_hash = False

    def finish_pending_requirement() -> None:
        if pending_requirement_line is not None and not pending_requirement_has_hash:
            raise PublicLockError(
                f"Exported requirements line {pending_requirement_line} "
                "does not have a SHA-256 hash."
            )

    for line_number, raw_line in enumerate(
        requirements_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip().removesuffix("\\").rstrip()
        if not line or line.startswith("#"):
            continue
        if HASH_OPTION.fullmatch(line):
            if pending_requirement_line is None:
                raise PublicLockError(
                    f"Exported requirements line {line_number} has an orphaned hash."
                )
            pending_requirement_has_hash = True
            continue
        finish_pending_requirement()
        pending_requirement_line = None
        pending_requirement_has_hash = False
        if any(
            token in line.lower()
            for token in (" @ ", "://", "file:", "git+", "--find-links")
        ):
            raise PublicLockError(
                f"Exported requirements line {line_number} contains a direct source."
            )
        if PINNED_REQUIREMENT.fullmatch(line):
            pending_requirement_line = line_number
            continue
        raise PublicLockError(
            f"Exported requirements line {line_number} is not a hash-pinned "
            "registry requirement."
        )
    finish_pending_requirement()


def main(argv: Sequence[str]) -> int:
    staged = list(argv) == ["staged"]
    try:
        if staged:
            validate_staged_public_lock(Path(__file__).resolve().parents[1])
        elif len(argv) == 3 and argv[0] == "lock":
            validate_public_lock(Path(argv[1]), Path(argv[2]))
        elif len(argv) == 2 and argv[0] == "requirements":
            validate_exported_requirements(Path(argv[1]))
        else:
            print(
                "Usage: public_lock.py "
                "(staged | lock <pyproject> <uv.lock> | requirements <requirements>)",
                file=sys.stderr,
            )
            return 2
    except (OSError, PublicLockError, tomllib.TOMLDecodeError) as error:
        if staged:
            print(
                "error: Staged public lock validation failed. Ensure uv.lock and "
                "pyproject.toml exist, are valid, and use only public PyPI sources "
                "and declared workspace members. No files or staged changes were "
                "modified. See docs/deployment.md for lock recovery.",
                file=sys.stderr,
            )
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
