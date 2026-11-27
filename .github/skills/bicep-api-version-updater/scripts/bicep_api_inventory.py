#!/usr/bin/env python3
"""Bicep resource API version inventory, public version fetcher, and coverage verifier.

Zero-dependency (Python 3.14+ stdlib only) helper for the bicep-api-version-updater
skill. Performs deterministic extraction, public-reference lookups (without Azure
authentication), and coverage checks so the agent can focus on compatibility and
breaking-change analysis.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

USER_AGENT = "aks-chaos-lab-bicep-api-inventory/1.0"
VERSION_TOKEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-[a-zA-Z0-9]+)?$")
GA_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TYPE_REF_RE = re.compile(
    r"\b(?P<kind>resourceInput|resourceOutput)\s*<\s*'(?P<spec>[^']+)'\s*>"
)
RESOURCE_DECL_RE = re.compile(
    r"^\s*resource\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+'(?P<spec>[^']+)'"
    r"(?P<after>.*)$"
)

# Resource-type (provider/top-level-type) specific Azure/azure-rest-api-specs roots
# for providers whose specs are split across multiple service directories.
RESOURCE_TYPE_SPEC_ROOTS: dict[str, str] = {
    "microsoft.cache/redis": "specification/redis/resource-manager/Microsoft.Cache",
    "microsoft.cache/redisenterprise": (
        "specification/redisenterprise/resource-manager/Microsoft.Cache"
    ),
    "microsoft.containerservice/fleets": (
        "specification/containerservice/resource-manager/Microsoft.ContainerService/fleet"
    ),
    "microsoft.containerservice/managedclusters": (
        "specification/containerservice/resource-manager/Microsoft.ContainerService/aks"
    ),
    "microsoft.insights/components": (
        "specification/applicationinsights/resource-manager/Microsoft.Insights/components"
    ),
    "microsoft.insights/datacollectionendpoints": (
        "specification/monitor/resource-manager/Microsoft.Insights"
    ),
    "microsoft.insights/datacollectionrules": (
        "specification/monitor/resource-manager/Microsoft.Insights"
    ),
    "microsoft.insights/diagnosticsettings": (
        "specification/monitor/resource-manager/Microsoft.Insights"
    ),
    "microsoft.insights/metricalerts": (
        "specification/monitor/resource-manager/Microsoft.Insights"
    ),
    "microsoft.insights/scheduledqueryrules": (
        "specification/monitor/resource-manager/Microsoft.Insights"
    ),
    "microsoft.network/privatednszones": (
        "specification/privatedns/resource-manager/Microsoft.Network"
    ),
}

# Known Azure/azure-rest-api-specs roots for providers that map to a single spec root.
PROVIDER_SPEC_ROOTS: dict[str, str] = {
    "microsoft.alertsmanagement": (
        "specification/alertsmanagement/resource-manager/Microsoft.AlertsManagement"
    ),
    "microsoft.authorization": (
        "specification/authorization/resource-manager/Microsoft.Authorization"
    ),
    "microsoft.chaos": "specification/chaos/resource-manager/Microsoft.Chaos",
    "microsoft.containerregistry": (
        "specification/containerregistry/resource-manager/Microsoft.ContainerRegistry"
    ),
    "microsoft.keyvault": "specification/keyvault/resource-manager/Microsoft.KeyVault",
    "microsoft.managedidentity": (
        "specification/msi/resource-manager/Microsoft.ManagedIdentity"
    ),
    "microsoft.network": "specification/network/resource-manager/Microsoft.Network",
    "microsoft.operationalinsights": (
        "specification/operationalinsights/resource-manager/Microsoft.OperationalInsights"
    ),
    "microsoft.resources": "specification/resources/resource-manager/Microsoft.Resources",
    "microsoft.storage": "specification/storage/resource-manager/Microsoft.Storage",
    "microsoft.web": "specification/web/resource-manager/Microsoft.Web",
}


@dataclass(frozen=True)
class ResourceDeclaration:
    declaration_id: str
    file: str
    line: int
    name: str
    resource_type: str
    api_version: str
    is_existing: bool = False
    is_conditional: bool = False
    is_loop: bool = False
    is_nested: bool = False
    parent_name: str | None = None
    is_preview: bool = False


@dataclass(frozen=True)
class UnresolvedItem:
    file: str
    line: int | None
    name: str | None
    reason_code: str
    detail: str


@dataclass(frozen=True)
class ExcludedTypeRef:
    file: str
    line: int
    kind: str
    spec: str
    reason: str = "Type reference (not a resource declaration)"


@dataclass
class ParseResult:
    declarations: list[ResourceDeclaration] = field(default_factory=list)
    unresolved: list[UnresolvedItem] = field(default_factory=list)
    excluded_type_refs: list[ExcludedTypeRef] = field(default_factory=list)


def strip_bicep_comments_preserving_lines(text: str) -> str:
    """Replace comments and string interiors safely while keeping line numbers intact.

    Single-quoted string literals are kept intact because resource declarations rely
    on `'Type@version'`. Multiline strings `'''...'''`, line comments `//...`, and
    block comments `/*...*/` are blanked out (preserving newlines) so example resource
    declarations inside comments or multiline strings are never extracted.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        # Multiline string '''...'''
        if text.startswith("'''", i):
            end = text.find("'''", i + 3)
            if end == -1:
                segment = text[i:]
                out.append("\n" * segment.count("\n"))
                break
            segment = text[i : end + 3]
            out.append("\n" * segment.count("\n"))
            i = end + 3
            continue
        # Line comment //...
        if ch == "/" and nxt == "/":
            nl = text.find("\n", i + 2)
            if nl == -1:
                break
            out.append("\n")
            i = nl + 1
            continue
        # Block comment /*...*/
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                segment = text[i:]
                out.append("\n" * segment.count("\n"))
                break
            segment = text[i : end + 2]
            out.append("\n" * segment.count("\n"))
            i = end + 2
            continue
        # Single-quoted string '...' (preserve content, respect \' escapes)
        if ch == "'":
            out.append(ch)
            i += 1
            while i < n:
                c = text[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if c == "'":
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _count_braces_outside_strings(line: str) -> tuple[int, int]:
    """Count `{` and `}` characters outside single-quoted strings."""
    opens = 0
    closes = 0
    in_str = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == "'":
                in_str = False
        else:
            if c == "'":
                in_str = True
            elif c == "{":
                opens += 1
            elif c == "}":
                closes += 1
        i += 1
    return opens, closes


def parse_bicep_text(text: str, rel_path: str) -> ParseResult:
    """Parse resource declarations from a single Bicep file."""
    cleaned = strip_bicep_comments_preserving_lines(text)
    result = ParseResult()
    lines = cleaned.splitlines()

    # Stack of active enclosing resource blocks: (brace_depth_at_entry, ResourceDeclaration)
    resource_stack: list[tuple[int, ResourceDeclaration]] = []
    brace_depth = 0

    for line_no, raw_line in enumerate(lines, start=1):
        for match in TYPE_REF_RE.finditer(raw_line):
            result.excluded_type_refs.append(
                ExcludedTypeRef(
                    file=rel_path,
                    line=line_no,
                    kind=match.group("kind"),
                    spec=match.group("spec"),
                )
            )

        opens, closes = _count_braces_outside_strings(raw_line)
        decl_match = RESOURCE_DECL_RE.match(raw_line)
        if decl_match:
            name = decl_match.group("name")
            spec = decl_match.group("spec").strip()
            after = decl_match.group("after")
            is_existing = bool(re.search(r"\bexisting\b", after.split("=", 1)[0]))
            rhs = after.split("=", 1)[1].strip() if "=" in after else ""
            is_conditional = rhs.startswith("if")
            is_loop = rhs.startswith("[for") or rhs.startswith("[ for")
            parent_decl = resource_stack[-1][1] if resource_stack else None
            is_nested = parent_decl is not None

            if "@" in spec:
                raw_type, raw_version = spec.rsplit("@", 1)
                raw_type = raw_type.strip()
                raw_version = raw_version.strip()
            else:
                raw_type = spec
                raw_version = ""

            if "/" not in raw_type:
                if parent_decl is not None:
                    resolved_type = f"{parent_decl.resource_type}/{raw_type}"
                    resolved_version = raw_version or parent_decl.api_version
                else:
                    result.unresolved.append(
                        UnresolvedItem(
                            file=rel_path,
                            line=line_no,
                            name=name,
                            reason_code="target-unresolved",
                            detail=f"Child resource type '{spec}' has no enclosing parent declaration",
                        )
                    )
                    brace_depth = max(0, brace_depth + opens - closes)
                    while resource_stack and brace_depth <= resource_stack[-1][0]:
                        resource_stack.pop()
                    continue
            else:
                resolved_type = raw_type
                resolved_version = raw_version

            if not resolved_version:
                result.unresolved.append(
                    UnresolvedItem(
                        file=rel_path,
                        line=line_no,
                        name=name,
                        reason_code="target-unresolved",
                        detail=f"Missing API version in resource declaration '{spec}'",
                    )
                )
            else:
                decl = ResourceDeclaration(
                    declaration_id=f"{rel_path}:{line_no}:{name}",
                    file=rel_path,
                    line=line_no,
                    name=name,
                    resource_type=resolved_type,
                    api_version=resolved_version,
                    is_existing=is_existing,
                    is_conditional=is_conditional,
                    is_loop=is_loop,
                    is_nested=is_nested,
                    parent_name=parent_decl.name if parent_decl else None,
                    is_preview="preview" in resolved_version.lower(),
                )
                result.declarations.append(decl)
                if opens > closes:
                    resource_stack.append((brace_depth, decl))

        brace_depth = max(0, brace_depth + opens - closes)
        while resource_stack and brace_depth <= resource_stack[-1][0]:
            resource_stack.pop()

    return result


def _run_git(repo_root: Path, args: list[str]) -> tuple[int, str, str]:
    git_bin = shutil.which("git")
    if not git_bin:
        return 127, "", "git executable not found"
    completed = subprocess.run(
        [git_bin, *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def discover_bicep_files(
    repo_root: Path, targets: list[str]
) -> tuple[list[Path], list[UnresolvedItem]]:
    """Enumerate target .bicep files without silently treating errors as empty."""
    discovered: list[Path] = []
    unresolved: list[UnresolvedItem] = []

    if not targets:
        rc, out, err = _run_git(
            repo_root,
            ["ls-files", "--cached", "--others", "--exclude-standard", "--", "*.bicep"],
        )
        if rc == 0:
            for line in out.splitlines():
                if line.strip():
                    candidate = repo_root / line.strip()
                    if candidate.is_file():
                        discovered.append(candidate)
        else:
            if (repo_root / ".git").exists():
                unresolved.append(
                    UnresolvedItem(
                        file=".",
                        line=None,
                        name=None,
                        reason_code="target-unresolved",
                        detail=f"git ls-files failed (rc={rc}): {err.strip()}",
                    )
                )
            else:
                discovered.extend(sorted(repo_root.rglob("*.bicep")))
        return sorted(set(discovered)), unresolved

    for raw in targets:
        path = Path(raw)
        resolved = path if path.is_absolute() else (repo_root / path)
        if not resolved.exists():
            unresolved.append(
                UnresolvedItem(
                    file=raw,
                    line=None,
                    name=None,
                    reason_code="target-unresolved",
                    detail="Target path does not exist",
                )
            )
            continue
        if resolved.is_file():
            discovered.append(resolved)
        elif resolved.is_dir():
            discovered.extend(sorted(resolved.rglob("*.bicep")))
        else:
            unresolved.append(
                UnresolvedItem(
                    file=raw,
                    line=None,
                    name=None,
                    reason_code="target-unresolved",
                    detail="Target path is neither a file nor a directory",
                )
            )

    return sorted(set(discovered)), unresolved


def get_git_context(repo_root: Path) -> dict[str, Any]:
    rc_head, out_head, _ = _run_git(repo_root, ["rev-parse", "HEAD"])
    rc_status, out_status, _ = _run_git(repo_root, ["status", "--short"])
    return {
        "commit": out_head.strip() if rc_head == 0 else None,
        "working_tree_changes": (
            [line for line in out_status.splitlines() if line.strip()]
            if rc_status == 0
            else []
        ),
        "working_tree_dirty": bool(out_status.strip()) if rc_status == 0 else None,
    }


def extract_inventory(repo_root: Path, targets: list[str]) -> dict[str, Any]:
    files, unresolved = discover_bicep_files(repo_root, targets)
    all_declarations: list[ResourceDeclaration] = []
    all_excluded_refs: list[ExcludedTypeRef] = []
    scanned_files: list[str] = []

    for file_path in files:
        try:
            rel = file_path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = str(file_path)
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            unresolved.append(
                UnresolvedItem(
                    file=rel,
                    line=None,
                    name=None,
                    reason_code="target-unresolved",
                    detail=f"Failed to read file: {exc}",
                )
            )
            continue
        scanned_files.append(rel)
        parsed = parse_bicep_text(text, rel)
        all_declarations.extend(parsed.declarations)
        unresolved.extend(parsed.unresolved)
        all_excluded_refs.extend(parsed.excluded_type_refs)

    by_type: dict[str, dict[str, Any]] = {}
    for decl in all_declarations:
        entry = by_type.setdefault(
            decl.resource_type,
            {
                "resource_type": decl.resource_type,
                "current_versions": set(),
                "declaration_ids": [],
            },
        )
        entry["current_versions"].add(decl.api_version)
        entry["declaration_ids"].append(decl.declaration_id)

    unique_types = [
        {
            "resource_type": k,
            "current_versions": sorted(v["current_versions"]),
            "declaration_ids": v["declaration_ids"],
        }
        for k, v in sorted(by_type.items(), key=lambda kv: kv[0].lower())
    ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": str(repo_root),
        "git": get_git_context(repo_root),
        "targets": targets or ["."],
        "summary": {
            "files_scanned": len(scanned_files),
            "declarations_count": len(all_declarations),
            "unique_resource_types": len(unique_types),
            "unresolved_count": len(unresolved),
            "excluded_type_refs_count": len(all_excluded_refs),
        },
        "files": scanned_files,
        "declarations": [asdict(d) for d in all_declarations],
        "unique_types": unique_types,
        "unresolved": [asdict(u) for u in unresolved],
        "excluded_type_refs": [asdict(e) for e in all_excluded_refs],
    }


def _validate_https_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Only https URLs are permitted: {url}")
    return url


def _http_get_text(
    url: str, *, timeout: float, headers: dict[str, str] | None = None
) -> tuple[int | None, str, str | None]:
    _validate_https_url(url)
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, None
    except urllib.error.HTTPError as exc:
        body = ""
        with contextlib.suppress(OSError):
            body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, f"HTTP {exc.code}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, "", str(exc)


def latest_ga_version(versions: list[str]) -> str | None:
    ga = [v for v in versions if GA_VERSION_RE.match(v)]
    return max(ga) if ga else None


def extract_versions_from_learn_html(html: str, resource_type: str) -> list[str]:
    """Extract API version strings from a Microsoft Learn template reference page."""
    parts = resource_type.lower().split("/")
    if len(parts) < 2:
        return []
    provider = re.escape(parts[0])
    rest = re.escape("/".join(parts[1:]))
    # Matches both absolute links (/azure/templates/{provider}/{version}/{rest})
    # and relative links (href="2025-05-01/virtualnetworks" or href="../2026-09-01/a/b")
    # used by Microsoft Learn template reference pages.
    abs_link_re = re.compile(
        rf"/azure/templates/{provider}/(\d{{4}}-\d{{2}}-\d{{2}}(?:-[a-zA-Z0-9]+)?)/{rest}(?=[\"'/?#]|$)",
        re.IGNORECASE,
    )
    rel_link_re = re.compile(
        rf'href="(?:\.\./)*(\d{{4}}-\d{{2}}-\d{{2}}(?:-[a-zA-Z0-9]+)?)/{rest}(?=["/?#]|$)',
        re.IGNORECASE,
    )
    found = set(abs_link_re.findall(html))
    found.update(rel_link_re.findall(html))
    if not found:
        # Fallback: version dropdown options or JSON-embedded apiVersion strings near the type
        option_re = re.compile(
            r'value="(\d{4}-\d{2}-\d{2}(?:-[a-zA-Z0-9]+)?)"', re.IGNORECASE
        )
        found.update(option_re.findall(html))
    return sorted(v for v in found if VERSION_TOKEN_RE.match(v))


def fetch_learn_versions(resource_type: str, timeout: float) -> dict[str, Any]:
    parts = resource_type.lower().split("/")
    url = f"https://learn.microsoft.com/en-us/azure/templates/{'/'.join(parts)}"
    status, body, error = _http_get_text(url, timeout=timeout)
    if status != 200:
        return {
            "source": "microsoft-learn",
            "url": url,
            "status": "unavailable",
            "http_status": status,
            "error": error,
            "versions": [],
            "latest_ga": None,
        }
    versions = extract_versions_from_learn_html(body, resource_type)
    return {
        "source": "microsoft-learn",
        "url": url,
        "status": "ok" if versions else "no-versions-parsed",
        "http_status": status,
        "error": None if versions else "Page fetched but no API version list parsed",
        "versions": versions,
        "latest_ga": latest_ga_version(versions),
    }


def _resolve_github_token() -> str | None:
    for env_key in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(env_key)
        if token:
            return token.strip()
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return None
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        completed = subprocess.run(
            [gh_bin, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    return None


def resolve_spec_root(resource_type: str) -> str:
    """Resolve the single azure-rest-api-specs root directory for a resource type."""
    parts = resource_type.split("/")
    provider = parts[0]
    provider_low = provider.lower()
    if len(parts) >= 2:
        top_level_key = f"{provider_low}/{parts[1].lower()}"
        if top_level_key in RESOURCE_TYPE_SPEC_ROOTS:
            return RESOURCE_TYPE_SPEC_ROOTS[top_level_key]
    if provider_low in PROVIDER_SPEC_ROOTS:
        return PROVIDER_SPEC_ROOTS[provider_low]
    short = provider_low.removeprefix("microsoft.")
    return f"specification/{short}/resource-manager/{provider}"


def _list_github_dir(
    path: str, *, timeout: float, token: str | None
) -> tuple[int | None, list[dict[str, Any]], str | None]:
    url = f"https://api.github.com/repos/Azure/azure-rest-api-specs/contents/{path}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, body, error = _http_get_text(url, timeout=timeout, headers=headers)
    if status != 200:
        return status, [], error
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return status, [], f"Invalid JSON: {exc}"
    if not isinstance(data, list):
        return status, [], "Unexpected non-list GitHub contents response"
    return status, data, None


def fetch_rest_api_specs_versions(
    spec_root: str, *, timeout: float, token: str | None
) -> dict[str, Any]:
    """Fetch stable/preview version directory names for a single spec root.

    Does not merge sibling service subdirectories under a shared provider root,
    avoiding cross-service version contamination.
    """
    status, entries, err = _list_github_dir(spec_root, timeout=timeout, token=token)
    if status != 200:
        return {
            "source": "azure-rest-api-specs",
            "spec_root": spec_root,
            "status": "unavailable",
            "urls": [],
            "error": f"{spec_root}: {err}",
            "versions": [],
            "latest_ga": None,
        }

    dirs = [e for e in entries if e.get("type") == "dir"]
    dir_names = {str(e.get("name", "")) for e in dirs}
    if "stable" not in dir_names and "preview" not in dir_names:
        # Unmapped multi-service layout: do not merge unrelated sibling services.
        if len(dirs) == 1 and dirs[0].get("path"):
            search_base = str(dirs[0]["path"])
        else:
            return {
                "source": "azure-rest-api-specs",
                "spec_root": spec_root,
                "status": "ambiguous-service-layout",
                "urls": [
                    f"https://github.com/Azure/azure-rest-api-specs/tree/main/{spec_root}"
                ],
                "error": (
                    f"Spec root '{spec_root}' has multiple subdirectories without direct "
                    "stable/preview folders; skipping merge to avoid cross-service mixing"
                ),
                "versions": [],
                "latest_ga": None,
            }
    else:
        search_base = spec_root

    discovered_versions: set[str] = set()
    matched_urls: list[str] = []
    for channel in ("stable", "preview"):
        ch_path = f"{search_base}/{channel}"
        ch_status, ch_entries, _ = _list_github_dir(
            ch_path, timeout=timeout, token=token
        )
        if ch_status == 200:
            matched_urls.append(
                f"https://github.com/Azure/azure-rest-api-specs/tree/main/{ch_path}"
            )
            for item in ch_entries:
                name = str(item.get("name", ""))
                if item.get("type") == "dir" and VERSION_TOKEN_RE.match(name):
                    discovered_versions.add(name)

    versions = sorted(discovered_versions)
    return {
        "source": "azure-rest-api-specs",
        "spec_root": spec_root,
        "status": "ok" if versions else "unavailable",
        "urls": sorted(set(matched_urls)),
        "error": None if versions else f"{search_base}: No version directories found",
        "versions": versions,
        "latest_ga": latest_ga_version(versions),
    }


def classify_comparison(
    current_version: str,
    learn_latest_ga: str | None,
    specs_latest_ga: str | None,
) -> str:
    """Classify the relationship between current API version and public GA candidates."""
    is_preview = "preview" in current_version.lower()
    candidates = [v for v in (learn_latest_ga, specs_latest_ga) if v]
    if not candidates:
        return "evidence-unavailable"
    max_ga = max(candidates)
    if is_preview:
        return "preview-current"
    if learn_latest_ga and specs_latest_ga and learn_latest_ga != specs_latest_ga:
        if current_version < max_ga:
            return "update-candidate-source-mismatch"
        return "source-mismatch"
    if current_version < max_ga:
        return "update-candidate"
    if current_version > max_ga:
        return "ahead-of-sources"
    return "up-to-date"


def fetch_versions_for_inventory(
    inventory: dict[str, Any],
    *,
    timeout: float,
    workers: int,
    include_github: bool,
) -> dict[str, Any]:
    unique_types: list[dict[str, Any]] = inventory.get("unique_types", [])
    type_to_spec_root = {
        item["resource_type"]: resolve_spec_root(item["resource_type"])
        for item in unique_types
        if "/" in item["resource_type"]
    }
    unique_spec_roots = sorted(set(type_to_spec_root.values()))
    token = _resolve_github_token() if include_github else None

    learn_results: dict[str, dict[str, Any]] = {}
    specs_results: dict[str, dict[str, Any]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        learn_futures = {
            pool.submit(fetch_learn_versions, item["resource_type"], timeout): item[
                "resource_type"
            ]
            for item in unique_types
        }
        specs_futures = (
            {
                pool.submit(
                    fetch_rest_api_specs_versions,
                    spec_root,
                    timeout=timeout,
                    token=token,
                ): spec_root
                for spec_root in unique_spec_roots
            }
            if include_github
            else {}
        )
        for fut, rtype in learn_futures.items():
            learn_results[rtype] = fut.result()
        for fut, spec_root in specs_futures.items():
            specs_results[spec_root] = fut.result()

    type_summaries: list[dict[str, Any]] = []
    for item in unique_types:
        rtype = item["resource_type"]
        spec_root = type_to_spec_root.get(rtype, "")
        learn = learn_results[rtype]
        specs = specs_results.get(
            spec_root,
            {
                "source": "azure-rest-api-specs",
                "spec_root": spec_root,
                "status": "skipped",
                "urls": [],
                "error": "Skipped by --no-github",
                "versions": [],
                "latest_ga": None,
            },
        )
        per_current = {
            cur: classify_comparison(cur, learn["latest_ga"], specs["latest_ga"])
            for cur in item["current_versions"]
        }
        type_summaries.append(
            {
                "resource_type": rtype,
                "current_versions": item["current_versions"],
                "declaration_ids": item["declaration_ids"],
                "learn": learn,
                "rest_api_specs": specs,
                "comparisons": per_current,
            }
        )

    declaration_comparisons: list[dict[str, Any]] = []
    by_type_map = {t["resource_type"]: t for t in type_summaries}
    for decl in inventory.get("declarations", []):
        tinfo = by_type_map.get(decl["resource_type"], {})
        learn_ga = tinfo.get("learn", {}).get("latest_ga")
        specs_ga = tinfo.get("rest_api_specs", {}).get("latest_ga")
        comp = classify_comparison(decl["api_version"], learn_ga, specs_ga)
        declaration_comparisons.append(
            {
                "declaration_id": decl["declaration_id"],
                "file": decl["file"],
                "line": decl["line"],
                "name": decl["name"],
                "resource_type": decl["resource_type"],
                "current_version": decl["api_version"],
                "latest_ga_learn": learn_ga,
                "latest_ga_specs": specs_ga,
                "comparison": comp,
                "needs_deep_review": comp != "up-to-date",
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "github_authenticated": bool(token) if include_github else False,
        "summary": {
            "total_declarations": len(declaration_comparisons),
            "needs_deep_review": sum(
                1 for d in declaration_comparisons if d["needs_deep_review"]
            ),
            "up_to_date": sum(
                1 for d in declaration_comparisons if d["comparison"] == "up-to-date"
            ),
            "update_candidates": sum(
                1
                for d in declaration_comparisons
                if d["comparison"].startswith("update-candidate")
            ),
            "preview_current": sum(
                1
                for d in declaration_comparisons
                if d["comparison"] == "preview-current"
            ),
            "evidence_unavailable": sum(
                1
                for d in declaration_comparisons
                if d["comparison"] == "evidence-unavailable"
            ),
        },
        "types": type_summaries,
        "declarations": declaration_comparisons,
    }


VALID_STATUSES = {"verified", "unverified", "excluded"}
VALID_UNVERIFIED_REASONS = {
    "not-processed",
    "target-unresolved",
    "evidence-unavailable",
    "assessment-incomplete",
}


def verify_coverage(
    inventory: dict[str, Any], assessments: dict[str, Any]
) -> dict[str, Any]:
    """Verify that every extracted declaration has a valid assessment entry."""
    expected_ids = {d["declaration_id"] for d in inventory.get("declarations", [])}
    items = assessments.get("declarations", [])
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    invalid_entries: list[dict[str, Any]] = []
    unjustified_not_processed: list[str] = []

    status_counts = {"verified": 0, "unverified": 0, "excluded": 0}
    unverified_reason_counts: dict[str, int] = dict.fromkeys(
        VALID_UNVERIFIED_REASONS, 0
    )
    issue_count = 0
    update_count = 0

    for entry in items:
        decl_id = str(entry.get("declaration_id", ""))
        if decl_id in seen_ids:
            duplicate_ids.append(decl_id)
        seen_ids.add(decl_id)

        status = entry.get("status")
        if status not in VALID_STATUSES:
            invalid_entries.append(
                {"declaration_id": decl_id, "error": f"Invalid status: {status!r}"}
            )
            continue
        status_counts[status] += 1

        if status == "unverified":
            reason = entry.get("reason_code")
            if reason not in VALID_UNVERIFIED_REASONS:
                invalid_entries.append(
                    {
                        "declaration_id": decl_id,
                        "error": f"Invalid unverified reason_code: {reason!r}",
                    }
                )
            else:
                unverified_reason_counts[reason] += 1
                if reason == "not-processed" and not entry.get("external_constraint"):
                    unjustified_not_processed.append(decl_id)

        if entry.get("has_issue"):
            issue_count += 1
        if entry.get("has_update"):
            update_count += 1

    missing_ids = sorted(expected_ids - seen_ids)
    extra_ids = sorted(seen_ids - expected_ids)
    unresolved_inventory = inventory.get("unresolved", [])
    complete = (
        not missing_ids
        and not extra_ids
        and not duplicate_ids
        and not invalid_entries
        and not unresolved_inventory
        and status_counts["unverified"] == 0
    )

    return {
        "schema_version": 1,
        "complete": complete,
        "coverage_valid": (
            not missing_ids
            and not extra_ids
            and not duplicate_ids
            and not invalid_entries
            and not unjustified_not_processed
        ),
        "counts": {
            "expected_declarations": len(expected_ids),
            "assessed_declarations": len(seen_ids),
            "status": status_counts,
            "unverified_reasons": unverified_reason_counts,
            "issues": issue_count,
            "updates": update_count,
            "unresolved_targets": len(unresolved_inventory),
        },
        "missing_declaration_ids": missing_ids,
        "extra_declaration_ids": extra_ids,
        "duplicate_declaration_ids": sorted(set(duplicate_ids)),
        "invalid_entries": invalid_entries,
        "unjustified_not_processed_ids": sorted(unjustified_not_processed),
    }


def _write_json(data: dict[str, Any], output_path: str | None) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if output_path:
        Path(output_path).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bicep API version inventory, public lookup, and coverage verifier"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser(
        "extract", help="Extract all Bicep resource declarations"
    )
    p_extract.add_argument(
        "--repo-root", default=".", help="Repository root directory (default: .)"
    )
    p_extract.add_argument(
        "--target",
        action="append",
        default=[],
        help="Target file or directory (repeatable; default: entire repo)",
    )
    p_extract.add_argument("--output", help="Write JSON output to file")

    p_fetch = sub.add_parser(
        "fetch-versions",
        help="Fetch public API version lists from Microsoft Learn and azure-rest-api-specs",
    )
    p_fetch.add_argument(
        "--inventory", required=True, help="Path to JSON generated by extract"
    )
    p_fetch.add_argument("--output", help="Write JSON output to file")
    p_fetch.add_argument(
        "--timeout", type=float, default=15.0, help="HTTP timeout seconds (default: 15)"
    )
    p_fetch.add_argument(
        "--workers", type=int, default=8, help="Concurrent worker count (default: 8)"
    )
    p_fetch.add_argument(
        "--no-github",
        action="store_true",
        help="Skip Azure/azure-rest-api-specs GitHub API queries",
    )

    p_verify = sub.add_parser(
        "verify-coverage",
        help="Verify that all extracted declarations are covered by assessments",
    )
    p_verify.add_argument(
        "--inventory", required=True, help="Path to JSON generated by extract"
    )
    p_verify.add_argument(
        "--assessments", required=True, help="Path to assessment JSON produced by agent"
    )
    p_verify.add_argument("--output", help="Write JSON output to file")

    args = parser.parse_args(argv)

    if args.command == "extract":
        result = extract_inventory(Path(args.repo_root).resolve(), args.target)
        _write_json(result, args.output)
        return 0

    if args.command == "fetch-versions":
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        result = fetch_versions_for_inventory(
            inventory,
            timeout=args.timeout,
            workers=args.workers,
            include_github=not args.no_github,
        )
        _write_json(result, args.output)
        return 0

    if args.command == "verify-coverage":
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        assessments = json.loads(Path(args.assessments).read_text(encoding="utf-8"))
        result = verify_coverage(inventory, assessments)
        _write_json(result, args.output)
        return 0 if result["coverage_valid"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
