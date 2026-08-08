from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

PUBLIC_ARTIFACT_HOST = "files.pythonhosted.org"
PUBLIC_PYPI_INDEX = "https://pypi.org/simple"
HASH_OPTION = re.compile(r"^--hash=sha256:[0-9a-f]{64}$")
PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.\-\[\],]+==[^\s;]+(?:\s*;\s*.+)?$")


class PublicLockError(ValueError):
    pass


def workspace_members(pyproject_path: Path) -> set[str]:
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return set(project["tool"]["uv"]["workspace"]["members"])


def validate_artifact_url(url: object) -> None:
    if not isinstance(url, str):
        raise PublicLockError("uv.lock contains a non-string artifact URL.")
    parsed = urlparse(url)
    try:
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
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    allowed_workspace_sources = workspace_members(pyproject_path) | {"."}
    registry_packages = 0
    for package in lock.get("package", []):
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
        for wheel in package.get("wheels", []):
            if isinstance(wheel, dict) and "url" in wheel:
                validate_artifact_url(wheel["url"])

    if registry_packages == 0:
        raise PublicLockError("uv.lock does not contain public PyPI registry packages.")


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
    try:
        if len(argv) == 3 and argv[0] == "lock":
            validate_public_lock(Path(argv[1]), Path(argv[2]))
        elif len(argv) == 2 and argv[0] == "requirements":
            validate_exported_requirements(Path(argv[1]))
        else:
            print(
                "Usage: public_lock.py "
                "(lock <pyproject> <uv.lock> | requirements <requirements>)",
                file=sys.stderr,
            )
            return 2
    except (OSError, PublicLockError, tomllib.TOMLDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
