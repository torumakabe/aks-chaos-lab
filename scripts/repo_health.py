#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import ast
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import unquote, urlsplit, urlunsplit

SCHEMA_VERSION = "2.1"
CONFIG_SCHEMA_VERSION = 2
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(".github/repo-health.toml")
GIT_TIMEOUT_SECONDS = 30
Status = Literal["pass", "fail", "unverified", "excluded"]
VALID_STATUSES = {"pass", "fail", "unverified", "excluded"}
BICEP_PARAMETER_ENTRYPOINTS = {
    "infra/main.parameters.json": "infra/main.bicep",
    "infra/sli/main.parameters.json": "infra/sli/main.bicep",
}

_WORKFLOW_USES = re.compile(r"^[ \t]*(?:-[ \t]*)?uses:[ \t]*([^\s#]+)", re.MULTILINE)
_DOCKER_FROM = re.compile(
    r"^[ \t]*FROM[ \t]+(?:--platform=\S+[ \t]+)?(?P<image>\S+)",
    re.MULTILINE | re.IGNORECASE,
)
_BICEP_RESOURCE = re.compile(
    r"^[ \t]*resource[ \t]+\w+[ \t]+'(?P<type>[^'@]+)@(?P<version>[^']+)'",
    re.MULTILINE,
)
_BICEP_PARAMETER = re.compile(
    r"^[ \t]*param[ \t]+(?P<name>[A-Za-z_]\w*)[ \t]+\w+[ \t]*=[ \t]*"
    r"['\"](?P<value>[^'\"]+)",
    re.MULTILINE,
)
_BICEP_PARAMETER_DECLARATION = re.compile(
    r"^[ \t]*param[ \t]+(?P<name>[A-Za-z_]\w*)[ \t]+"
    r"(?P<type>[^=\r\n]+?)(?P<default>[ \t]*=.*)?$",
    re.MULTILINE,
)
_WORKFLOW_KUBERNETES_VERSION = re.compile(
    r"-kubernetes-version\s+(?P<value>[0-9]+(?:\.[0-9]+){1,2})"
)
_WORKFLOW_TOOL_VERSION = re.compile(
    r"^[ \t]*(?P<name>LEFTHOOK_VERSION|KUBECONFORM_VERSION|HELM_VERSION)="
    r"['\"]?(?P<value>v?[0-9][^'\"\s#]*)",
    re.MULTILINE,
)
_ACTIONLINT_IMAGE = re.compile(r"\brhysd/actionlint:(?P<value>v?[0-9][A-Za-z0-9._-]*)")
_AZD_REQUIRED_VERSION = re.compile(
    r"^[ \t]+azd:[ \t]*['\"]?>=[ \t]*(?P<value>[0-9][^'\"\s#]*)",
    re.MULTILINE,
)
_GH_AW_VERSION = re.compile(r"^[ \t]*version:[ \t]*['\"]?(?P<value>v[0-9][^'\"\s#]*)")
_GH_AW_METADATA = re.compile(r"^# gh-aw-metadata:\s*(?P<metadata>\{.*\})$")
_YAML_API_VERSION = re.compile(r"^apiVersion:\s*(?P<value>\S+)", re.MULTILINE)
_YAML_KIND = re.compile(r"^kind:\s*(?P<value>\S+)", re.MULTILINE)
_YAML_DOCUMENT_SEPARATOR = re.compile(r"^---[ \t]*(?:#.*)?$")
_YAML_BLOCK_SCALAR = re.compile(
    r"(?:^|:\s+|-\s+)(?:(?:&\S+|!\S+)[ \t]+)*"
    r"[|>](?:[1-9][+-]?|[+-][1-9]?)?(?:[ \t]+#.*)?$"
)
_HELM_CHART = re.compile(r"^[ \t]+(?:-[ \t]*)?chart:[ \t]*(?P<value>\S+)", re.MULTILINE)
_HELM_VERSION = re.compile(
    r"^[ \t]+(?:-[ \t]*)?version:[ \t]*['\"]?(?P<value>[^\s'\"]+)",
    re.MULTILINE,
)
_HELM_VALUES = re.compile(
    r"^[ \t]+(?:-[ \t]*)?values:[ \t]*['\"]?(?P<value>[^\s'\"]+)",
    re.MULTILINE,
)
_DOC_REFERENCE = re.compile(
    r"(?:ADR-\d{3}|docs/(?:adr|features)/[A-Za-z0-9_./-]+\.md|docs/workarounds\.md)"
)
_MARKDOWN_FENCE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})")
_MARKDOWN_INLINE_CODE = re.compile(r"`+[^`\n]*`+")
_MARKDOWN_INLINE_LINK = re.compile(r"\[[^\]\n]*\]\((?P<target><[^>\n]+>|[^)\n]+)\)")
_MARKDOWN_REFERENCE_LINK = re.compile(
    r"^[ \t]*\[[^\]\n]+\]:[ \t]*(?P<target><[^>\n]+>|\S+)"
)
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ADR_DOCUMENT = re.compile(r"^docs/adr/\d{3}-[^/]+\.md$")
_ADR_STATUS_HEADING = re.compile(r"^## Status[ \t]*$")
_ADR_LEGACY_STATUS = re.compile(r"^-[ \t]*Status:")
_ADR_STATUS_VALUE = re.compile(
    r"^(?:Accepted|Rejected|Deprecated|Superseded by)(?:\b|[ \t])"
)


