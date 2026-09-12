#!/usr/bin/env python3
"""Small, deterministic repository checks and public-review inventory."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

SCHEMA_VERSION = "2.1"
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(".github/repo-health.toml")
GIT_TIMEOUT_SECONDS = 30
BICEP_PARAMETER_ENTRYPOINTS = (
    (Path("infra/main.bicep"), Path("infra/main.parameters.json")),
    (Path("infra/sli/main.bicep"), Path("infra/sli/main.parameters.json")),
)
Status = Literal["pass", "fail"]

_BICEP_RESOURCE = re.compile(
    r"\bresource\s+\w+\s+'(?P<type>[^'@]+)@(?P<version>[^']+)'"
)
_BICEP_PARAMETER = re.compile(
    r"(?m)^\s*param\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+\S+"
    r"(?P<default>\s*=\s*)?"
)
_DOCKER_FROM = re.compile(r"(?mi)^\s*FROM\s+(?P<image>\S+)")
_EXTERNAL_LINK = re.compile(r"https?://[^\s<>()\"']+")
_K8S_KIND = re.compile(r"(?m)^kind:\s*(?P<kind>[A-Za-z][A-Za-z0-9]*)\s*$")
_K8S_DOCUMENT = re.compile(r"(?m)^---\s*$")
_GH_AW_SETUP = re.compile(r"(?m)^\s*version:\s*(?P<version>v\d+\.\d+\.\d+)\s*$")
_GH_AW_METADATA = re.compile(
    r'gh-aw-metadata:\s*\{.*?"compiler_version":"(?P<version>v\d+\.\d+\.\d+)".*\}'
)
_K8S_TASK_VERSION = re.compile(
    r'(?m)^K8S_VERSION\s*=\s*"(?P<version>\d+\.\d+(?:\.\d+)?)"\s*$'
)
_K8S_BICEP_VERSION = re.compile(
    r"(?m)^\s*param\s+kubernetesVersion\s+string\s*=\s*"
    r"'(?P<version>\d+\.\d+(?:\.\d+)?)'\s*$"
)


class RepoHealthError(RuntimeError):
    pass


@dataclass(frozen=True)
class Coordinate:
    category: str
    path: str
    location: str
    value: str
    status: Status = "pass"


@dataclass(frozen=True)
class Check:
    id: str
    status: Status
    message: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    status: Status
    path: str
    location: str
    message: str


def _git(root: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RepoHealthError("git is required")
    try:
        completed = subprocess.run(
            [git, *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise RepoHealthError(
            f"git {' '.join(args)} exceeded the {GIT_TIMEOUT_SECONDS}-second limit"
        ) from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise RepoHealthError(f"git {' '.join(args)} failed: {error}") from error
    return completed.stdout


def list_repository_files(root: Path) -> tuple[Path, ...]:
    output = _git(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    return tuple(Path(value) for value in output.split("\0") if value)


def repository_commit(root: Path) -> str | None:
    try:
        return _git(root, "rev-parse", "HEAD").strip() or None
    except RepoHealthError:
        return None


def load_kubernetes_schema_excluded_kinds(path: Path) -> tuple[str, ...]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RepoHealthError(f"could not read {path}: {error}") from error
    values = document.get("kubernetes_schema_excluded_kinds")
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value for value in values)
    ):
        raise RepoHealthError(
            f"{path} must define a non-empty kubernetes_schema_excluded_kinds array"
        )
    if len(values) != len(set(values)):
        raise RepoHealthError(
            f"{path} contains duplicate kubernetes_schema_excluded_kinds"
        )
    return tuple(values)


def _coordinates_for_file(root: Path, relative: Path) -> list[Coordinate]:
    path = root / relative
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    name = relative.as_posix()
    coordinates: list[Coordinate] = []
    if relative.suffix == ".bicep":
        for index, match in enumerate(_BICEP_RESOURCE.finditer(text), 1):
            coordinates.append(
                Coordinate(
                    "bicep-resource-api",
                    name,
                    f"resource:{index}",
                    f"{match.group('type')}@{match.group('version')}",
                )
            )
    if relative.name == "Dockerfile":
        for index, match in enumerate(_DOCKER_FROM.finditer(text), 1):
            coordinates.append(
                Coordinate("docker-base-image", name, f"from:{index}", match["image"])
            )
    if relative.suffix.lower() == ".md":
        seen: set[str] = set()
        for match in _EXTERNAL_LINK.finditer(text):
            value = match.group(0).rstrip(".,;:")
            if value in seen:
                continue
            seen.add(value)
            coordinates.append(
                Coordinate(
                    "documentation-external-link",
                    name,
                    f"line:{text.count(chr(10), 0, match.start()) + 1}",
                    value,
                )
            )
    if relative == Path("src/external-sli-publisher/host.json"):
        try:
            document = json.loads(text)
            bundle = document["extensionBundle"]["version"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise RepoHealthError(f"{name} has no extensionBundle.version") from error
        if not isinstance(bundle, str):
            raise RepoHealthError(f"{name} extensionBundle.version must be a string")
        coordinates.append(
            Coordinate(
                "function-extension-bundle", name, "extensionBundle.version", bundle
            )
        )
    if name.startswith("k8s/") and relative.suffix in {".yaml", ".yml"}:
        for index, document in enumerate(_K8S_DOCUMENT.split(text), 1):
            match = _K8S_KIND.search(document)
            if match:
                coordinates.append(
                    Coordinate(
                        "kubernetes-manifest",
                        name,
                        f"document:{index}",
                        match["kind"],
                    )
                )
    return coordinates


def scan_inventory(root: Path) -> list[Coordinate]:
    coordinates: list[Coordinate] = []
    for relative in list_repository_files(root):
        coordinates.extend(_coordinates_for_file(root, relative))
    return sorted(
        coordinates,
        key=lambda item: (item.category, item.path, item.location, item.value),
    )


def validate_bicep_parameter_files(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for template_path, parameter_path in BICEP_PARAMETER_ENTRYPOINTS:
        template = root / template_path
        parameters = root / parameter_path
        try:
            template_text = template.read_text(encoding="utf-8")
            document = json.loads(parameters.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            findings.append(
                Finding(
                    "bicep-parameters",
                    "fail",
                    parameter_path.as_posix(),
                    "parameters",
                    f"could not read parameter pair: {error}",
                )
            )
            continue
        declarations = {
            match["name"]: match["default"] is not None
            for match in _BICEP_PARAMETER.finditer(template_text)
        }
        values = document.get("parameters") if isinstance(document, dict) else None
        if not isinstance(values, dict):
            findings.append(
                Finding(
                    "bicep-parameters",
                    "fail",
                    parameter_path.as_posix(),
                    "parameters",
                    "top-level parameters must be an object",
                )
            )
            continue
        for name in sorted(set(values) - set(declarations)):
            findings.append(
                Finding(
                    "bicep-parameters",
                    "fail",
                    parameter_path.as_posix(),
                    name,
                    f"parameter is not declared by {template_path}",
                )
            )
        for name, has_default in declarations.items():
            if not has_default and name not in values:
                findings.append(
                    Finding(
                        "bicep-parameters",
                        "fail",
                        parameter_path.as_posix(),
                        name,
                        f"required parameter from {template_path} is missing",
                    )
                )
    return findings


def _single_match(path: Path, pattern: re.Pattern[str], label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RepoHealthError(f"could not read {path}: {error}") from error
    values = [match["version"] for match in pattern.finditer(text)]
    if len(values) != 1:
        raise RepoHealthError(f"{label} must appear exactly once in {path}")
    return values[0]


def _major_minor(version: str) -> tuple[int, int]:
    major, minor, *_ = version.split(".")
    return int(major), int(minor)


def _check_versions(root: Path) -> list[Check]:
    checks: list[Check] = []
    try:
        task_version = _single_match(
            root / "scripts/tasks.py", _K8S_TASK_VERSION, "K8S_VERSION"
        )
        bicep_version = _single_match(
            root / "infra/main.bicep",
            _K8S_BICEP_VERSION,
            "kubernetesVersion default",
        )
        status: Status = (
            "pass"
            if _major_minor(task_version) == _major_minor(bicep_version)
            else "fail"
        )
        checks.append(
            Check(
                "kubernetes-version",
                status,
                f"tasks={task_version}, bicep={bicep_version}",
            )
        )
    except RepoHealthError as error:
        checks.append(Check("kubernetes-version", "fail", str(error)))

    try:
        setup = _single_match(
            root / ".github/workflows/copilot-setup-steps.yml",
            _GH_AW_SETUP,
            "gh-aw setup version",
        )
        compiler = _single_match(
            root / ".github/workflows/aks-updates-analyzer.lock.yml",
            _GH_AW_METADATA,
            "gh-aw compiler version",
        )
        status = "pass" if setup == compiler else "fail"
        checks.append(
            Check(
                "gh-aw-compiler-version", status, f"setup={setup}, compiler={compiler}"
            )
        )
    except RepoHealthError as error:
        checks.append(Check("gh-aw-compiler-version", "fail", str(error)))

    images = [
        item for item in scan_inventory(root) if item.category == "docker-base-image"
    ]
    unpinned = [item.value for item in images if "@sha256:" not in item.value]
    checks.append(
        Check(
            "docker-base-digest",
            "fail" if unpinned or not images else "pass",
            (
                f"unpinned images: {', '.join(unpinned)}"
                if unpinned
                else f"{len(images)} base images are digest-pinned"
            ),
        )
    )
    return checks


def build_result(root: Path, include_checks: bool) -> dict[str, Any]:
    inventory = scan_inventory(root)
    parameter_findings = validate_bicep_parameter_files(root)
    checks = _check_versions(root) if include_checks else []
    findings = [
        Finding(check.id, check.status, "", check.id, check.message)
        for check in checks
        if check.status == "fail"
    ]
    findings.extend(parameter_findings)
    excluded_kinds = load_kubernetes_schema_excluded_kinds(root / CONFIG_PATH)
    excluded = [
        asdict(item)
        for item in inventory
        if item.category == "kubernetes-manifest" and item.value in excluded_kinds
    ]
    files = list_repository_files(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "repository_root": str(root.resolve()),
        "commit": repository_commit(root),
        "inventory": [asdict(item) for item in inventory],
        "checks": [asdict(item) for item in checks],
        "coverage": {
            "repository_files": len(files),
            "inventory_coordinates": len(inventory),
            "excluded_kubernetes_documents": excluded,
        },
        "findings": [asdict(item) for item in findings],
        "environment_limitations": [
            {
                "area": "external-freshness",
                "status": "excluded",
                "reason": (
                    "Published versions and support dates require Renovate or "
                    "an explicit public-information review."
                ),
            }
        ],
    }


def json_output(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _validate_report(report: object) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise RepoHealthError("report input must be an object")
    typed_report = cast(dict[str, Any], report)
    required = {
        "schema_version",
        "repository_root",
        "inventory",
        "checks",
        "coverage",
        "findings",
        "environment_limitations",
    }
    missing = sorted(required - set(typed_report))
    if missing:
        raise RepoHealthError(f"report input is missing keys: {', '.join(missing)}")
    if typed_report["schema_version"] != SCHEMA_VERSION:
        raise RepoHealthError(f"report input schema_version must be {SCHEMA_VERSION}")
    for key in ("inventory", "checks", "findings", "environment_limitations"):
        if not isinstance(typed_report[key], list):
            raise RepoHealthError(f"report input {key} must be an array")
    if not isinstance(typed_report["coverage"], dict):
        raise RepoHealthError("report input coverage must be an object")
    return typed_report


def text_output(result: dict[str, Any]) -> str:
    checks = result["checks"]
    findings = result["findings"]
    lines = [
        f"Repository files: {result['coverage']['repository_files']}",
        f"Inventory coordinates: {result['coverage']['inventory_coordinates']}",
    ]
    lines.extend(
        f"[{check['status']}] {check['id']}: {check['message']}" for check in checks
    )
    lines.extend(
        f"[{finding['status']}] {finding['rule_id']}: "
        f"{finding['path']}:{finding['location']} {finding['message']}"
        for finding in findings
    )
    if not findings:
        lines.append(f"ok: inventory {len(result['inventory'])} coordinates")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inventory", "check"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--format", choices=("json", "text"), default="text"
        )
    report = subparsers.add_parser("report")
    report.add_argument("path", type=Path)
    subparsers.add_parser("validate-bicep-parameters")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "report":
            result = _validate_report(json.loads(args.path.read_text(encoding="utf-8")))
        elif args.command == "validate-bicep-parameters":
            findings = validate_bicep_parameter_files(args.root)
            if findings:
                print(
                    "\n".join(
                        f"error: {item.path}:{item.location}: {item.message}"
                        for item in findings
                    ),
                    file=sys.stderr,
                )
                return 1
            print("ok: Bicep parameter files are valid")
            return 0
        else:
            result = build_result(args.root, include_checks=args.command == "check")
        output = (
            json_output(result)
            if getattr(args, "format", "text") == "json"
            else text_output(result)
        )
        print(output, end="")
        return 1 if result["findings"] else 0
    except (OSError, json.JSONDecodeError, RepoHealthError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