class RepoHealthError(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class Coordinate:
    category: str
    path: str
    location: str
    value: str
    status: Status = "unverified"


@dataclass(frozen=True, order=True)
class Finding:
    rule_id: str
    status: Status
    path: str
    location: str
    message: str


@dataclass(frozen=True, order=True)
class ValidationIssue:
    path: str
    location: str
    message: str


@dataclass(frozen=True)
class Location:
    path: str
    selector: str


@dataclass(frozen=True, order=True)
class TargetFingerprint:
    path: str
    selector: str
    value: str


@dataclass(frozen=True)
class ExceptionPolicy:
    reason: str
    owner: str
    tracking: str
    review_by: date
    resolution: str
    canonical_values: tuple[str, ...]
    target_fingerprint: tuple[TargetFingerprint, ...]


@dataclass(frozen=True)
class Rule:
    id: str
    canonical: Location
    targets: tuple[Location, ...]
    enforce: bool
    exception: ExceptionPolicy | None


@dataclass(frozen=True)
class Check:
    id: str
    status: Status
    canonical: tuple[Coordinate, ...]
    targets: tuple[Coordinate, ...]
    message: str
    exception: dict[str, object] | None = None
    failure_location: Location | None = None


@dataclass
class Scan:
    coordinates: list[Coordinate]
    recognized: set[str]
    extraction_failures: list[dict[str, str]]
    validation_issues: list[ValidationIssue]


Extractor = Callable[[Path, str, str], list[Coordinate]]


def _git(root: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RepoHealthError("git could not be executed: command not found")
    try:
        completed = subprocess.run(
            [git, *args],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise RepoHealthError(
            f"git {' '.join(args)} exceeded the {GIT_TIMEOUT_SECONDS}-second limit"
        ) from error
    except OSError as error:
        raise RepoHealthError(f"git could not be executed: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RepoHealthError(
            f"git {' '.join(args)} failed with exit code {completed.returncode}: "
            f"{detail or 'no diagnostic output'}"
        )
    return completed.stdout.decode("utf-8", errors="surrogateescape")


def list_tracked_files(root: Path) -> list[str]:
    output = _git(root, "ls-files", "-z")
    return sorted(
        path
        for path in output.split("\0")
        if path and (root / PurePosixPath(path)).is_file()
    )


def list_untracked_files(root: Path) -> list[str]:
    output = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(
        path
        for path in output.split("\0")
        if path and (root / PurePosixPath(path)).is_file()
    )


def repository_commit(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _coordinate(
    category: str, path: str, location: str, value: str, status: Status = "unverified"
) -> Coordinate:
    return Coordinate(category, path, location, value, status)


def extract_bicep_parameter(root: Path, path: str, selector: str) -> list[Coordinate]:
    name = selector.partition(":")[2]
    text = (root / path).read_text(encoding="utf-8")
    return [
        _coordinate(
            "version-constant",
            path,
            f"line:{_line_number(text, match.start())}:param:{name}",
            match.group("value"),
        )
        for match in _BICEP_PARAMETER.finditer(text)
        if match.group("name") == name
    ]


def extract_workflow_kubernetes_version(
    root: Path, path: str, _selector: str
) -> list[Coordinate]:
    text = (root / path).read_text(encoding="utf-8")
    return [
        _coordinate(
            "version-constant",
            path,
            f"line:{_line_number(text, match.start())}:kubernetes-version",
            match.group("value"),
        )
        for match in _WORKFLOW_KUBERNETES_VERSION.finditer(text)
    ]


def extract_python_constant(root: Path, path: str, selector: str) -> list[Coordinate]:
    name = selector.partition(":")[2]
    text = (root / path).read_text(encoding="utf-8")
    tree = ast.parse(text, filename=path)
    coordinates: list[Coordinate] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            coordinates.append(
                _coordinate(
                    "version-constant",
                    path,
                    f"line:{node.lineno}:constant:{name}",
                    node.value.value,
                )
            )
    return coordinates


def extract_gh_aw_setup_version(
    root: Path, path: str, _selector: str
) -> list[Coordinate]:
    text = (root / path).read_text(encoding="utf-8")
    coordinates: list[Coordinate] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "github/gh-aw-actions/setup-cli@" not in line:
            continue
        for version_index in range(index + 1, min(index + 6, len(lines))):
            match = _GH_AW_VERSION.match(lines[version_index])
            if match:
                coordinates.append(
                    _coordinate(
                        "gh-aw-version",
                        path,
                        f"line:{version_index + 1}:setup-version",
                        match.group("value"),
                    )
                )
                break
    return coordinates


def extract_gh_aw_compiler_version(
    root: Path, path: str, _selector: str
) -> list[Coordinate]:
    lines = (root / path).read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    first_line = lines[0]
    match = _GH_AW_METADATA.match(first_line)
    if match is None:
        return []
    try:
        metadata = json.loads(match.group("metadata"))
    except json.JSONDecodeError as error:
        raise RepoHealthError(f"{path}: invalid gh-aw metadata: {error}") from error
    version = metadata.get("compiler_version")
    if not isinstance(version, str):
        return []
    return [_coordinate("gh-aw-version", path, "line:1:compiler-version", version)]


def extract_docker_from(root: Path, path: str, _selector: str) -> list[Coordinate]:
    text = (root / path).read_text(encoding="utf-8")
    return [
        _coordinate(
            "docker-base-image",
            path,
            f"line:{_line_number(text, match.start())}:from",
            match.group("image"),
        )
        for match in _DOCKER_FROM.finditer(text)
    ]


EXTRACTORS: dict[str, Extractor] = {
    "bicep-parameter": extract_bicep_parameter,
    "docker-from": extract_docker_from,
    "gh-aw-compiler-version": extract_gh_aw_compiler_version,
    "gh-aw-setup-version": extract_gh_aw_setup_version,
    "python-constant": extract_python_constant,
    "workflow-kubernetes-version": extract_workflow_kubernetes_version,
}
KNOWN_RULE_IDS = {
    "docker-base-digest",
    "gh-aw-compiler-version",
    "kubernetes-version",
}


def _extract_location(root: Path, location: Location) -> list[Coordinate]:
    selector_kind = location.selector.partition(":")[0]
    extractor = EXTRACTORS.get(selector_kind)
    if extractor is None:
        raise RepoHealthError(f"unknown built-in selector: {location.selector}")
    path = root / PurePosixPath(location.path)
    if not path.is_file():
        raise RepoHealthError(f"configured path does not exist: {location.path}")
    try:
        return sorted(extractor(root, location.path, location.selector))
    except (OSError, SyntaxError) as error:
        raise RepoHealthError(
            f"failed to extract {location.selector} from {location.path}: {error}"
        ) from error


def _inventory_python_file(root: Path, path: str, scan: Scan) -> None:
    name = PurePosixPath(path).name
    if name == "pyproject.toml":
        scan.coordinates.append(
            _coordinate("python-manifest", path, "file", "pyproject")
        )
        try:
            config = tomllib.loads((root / path).read_text(encoding="utf-8"))
            workspace = config.get("tool", {}).get("uv", {}).get("workspace", {})
            members = workspace.get("members", [])
            if isinstance(members, list):
                scan.coordinates.extend(
                    _coordinate(
                        "python-workspace-member", path, "tool.uv.workspace", item
                    )
                    for item in members
                    if isinstance(item, str)
                )
        except (OSError, tomllib.TOMLDecodeError) as error:
            scan.extraction_failures.append({"path": path, "reason": str(error)})
        scan.recognized.add(path)
    elif name == "uv.lock":
        scan.coordinates.append(_coordinate("python-lock", path, "file", "uv"))
        scan.recognized.add(path)
    elif "requirements" in name and name.endswith(".txt"):
        scan.coordinates.append(_coordinate("python-requirements", path, "file", name))
        scan.recognized.add(path)


def _inventory_workflow(root: Path, path: str, scan: Scan) -> None:
    text = (root / path).read_text(encoding="utf-8")
    kind = "workflow-lock" if path.endswith(".lock.yml") else "workflow-source"
    scan.coordinates.append(_coordinate(kind, path, "file", PurePosixPath(path).name))
    scan.coordinates.extend(
        _coordinate(
            "workflow-action",
            path,
            f"line:{_line_number(text, match.start())}:uses",
            match.group(1),
        )
        for match in _WORKFLOW_USES.finditer(text)
    )
    scan.coordinates.extend(
        _coordinate(
            "version-constant",
            path,
            f"line:{_line_number(text, match.start())}:kubernetes-version",
            match.group("value"),
        )
        for match in _WORKFLOW_KUBERNETES_VERSION.finditer(text)
    )
    tool_names = {
        "LEFTHOOK_VERSION": "lefthook",
        "KUBECONFORM_VERSION": "kubeconform",
        "HELM_VERSION": "helm",
    }
    scan.coordinates.extend(
        _coordinate(
            "tool-version",
            path,
            f"line:{_line_number(text, match.start())}:tool:{tool_names[match.group('name')]}",
            match.group("value"),
        )
        for match in _WORKFLOW_TOOL_VERSION.finditer(text)
    )
    scan.coordinates.extend(
        _coordinate(
            "tool-version",
            path,
            f"line:{_line_number(text, match.start())}:tool:actionlint",
            match.group("value"),
        )
        for match in _ACTIONLINT_IMAGE.finditer(text)
    )
    if path.endswith(".lock.yml"):
        try:
            scan.coordinates.extend(
                extract_gh_aw_compiler_version(root, path, "gh-aw-compiler-version")
            )
        except RepoHealthError as error:
            scan.extraction_failures.append({"path": path, "reason": str(error)})
    if path == ".github/workflows/copilot-setup-steps.yml":
        scan.coordinates.extend(
            extract_gh_aw_setup_version(root, path, "gh-aw-setup-version")
        )
    scan.recognized.add(path)


def _inventory_dockerfile(root: Path, path: str, scan: Scan) -> None:
    scan.coordinates.extend(extract_docker_from(root, path, "docker-from"))
    scan.recognized.add(path)


def _inventory_bicep(root: Path, path: str, scan: Scan) -> None:
    text = (root / path).read_text(encoding="utf-8")
    scan.coordinates.extend(
        _coordinate(
            "bicep-resource-api",
            path,
            f"line:{_line_number(text, match.start())}:resource:{match.group('type')}",
            match.group("version"),
        )
        for match in _BICEP_RESOURCE.finditer(text)
    )
    scan.coordinates.extend(
        _coordinate(
            "version-constant",
            path,
            f"line:{_line_number(text, match.start())}:param:{match.group('name')}",
            match.group("value"),
        )
        for match in _BICEP_PARAMETER.finditer(text)
        if match.group("name").lower().endswith("version")
    )
    scan.recognized.add(path)


def _inventory_python_constants(root: Path, path: str, scan: Scan) -> None:
    text = (root / path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as error:
        scan.extraction_failures.append({"path": path, "reason": str(error)})
        return
    coordinate_count = len(scan.coordinates)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if target.id.endswith("VERSION"):
            scan.coordinates.append(
                _coordinate(
                    "version-constant",
                    path,
                    f"line:{node.lineno}:constant:{target.id}",
                    node.value.value,
                )
            )
        tool_name = {
            "ACTIONLINT_IMAGE": "actionlint",
            "KUBECONFORM_IMAGE": "kubeconform",
            "HELM_VERSION": "helm",
        }.get(target.id)
        if tool_name is not None:
            version = node.value.value
            if target.id != "HELM_VERSION":
                _, separator, version = version.rpartition(":")
                if not separator:
                    continue
            scan.coordinates.append(
                _coordinate(
                    "tool-version",
                    path,
                    f"line:{node.lineno}:tool:{tool_name}",
                    version,
                )
            )
    if len(scan.coordinates) > coordinate_count:
        scan.recognized.add(path)


def _yaml_documents(text: str) -> list[str]:
    documents: list[list[str]] = [[]]
    block_scalar_indent: int | None = None
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        indentation = len(content) - len(content.lstrip(" \t"))
        if block_scalar_indent is not None:
            if not content.strip() or indentation > block_scalar_indent:
                documents[-1].append(line)
                continue
            block_scalar_indent = None
        if _YAML_DOCUMENT_SEPARATOR.fullmatch(content):
            documents.append([])
            continue
        documents[-1].append(line)
        if _YAML_BLOCK_SCALAR.search(content.strip()):
            block_scalar_indent = indentation
    rendered = ["".join(document) for document in documents]
    if rendered and all(
        not line.strip() or line.lstrip().startswith("#")
        for line in rendered[0].splitlines()
    ):
        rendered = rendered[1:]
    return rendered


def _inventory_yaml(
    root: Path,
    path: str,
    scan: Scan,
    kubernetes_schema_excluded_kinds: set[str],
) -> None:
    text = (root / path).read_text(encoding="utf-8")
    if path == "azure.yaml":
        scan.coordinates.extend(
            _coordinate(
                "tool-version",
                path,
                f"line:{_line_number(text, match.start())}:tool:azd",
                match.group("value"),
            )
            for match in _AZD_REQUIRED_VERSION.finditer(text)
        )
    charts = list(_HELM_CHART.finditer(text))
    for index, chart in enumerate(charts):
        scan.coordinates.append(
            _coordinate(
                "helm-chart",
                path,
                f"line:{_line_number(text, chart.start())}:chart",
                chart.group("value"),
            )
        )
        end = charts[index + 1].start() if index + 1 < len(charts) else len(text)
        version = _HELM_VERSION.search(text, chart.end(), end)
        if version is not None:
            scan.coordinates.append(
                _coordinate(
                    "helm-version",
                    path,
                    f"line:{_line_number(text, version.start())}:version",
                    version.group("value"),
                )
            )
        values = _HELM_VALUES.search(text, chart.end(), end)
        if values is not None:
            scan.coordinates.append(
                _coordinate(
                    "helm-values-reference",
                    path,
                    f"line:{_line_number(text, values.start())}:values",
                    values.group("value"),
                )
            )
    if charts:
        scan.recognized.add(path)
    if path.startswith("k8s/"):
        documents = _yaml_documents(text)
        for document_index, document in enumerate(documents, start=1):
            api_version = _YAML_API_VERSION.search(document)
            kind = _YAML_KIND.search(document)
            if api_version is None or kind is None:
                continue
            category = (
                "kustomize"
                if kind.group("value") == "Kustomization"
                or PurePosixPath(path).name.startswith("kustomization.")
                else "kubernetes-manifest"
            )
            scan.coordinates.append(
                _coordinate(
                    category,
                    path,
                    f"document:{document_index}",
                    f"{api_version.group('value')}/{kind.group('value')}",
                )
            )
            if kind.group("value") in kubernetes_schema_excluded_kinds:
                scan.coordinates.append(
                    _coordinate(
                        "kubernetes-schema-exclusion",
                        path,
                        f"document:{document_index}",
                        f"{api_version.group('value')}/{kind.group('value')}",
                        "excluded",
                    )
                )
            scan.recognized.add(path)


def _markdown_content_lines(text: str) -> list[str]:
    content_lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    html_comment = False
    indented_code = False
    previous_line_blank = True
    for line in text.splitlines():
        marker_match = _MARKDOWN_FENCE.match(line)
        if fence_character is not None:
            if marker_match is not None:
                marker = marker_match.group("marker")
                if marker[0] == fence_character and len(marker) >= fence_length:
                    fence_character = None
                    fence_length = 0
            content_lines.append("")
            continue
        if marker_match is not None:
            marker = marker_match.group("marker")
            fence_character = marker[0]
            fence_length = len(marker)
            content_lines.append("")
            continue
        if indented_code:
            if not line.strip() or line.startswith(("    ", "\t")):
                content_lines.append("")
                previous_line_blank = not line.strip()
                continue
            indented_code = False
        if previous_line_blank and line.startswith(("    ", "\t")):
            indented_code = True
            content_lines.append("")
            previous_line_blank = False
            continue
        visible_parts: list[str] = []
        remaining = line
        while remaining:
            if html_comment:
                closing = remaining.find("-->")
                if closing < 0:
                    remaining = ""
                    break
                remaining = remaining[closing + 3 :]
                html_comment = False
                continue
            opening = remaining.find("<!--")
            if opening < 0:
                visible_parts.append(remaining)
                break
            visible_parts.append(remaining[:opening])
            remaining = remaining[opening + 4 :]
            html_comment = True
        visible_line = "".join(visible_parts)
        content_lines.append(_MARKDOWN_INLINE_CODE.sub("", visible_line))
        previous_line_blank = not line.strip()
    return content_lines


def _markdown_target_text(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<"):
        closing = target.find(">")
        if closing < 0:
            return None
        target = target[1:closing]
    else:
        target = target.split(maxsplit=1)[0]
    return unquote(target) or None


def _markdown_target(raw_target: str) -> str | None:
    target = _markdown_target_text(raw_target)
    if (
        not target
        or target.startswith(("#", "/"))
        or _URI_SCHEME.match(target) is not None
    ):
        return None
    return re.split(r"[?#]", target, maxsplit=1)[0] or None


def _markdown_external_target(raw_target: str) -> tuple[str | None, bool]:
    target = _markdown_target_text(raw_target)
    if target is None:
        return None, False
    parsed = urlsplit(target)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None, False
    if parsed.username is not None or parsed.password is not None:
        return None, True
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            "",
            "",
        )
    ), False


def _tracked_markdown_target(
    source_path: str,
    raw_target: str,
    tracked_files: set[str],
) -> str | None:
    target = _markdown_target(raw_target)
    if target is None:
        return None
    source_directory = PurePosixPath(source_path).parent.as_posix()
    normalized = posixpath.normpath(posixpath.join(source_directory, target))
    if normalized == ".." or normalized.startswith("../"):
        return None
    if normalized == "." and tracked_files:
        return normalized
    if normalized in tracked_files:
        return normalized
    directory_prefix = normalized.rstrip("/") + "/"
    if any(path.startswith(directory_prefix) for path in tracked_files):
        return normalized
    return ""


def _inventory_markdown_document(
    root: Path,
    path: str,
    scan: Scan,
    tracked_files: set[str],
) -> None:
    text = (root / PurePosixPath(path)).read_text(encoding="utf-8")
    scan.coordinates.append(
        _coordinate("documentation-source", path, "file", PurePosixPath(path).name)
    )
    scan.recognized.add(path)
    for line_number, line in enumerate(_markdown_content_lines(text), start=1):
        matches = list(_MARKDOWN_INLINE_LINK.finditer(line))
        reference_match = _MARKDOWN_REFERENCE_LINK.match(line)
        if reference_match is not None:
            matches.append(reference_match)
        for match in matches:
            raw_target = match.group("target")
            external_target, contains_credentials = _markdown_external_target(
                raw_target
            )
            if contains_credentials:
                scan.validation_issues.append(
                    ValidationIssue(
                        path,
                        f"line:{line_number}:link",
                        "public Markdown link must not contain URL credentials",
                    )
                )
                continue
            if external_target is not None:
                scan.coordinates.append(
                    _coordinate(
                        "documentation-external-link",
                        path,
                        f"line:{line_number}:link",
                        external_target,
                    )
                )
                continue
            normalized = _tracked_markdown_target(path, raw_target, tracked_files)
            if normalized is None:
                continue
            location = f"line:{line_number}:link"
            scan.coordinates.append(
                _coordinate(
                    "documentation-link",
                    path,
                    location,
                    normalized or raw_target,
                )
            )
            if not normalized:
                scan.validation_issues.append(
                    ValidationIssue(
                        path,
                        location,
                        "relative Markdown link target is not a tracked repository "
                        f"path: {raw_target}",
                    )
                )

    if _ADR_DOCUMENT.fullmatch(path) is None:
        return
    lines = text.splitlines()
    heading_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _ADR_STATUS_HEADING.fullmatch(line)
        ),
        None,
    )
    legacy_status = next(
        (
            (line_number, line)
            for line_number, line in enumerate(
                lines if heading_index is None else lines[:heading_index],
                start=1,
            )
            if _ADR_LEGACY_STATUS.match(line)
        ),
        None,
    )
    if legacy_status is not None:
        scan.validation_issues.append(
            ValidationIssue(
                path,
                f"line:{legacy_status[0]}:status",
                "ADR uses legacy '- Status:' metadata; use a '## Status' section",
            )
        )
        return
    if heading_index is None:
        scan.validation_issues.append(
            ValidationIssue(
                path,
                "document:status",
                "ADR is missing the required '## Status' section",
            )
        )
        return
    status_line = next(
        (
            (index, line.strip())
            for index, line in enumerate(lines[heading_index + 1 :], heading_index + 1)
            if line.strip()
        ),
        None,
    )
    if status_line is None or _ADR_STATUS_VALUE.match(status_line[1]) is None:
        location = (
            "document:status"
            if status_line is None
            else f"line:{status_line[0] + 1}:status"
        )
        scan.validation_issues.append(
            ValidationIssue(
                path,
                location,
                "ADR Status must start with Accepted, Rejected, Deprecated, "
                "or Superseded by",
            )
        )


def _inventory_repository_sources(
    root: Path,
    path: str,
    scan: Scan,
    tracked_files: set[str],
) -> None:
    source_category: str | None = None
    if path.startswith(".github/agents/") and path.endswith(".agent.md"):
        source_category = "agent-source"
    elif path.startswith(".github/skills/"):
        source_category = "skill-source"
    elif path.startswith(".github/hooks/"):
        source_category = "hook-source"
    elif path == ".github/aw/actions-lock.json":
        source_category = "workflow-lock"
    elif path == ".github/repo-health.toml":
        source_category = "repository-health-config"
    if source_category is not None:
        scan.coordinates.append(
            _coordinate(source_category, path, "file", PurePosixPath(path).name)
        )
        scan.recognized.add(path)

    if path.endswith(".md"):
        _inventory_markdown_document(root, path, scan, tracked_files)
        text = (root / PurePosixPath(path)).read_text(encoding="utf-8")
        scan.coordinates.extend(
            _coordinate(
                "documentation-reference",
                path,
                f"line:{_line_number(text, match.start())}:reference",
                match.group(0),
            )
            for match in _DOC_REFERENCE.finditer(text)
        )


def _inventory_json(root: Path, path: str, scan: Scan) -> None:
    if path == "src/external-sli-publisher/host.json":
        document = json.loads((root / PurePosixPath(path)).read_text(encoding="utf-8"))
        extension_bundle = document.get("extensionBundle")
        version = (
            extension_bundle.get("version")
            if isinstance(extension_bundle, dict)
            else None
        )
        if not isinstance(version, str) or not version.strip():
            scan.validation_issues.append(
                ValidationIssue(
                    path,
                    "extensionBundle.version",
                    "Functions host configuration must define extensionBundle.version",
                )
            )
        else:
            scan.coordinates.append(
                _coordinate(
                    "function-extension-bundle",
                    path,
                    "extensionBundle.version",
                    version,
                )
            )
        scan.recognized.add(path)
        return

    entrypoint = BICEP_PARAMETER_ENTRYPOINTS.get(path)
    if entrypoint is None:
        return
    document = json.loads((root / PurePosixPath(path)).read_text(encoding="utf-8"))
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        scan.validation_issues.append(
            ValidationIssue(
                path,
                "parameters",
                "Bicep parameter file must contain a parameters object",
            )
        )
        scan.recognized.add(path)
        return
    declaration_text = (root / PurePosixPath(entrypoint)).read_text(encoding="utf-8")
    declarations = {
        match.group("name"): match.group("default") is not None
        for match in _BICEP_PARAMETER_DECLARATION.finditer(declaration_text)
    }
    parameter_names = {name for name in parameters if isinstance(name, str)}
    for name in sorted(parameter_names):
        scan.coordinates.append(
            _coordinate(
                "bicep-parameter",
                path,
                f"parameters:{name}",
                name,
            )
        )
        value = parameters[name]
        if not isinstance(value, dict) or "value" not in value:
            scan.validation_issues.append(
                ValidationIssue(
                    path,
                    f"parameters:{name}",
                    "Bicep parameter entry must be an object containing value",
                )
            )
    for name in sorted(parameter_names - declarations.keys()):
        scan.validation_issues.append(
            ValidationIssue(
                path,
                f"parameters:{name}",
                f"parameter is not declared by {entrypoint}: {name}",
            )
        )
    required = {name for name, has_default in declarations.items() if not has_default}
    for name in sorted(required - parameter_names):
        scan.validation_issues.append(
            ValidationIssue(
                entrypoint,
                f"param:{name}",
                f"required parameter is missing from {path}: {name}",
            )
        )
    scan.recognized.add(path)


def validate_bicep_parameter_files(root: Path) -> list[ValidationIssue]:
    scan = Scan([], set(), [], [])
    for path in BICEP_PARAMETER_ENTRYPOINTS:
        parameter_path = root / PurePosixPath(path)
        if not parameter_path.is_file():
            scan.validation_issues.append(
                ValidationIssue(path, "file", "Bicep parameter file does not exist")
            )
            continue
        try:
            _inventory_json(root, path, scan)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            scan.validation_issues.append(
                ValidationIssue(path, "file", f"parameter validation failed: {error}")
            )
    return sorted(set(scan.validation_issues))


def _inventory_helm_values(path: str, scan: Scan) -> None:
    if path != "infra/helm/chaos-mesh-values.yaml":
        return
    scan.coordinates.append(
        _coordinate("helm-values", path, "file", "chaos-mesh/chaos-mesh")
    )
    scan.recognized.add(path)


def scan_inventory(root: Path, tracked_files: Sequence[str]) -> Scan:
    tracked_file_set = set(tracked_files)
    kubernetes_schema_excluded_kinds = set(
        load_kubernetes_schema_excluded_kinds(root / CONFIG_PATH)
    )
    scan = Scan([], set(), [], [])
    for path in tracked_files:
        file_scan = Scan([], set(), [], [])
        try:
            if not (root / PurePosixPath(path)).is_file():
                raise FileNotFoundError(f"tracked file does not exist: {path}")
            _inventory_python_file(root, path, file_scan)
            if path.startswith(".github/workflows/") and path.endswith(
                (".yml", ".yaml")
            ):
                _inventory_workflow(root, path, file_scan)
            name = PurePosixPath(path).name
            if name == "Dockerfile" or name.endswith(".Dockerfile"):
                _inventory_dockerfile(root, path, file_scan)
            if path.endswith(".bicep"):
                _inventory_bicep(root, path, file_scan)
            if path.endswith(".py"):
                _inventory_python_constants(root, path, file_scan)
            if path.endswith((".yml", ".yaml")):
                _inventory_yaml(
                    root,
                    path,
                    file_scan,
                    kubernetes_schema_excluded_kinds,
                )
                _inventory_helm_values(path, file_scan)
            if path.endswith(".json"):
                _inventory_json(root, path, file_scan)
            _inventory_repository_sources(root, path, file_scan, tracked_file_set)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            RepoHealthError,
            SyntaxError,
        ) as error:
            scan.extraction_failures.append({"path": path, "reason": str(error)})
            continue
        scan.coordinates.extend(file_scan.coordinates)
        scan.recognized.update(file_scan.recognized)
        scan.extraction_failures.extend(file_scan.extraction_failures)
        scan.validation_issues.extend(file_scan.validation_issues)
    scan.coordinates = sorted(set(scan.coordinates))
    scan.extraction_failures.sort(key=lambda item: (item["path"], item["reason"]))
    scan.validation_issues = sorted(set(scan.validation_issues))
    return scan


def _file_coverage_entry(
    path: str,
    owner: str,
    reason: str,
) -> dict[str, str]:
    return {"path": path, "owner": owner, "reason": reason}


def classify_file_coverage(
    tracked_files: Sequence[str],
    recognized_files: set[str],
) -> dict[str, object]:
    covered: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    gaps: list[dict[str, str]] = []
    explicit_covered = {
        ".github/actionlint.yaml": (
            "lint-workflows",
            "actionlint loads this configuration while validating workflows",
        ),
        ".github/dependabot.yml": (
            "repository-automation-contract",
            "repository automation tests validate the Dependabot configuration",
        ),
        ".vscode/tasks.json": (
            "repository-automation-contract",
            "repository automation tests validate referenced task targets",
        ),
        "infra/abbreviations.json": (
            "build-bicep",
            "the Bicep entrypoint loads this resource abbreviation data",
        ),
        "lefthook.yml": (
            "test-hooks",
            "hook contract tests validate the Lefthook configuration",
        ),
    }
    explicit_excluded = {
        ".dockerignore": "container build exclusion patterns are data, not a freshness coordinate",
        ".gitattributes": "Git attribute patterns are data, not a freshness coordinate",
        ".gitignore": "Git ignore patterns are data, not a freshness coordinate",
        "LICENSE": "license text is outside repository health automation",
        "docs/features/.gitkeep": "placeholder file has no executable or document content",
        "src/external-sli-publisher/.funcignore": (
            "Functions package exclusion patterns are deployment data"
        ),
        "this.code-workspace": "editor workspace layout is developer-local configuration",
    }
    for path in sorted(set(tracked_files) - recognized_files):
        covered_rule = explicit_covered.get(path)
        if covered_rule is not None:
            covered.append(_file_coverage_entry(path, *covered_rule))
        elif path.startswith("infra/modules/templates/") and path.endswith(".kql"):
            covered.append(
                _file_coverage_entry(
                    path,
                    "build-bicep",
                    "the Bicep entrypoint loads this query template",
                )
            )
        elif path.startswith(("scripts/", "src/")) and path.endswith(".py"):
            covered.append(
                _file_coverage_entry(
                    path,
                    "qa-app",
                    "workspace lint, type checking, and tests cover Python sources",
                )
            )
        elif path in explicit_excluded:
            excluded.append(
                _file_coverage_entry(
                    path,
                    "repository-policy",
                    explicit_excluded[path],
                )
            )
        else:
            gaps.append(
                _file_coverage_entry(
                    path,
                    "unassigned",
                    "no deterministic repository health or specialized check is assigned",
                )
            )
    return {
        "count": len(covered) + len(excluded) + len(gaps),
        "covered_by_other_check": covered,
        "intentionally_excluded": excluded,
        "true_gap": gaps,
    }


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepoHealthError(f"{field} must be a non-empty string")
    return value


def _required_string_array(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RepoHealthError(f"{field} must be a non-empty array")
    items = tuple(
        _required_string(item, f"{field}[{index}]") for index, item in enumerate(value)
    )
    return tuple(sorted(items))


def _reject_unknown_keys(value: dict[Any, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(str(key) for key in set(value) - allowed)
    if unknown:
        raise RepoHealthError(f"{field} contains unknown keys: {', '.join(unknown)}")


def _parse_location(value: Any, field: str) -> Location:
    if not isinstance(value, dict):
        raise RepoHealthError(f"{field} must be a table")
    _reject_unknown_keys(value, {"path", "selector"}, field)
    return Location(
        _required_string(value.get("path"), f"{field}.path"),
        _required_string(value.get("selector"), f"{field}.selector"),
    )


def _parse_exception(value: Any, field: str) -> ExceptionPolicy:
    if not isinstance(value, dict):
        raise RepoHealthError(f"{field} must be a table")
    _reject_unknown_keys(
        value,
        {
            "reason",
            "owner",
            "tracking",
            "review_by",
            "resolution",
            "canonical_values",
            "target_fingerprint",
        },
        field,
    )
    review_by = value.get("review_by")
    if type(review_by) is not date:
        raise RepoHealthError(f"{field}.review_by must be a TOML local date")
    return ExceptionPolicy(
        reason=_required_string(value.get("reason"), f"{field}.reason"),
        owner=_required_string(value.get("owner"), f"{field}.owner"),
        tracking=_required_string(value.get("tracking"), f"{field}.tracking"),
        review_by=cast(date, review_by),
        resolution=_required_string(value.get("resolution"), f"{field}.resolution"),
        canonical_values=_required_string_array(
            value.get("canonical_values"), f"{field}.canonical_values"
        ),
        target_fingerprint=_parse_target_fingerprint(
            value.get("target_fingerprint"), f"{field}.target_fingerprint"
        ),
    )


def _parse_target_fingerprint(value: Any, field: str) -> tuple[TargetFingerprint, ...]:
    if not isinstance(value, list) or not value:
        raise RepoHealthError(f"{field} must be a non-empty array")
    items: list[TargetFingerprint] = []
    for index, raw_item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(raw_item, dict):
            raise RepoHealthError(f"{item_field} must be a table")
        item = cast(dict[str, Any], raw_item)
        _reject_unknown_keys(item, {"path", "selector", "value"}, item_field)
        items.append(
            TargetFingerprint(
                path=_required_string(item.get("path"), f"{item_field}.path"),
                selector=_required_string(
                    item.get("selector"), f"{item_field}.selector"
                ),
                value=_required_string(item.get("value"), f"{item_field}.value"),
            )
        )
    return tuple(sorted(items))


def _load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RepoHealthError(
            f"could not load configuration {path}: {error}"
        ) from error
    _reject_unknown_keys(
        config,
        {"schema_version", "kubernetes_schema_excluded_kinds", "rules"},
        "configuration",
    )
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise RepoHealthError(
            f"configuration schema_version must be {CONFIG_SCHEMA_VERSION}"
        )
    return config


def load_kubernetes_schema_excluded_kinds(path: Path) -> tuple[str, ...]:
    config = _load_config(path)
    raw_kinds = config.get("kubernetes_schema_excluded_kinds", [])
    if not isinstance(raw_kinds, list):
        raise RepoHealthError(
            "configuration kubernetes_schema_excluded_kinds must be an array"
        )
    kinds = tuple(
        _required_string(
            kind,
            f"configuration kubernetes_schema_excluded_kinds[{index}]",
        )
        for index, kind in enumerate(raw_kinds)
    )
    if len(set(kinds)) != len(kinds):
        raise RepoHealthError(
            "configuration kubernetes_schema_excluded_kinds contains duplicates"
        )
    return tuple(sorted(kinds))


def load_rules(path: Path) -> list[Rule]:
    config = _load_config(path)
    raw_rules = config.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RepoHealthError("configuration rules must be a non-empty array")
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, raw_rule_value in enumerate(raw_rules):
        field = f"rules[{index}]"
        if not isinstance(raw_rule_value, dict):
            raise RepoHealthError(f"{field} must be a table")
        raw_rule = cast(dict[str, Any], raw_rule_value)
        _reject_unknown_keys(
            raw_rule,
            {"id", "canonical", "targets", "enforce", "exception"},
            field,
        )
        rule_id = _required_string(raw_rule.get("id"), f"{field}.id")
        if rule_id not in KNOWN_RULE_IDS:
            raise RepoHealthError(f"{field}.id is not a built-in rule ID: {rule_id}")
        if rule_id in seen:
            raise RepoHealthError(f"duplicate rule ID: {rule_id}")
        seen.add(rule_id)
        enforce = raw_rule.get("enforce")
        if not isinstance(enforce, bool):
            raise RepoHealthError(f"{field}.enforce must be a boolean")
        raw_targets = raw_rule.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise RepoHealthError(f"{field}.targets must be a non-empty array")
        exception = (
            _parse_exception(raw_rule.get("exception"), f"{field}.exception")
            if "exception" in raw_rule
            else None
        )
        if not enforce and exception is None:
            raise RepoHealthError(f"{field}.exception is required when enforce=false")
        if enforce and exception is not None:
            raise RepoHealthError(f"{field}.exception is forbidden when enforce=true")
        rules.append(
            Rule(
                id=rule_id,
                canonical=_parse_location(
                    raw_rule.get("canonical"), f"{field}.canonical"
                ),
                targets=tuple(
                    _parse_location(target, f"{field}.targets[{target_index}]")
                    for target_index, target in enumerate(raw_targets)
                ),
                enforce=enforce,
                exception=exception,
            )
        )
    return sorted(rules, key=lambda rule: rule.id)


def _exception_dict(policy: ExceptionPolicy) -> dict[str, object]:
    return {
        "reason": policy.reason,
        "owner": policy.owner,
        "tracking": policy.tracking,
        "review_by": policy.review_by.isoformat(),
        "resolution": policy.resolution,
        "canonical_values": list(policy.canonical_values),
        "target_fingerprint": [asdict(item) for item in policy.target_fingerprint],
    }


def _coordinate_values(coordinates: Sequence[Coordinate]) -> tuple[str, ...]:
    return tuple(sorted(item.value for item in coordinates))


def _target_fingerprint(
    target_groups: Sequence[tuple[Location, Sequence[Coordinate]]],
) -> tuple[TargetFingerprint, ...]:
    return tuple(
        sorted(
            TargetFingerprint(
                path=target.path,
                selector=target.selector,
                value=coordinate.value,
            )
            for target, coordinates in target_groups
            for coordinate in coordinates
        )
    )


def _versions_match(
    canonical: Sequence[Coordinate], targets: Sequence[Coordinate]
) -> bool:
    canonical_values = {item.value.removesuffix(".0") for item in canonical}
    target_values = {item.value.removesuffix(".0") for item in targets}
    return len(canonical_values) == 1 and target_values == canonical_values


def _docker_images_pinned(
    canonical: Sequence[Coordinate], targets: Sequence[Coordinate]
) -> bool:
    coordinates = sorted(set(canonical) | set(targets))
    return bool(coordinates) and all("@sha256:" in item.value for item in coordinates)


def run_checks(
    root: Path, rules: Sequence[Rule], as_of: date | None = None
) -> list[Check]:
    today = as_of or date.today()
    checks: list[Check] = []
    for rule in rules:
        canonical: tuple[Coordinate, ...] = ()
        target_groups_list: list[tuple[Location, tuple[Coordinate, ...]]] = []
        extraction_location = rule.canonical
        try:
            canonical = tuple(_extract_location(root, rule.canonical))
            for target in rule.targets:
                extraction_location = target
                target_groups_list.append(
                    (target, tuple(_extract_location(root, target)))
                )
        except RepoHealthError as error:
            extracted_targets = tuple(
                coordinate
                for _target, coordinates in target_groups_list
                for coordinate in coordinates
            )
            checks.append(
                Check(
                    rule.id,
                    "fail",
                    canonical,
                    extracted_targets,
                    str(error),
                    failure_location=extraction_location,
                )
            )
            continue
        target_groups = tuple(target_groups_list)
        targets = tuple(
            coordinate
            for _target, coordinates in target_groups
            for coordinate in coordinates
        )
        if not canonical:
            checks.append(
                Check(
                    rule.id,
                    "fail",
                    canonical,
                    targets,
                    "canonical extraction returned zero coordinates",
                    failure_location=rule.canonical,
                )
            )
            continue
        missing_target = next(
            (target for target, coordinates in target_groups if not coordinates),
            None,
        )
        if missing_target is not None:
            checks.append(
                Check(
                    rule.id,
                    "fail",
                    canonical,
                    targets,
                    "target extraction returned zero coordinates: "
                    f"{missing_target.path} ({missing_target.selector})",
                    failure_location=missing_target,
                )
            )
            continue
        policy = rule.exception
        if policy is not None and policy.review_by < today:
            checks.append(
                Check(
                    rule.id,
                    "fail",
                    canonical,
                    targets,
                    f"exception expired on {policy.review_by.isoformat()}",
                    _exception_dict(policy),
                )
            )
            continue
        matches = (
            _docker_images_pinned(canonical, targets)
            if rule.id == "docker-base-digest"
            else _versions_match(canonical, targets)
        )
        if matches and policy is not None:
            checks.append(
                Check(
                    rule.id,
                    "fail",
                    canonical,
                    targets,
                    "exception remains after the configured inconsistency was resolved",
                    _exception_dict(policy),
                )
            )
        elif matches:
            checks.append(Check(rule.id, "pass", canonical, targets, "rule satisfied"))
        elif policy is not None:
            if (
                _coordinate_values(canonical) != policy.canonical_values
                or _target_fingerprint(target_groups) != policy.target_fingerprint
            ):
                checks.append(
                    Check(
                        rule.id,
                        "fail",
                        canonical,
                        targets,
                        "current inconsistency differs from the exception snapshot",
                        _exception_dict(policy),
                    )
                )
            else:
                checks.append(
                    Check(
                        rule.id,
                        "excluded",
                        canonical,
                        targets,
                        "known inconsistency covered by a current exception",
                        _exception_dict(policy),
                    )
                )
        else:
            checks.append(
                Check(
                    rule.id,
                    "fail",
                    canonical,
                    targets,
                    "canonical and target coordinates do not satisfy the rule",
                )
            )
    return sorted(checks, key=lambda check: check.id)


def _check_findings(checks: Sequence[Check]) -> list[Finding]:
    findings: list[Finding] = []
    for check in checks:
        if check.status == "pass":
            continue
        coordinate = check.targets[0] if check.targets else None
        path = (
            check.failure_location.path
            if check.failure_location is not None
            else coordinate.path
            if coordinate is not None
            else ".github/repo-health.toml"
        )
        location = (
            check.failure_location.selector
            if check.failure_location is not None
            else coordinate.location
            if coordinate is not None
            else "configuration"
        )
        findings.append(
            Finding(
                check.id,
                check.status,
                path,
                location,
                check.message,
            )
        )
    return sorted(findings)


def build_result(root: Path, *, include_checks: bool) -> dict[str, Any]:
    tracked_files = list_tracked_files(root)
    untracked_files = list_untracked_files(root)
    repository_files = sorted(set(tracked_files) | set(untracked_files))
    commit = repository_commit(root)
    scan = scan_inventory(root, repository_files)
    checks: list[Check] = []
    if include_checks:
        rules = load_rules(root / CONFIG_PATH)
        checks = run_checks(root, rules)
    file_coverage = classify_file_coverage(repository_files, scan.recognized)
    findings = _check_findings(checks)
    findings.extend(
        Finding(
            "inventory-extraction",
            "fail" if include_checks else "unverified",
            failure["path"],
            "extraction",
            f"inventory extraction failed: {failure['reason']}",
        )
        for failure in scan.extraction_failures
    )
    findings.extend(
        Finding(
            "document-health",
            "fail" if include_checks else "unverified",
            issue.path,
            issue.location,
            issue.message,
        )
        for issue in scan.validation_issues
    )
    findings.extend(
        Finding(
            "file-coverage",
            "fail" if include_checks else "unverified",
            entry["path"],
            "file",
            entry["reason"],
        )
        for entry in cast(list[dict[str, str]], file_coverage["true_gap"])
    )
    findings.sort()
    return {
        "schema_version": SCHEMA_VERSION,
        "repository_root": str(root.resolve()),
        "repository_commit": commit,
        "inventory": [asdict(item) for item in scan.coordinates],
        "checks": [asdict(item) for item in checks],
        "coverage": {
            "tracked_files": len(tracked_files),
            "untracked_files": len(untracked_files),
            "repository_files": len(repository_files),
            "recognized_files": len(scan.recognized),
            "inventory_coordinates": len(scan.coordinates),
            "checked_coordinates": sum(
                len(check.canonical) + len(check.targets) for check in checks
            ),
            "excluded_coordinates": [
                {
                    "rule_id": check.id,
                    "reason": check.exception["reason"]
                    if check.exception
                    else check.message,
                }
                for check in checks
                if check.status == "excluded"
            ]
            + [
                {
                    "rule_id": coordinate.category,
                    "path": coordinate.path,
                    "location": coordinate.location,
                    "reason": "schema validation is explicitly excluded by repository configuration",
                }
                for coordinate in scan.coordinates
                if coordinate.status == "excluded"
            ],
            "validation_issues": [asdict(issue) for issue in scan.validation_issues],
            "file_coverage": file_coverage,
            "extraction_failures": scan.extraction_failures,
        },
        "findings": [asdict(item) for item in findings],
        "environment_limitations": [
            {
                "area": "external-freshness",
                "status": "excluded",
                "reason": "This offline scanner does not query networks or authenticated environments.",
            },
            {
                "area": "public-link-reachability",
                "status": "unverified",
                "reason": "Public Markdown links require the network-enabled freshness check.",
            },
        ],
    }


def json_output(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _validate_report(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RepoHealthError("report input must contain a JSON object")
    required = {
        "schema_version",
        "repository_root",
        "inventory",
        "checks",
        "coverage",
        "findings",
        "environment_limitations",
    }
    missing = sorted(required - result.keys())
    if missing:
        raise RepoHealthError(f"report input is missing keys: {', '.join(missing)}")
    if result.get("schema_version") != SCHEMA_VERSION:
        raise RepoHealthError(f"report input schema_version must be {SCHEMA_VERSION}")
    if not isinstance(result.get("repository_root"), str):
        raise RepoHealthError("report input repository_root must be a string")
    coverage = result.get("coverage")
    if not isinstance(coverage, dict):
        raise RepoHealthError("report input coverage must be an object")
    for field in (
        "tracked_files",
        "untracked_files",
        "repository_files",
        "recognized_files",
    ):
        value = coverage.get(field)
        if type(value) is not int or value < 0:
            raise RepoHealthError(
                f"report input coverage.{field} must be a non-negative integer"
            )
    file_coverage = coverage.get("file_coverage")
    if not isinstance(file_coverage, dict):
        raise RepoHealthError("report input coverage.file_coverage must be an object")
    allowed_file_coverage_keys = {
        "count",
        "covered_by_other_check",
        "intentionally_excluded",
        "true_gap",
    }
    unknown_file_coverage_keys = sorted(set(file_coverage) - allowed_file_coverage_keys)
    if unknown_file_coverage_keys:
        raise RepoHealthError(
            "report input coverage.file_coverage contains unknown keys: "
            + ", ".join(unknown_file_coverage_keys)
        )
    count = file_coverage.get("count")
    if type(count) is not int or count < 0:
        raise RepoHealthError(
            "report input coverage.file_coverage.count must be a non-negative integer"
        )
    classified_count = 0
    for category in (
        "covered_by_other_check",
        "intentionally_excluded",
        "true_gap",
    ):
        entries = _validate_report_items(
            file_coverage.get(category),
            f"coverage.file_coverage.{category}",
            {"path": str, "owner": str, "reason": str},
        )
        classified_count += len(entries)
    if classified_count != count:
        raise RepoHealthError(
            "report input coverage.file_coverage.count does not match its categories"
        )
    _validate_report_items(
        result.get("inventory"),
        "inventory",
        {
            "category": str,
            "path": str,
            "location": str,
            "value": str,
            "status": str,
        },
        status_field="status",
    )
    checks = _validate_report_items(
        result.get("checks"),
        "checks",
        {"id": str, "status": str, "message": str},
        status_field="status",
    )
    for index, check in enumerate(checks):
        for field in ("canonical", "targets"):
            if field in check:
                _validate_report_items(
                    check[field],
                    f"checks[{index}].{field}",
                    {
                        "category": str,
                        "path": str,
                        "location": str,
                        "value": str,
                        "status": str,
                    },
                    status_field="status",
                )
        if (
            "exception" in check
            and check["exception"] is not None
            and not isinstance(check["exception"], dict)
        ):
            raise RepoHealthError(
                f"report input checks[{index}].exception must be an object or null"
            )
    _validate_report_items(
        result.get("findings"),
        "findings",
        {
            "rule_id": str,
            "status": str,
            "path": str,
            "location": str,
            "message": str,
        },
        status_field="status",
    )
    _validate_report_items(
        result.get("environment_limitations"),
        "environment_limitations",
        {"area": str, "status": str, "reason": str},
        status_field="status",
    )
    return result


def _validate_report_items(
    value: Any,
    section: str,
    fields: dict[str, type],
    *,
    status_field: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RepoHealthError(f"report input {section} must be an array")
    items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise RepoHealthError(f"report input {section}[{index}] must be an object")
        item = cast(dict[str, Any], raw_item)
        for field, expected_type in fields.items():
            field_value = item.get(field)
            if not isinstance(field_value, expected_type):
                raise RepoHealthError(
                    f"report input {section}[{index}].{field} must be a "
                    f"{expected_type.__name__}"
                )
        if status_field is not None and item[status_field] not in VALID_STATUSES:
            raise RepoHealthError(
                f"report input contains invalid status: {item[status_field]}"
            )
        items.append(item)
    return items


def text_output(result: dict[str, Any]) -> str:
    inventory = result["inventory"]
    checks = result["checks"]
    coverage = result["coverage"]
    findings = result["findings"]
    lines = [
        f"-> Repository health schema {result['schema_version']}",
        f"-> Repository: {result['repository_root']}",
        (
            "ok: inventory "
            f"{len(inventory)} coordinates from {coverage['recognized_files']} "
            f"of {coverage['repository_files']} repository files "
            f"({coverage['tracked_files']} tracked, "
            f"{coverage['untracked_files']} untracked)"
        ),
    ]
    file_coverage = coverage["file_coverage"]
    lines.append(
        "-> File coverage: "
        f"{len(file_coverage['covered_by_other_check'])} covered by other checks, "
        f"{len(file_coverage['intentionally_excluded'])} intentionally excluded, "
        f"{len(file_coverage['true_gap'])} true gaps"
    )
    for check in checks:
        prefix = {
            "pass": "ok:",
            "fail": "error:",
            "unverified": "->",
            "excluded": "->",
        }[check["status"]]
        lines.append(f"{prefix} [{check['status']}] {check['id']}: {check['message']}")
    lines.append(f"-> Findings: {len(findings)}")
    for finding in findings:
        prefix = "error:" if finding["status"] == "fail" else "->"
        lines.append(
            f"{prefix} [{finding['status']}] {finding['rule_id']} "
            f"{finding['path']} ({finding['message']})"
        )
    limitations = result["environment_limitations"]
    for limitation in limitations:
        lines.append(
            f"-> [{limitation['status']}] {limitation['area']}: {limitation['reason']}"
        )
    return "\n".join(lines) + "\n"


def _write_result(result: dict[str, Any], output_format: str) -> None:
    output = json_output(result) if output_format == "json" else text_output(result)
    sys.stdout.write(output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory and check repository health"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inventory", "check"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "--format", choices=("json", "text"), default="text"
        )
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("input", type=Path)
    subparsers.add_parser("validate-bicep-parameters")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "report":
            try:
                content = args.input.read_text(encoding="utf-8")
                result = _validate_report(json.loads(content))
            except (OSError, json.JSONDecodeError) as error:
                raise RepoHealthError(
                    f"could not read report input: {error}"
                ) from error
            _write_result(result, "text")
            return 0
        root = args.root.resolve()
        if args.command == "validate-bicep-parameters":
            issues = validate_bicep_parameter_files(root)
            for issue in issues:
                print(
                    f"error: {issue.path} ({issue.location}): {issue.message}",
                    file=sys.stderr,
                )
            return 1 if issues else 0
        result = build_result(root, include_checks=args.command == "check")
        _write_result(result, args.format)
        if args.command == "check" and (
            any(check["status"] == "fail" for check in result["checks"])
            or any(finding["status"] == "fail" for finding in result["findings"])
        ):
            return 1
        return 0
    except RepoHealthError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
