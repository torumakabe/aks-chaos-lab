#!/usr/bin/env python3
"""Markdown public link & Azure Functions extensionBundle extractor, checker, and coverage verifier.

Zero-dependency (Python 3.14+ stdlib only) helper for the repository-freshness-checker
skill. Performs deterministic extraction, deduplicated HTTP reachability checks (with
no Azure authentication), and coverage verification.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import ipaddress
import json
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

USER_AGENT = "aks-chaos-lab-freshness-inventory/1.0"

INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*(?P<url>https?://[^\s)]+)(?:\s+[^)]*)?\)")
REF_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*<?(?P<url>https?://[^\s>]+)>?")
AUTOLINK_RE = re.compile(r"<(?P<url>https?://[^>\s]+)>")
BARE_URL_RE = re.compile(r"(?<![(\[<\"'=])(?P<url>https?://[^\s<>)\"']+)")

# NuGet-style version range used by Azure Functions extensionBundle.version, e.g. [4.*, 5.0.0)
BUNDLE_RANGE_RE = re.compile(
    r"^\s*[\[(]\s*(?P<lower>\d+(?:\.(?:\d+|\*))?(?:\.(?:\d+|\*))?)\s*,\s*"
    r"(?P<upper>\d+(?:\.(?:\d+|\*))?(?:\.(?:\d+|\*))?)\s*[\])]\s*$"
)
EXACT_VERSION_RE = re.compile(r"^\s*(?P<major>\d+)\.(?:\d+|\*)(?:\.(?:\d+|\*))?\s*$")

NON_PUBLIC_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "example.com",
    "example.org",
    "example.net",
}
NON_PUBLIC_SUFFIXES = (".local", ".internal", ".localhost", ".example")
PLACEHOLDER_RE = re.compile(r"[<>{}]|\$\{")


@dataclass(frozen=True)
class LinkReference:
    target_id: str
    file: str
    line: int
    url: str
    kind: str


@dataclass(frozen=True)
class ExcludedLink:
    file: str
    line: int
    url: str
    reason: str


@dataclass(frozen=True)
class BundleTarget:
    target_id: str
    file: str
    setting: str
    bundle_id: str | None
    version_range: str | None
    major_versions: list[int]
    parse_issue: str | None


@dataclass(frozen=True)
class UnresolvedItem:
    file: str
    reason_code: str
    detail: str


@dataclass
class MarkdownExtractResult:
    links: list[LinkReference] = field(default_factory=list)
    excluded: list[ExcludedLink] = field(default_factory=list)


def _clean_url(raw: str) -> str:
    url = raw.strip()
    while url and url[-1] in ".,;:!?":
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url


def classify_public_url(url: str) -> str | None:
    """Return an exclusion reason if the URL is not clearly public; otherwise None."""
    if PLACEHOLDER_RE.search(url):
        return "Contains template placeholder characters"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "Not a valid http(s) URL"
    host = (parsed.hostname or "").lower()
    if not host:
        return "Missing hostname"
    if host in NON_PUBLIC_HOSTS or host.endswith(NON_PUBLIC_SUFFIXES):
        return f"Non-public or example host ({host})"
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            return f"Non-global IP address ({host})"
    except ValueError:
        if "." not in host:
            return f"Unqualified hostname ({host})"
    return None


def extract_markdown_links(text: str, rel_path: str) -> MarkdownExtractResult:
    """Extract public HTTP/HTTPS links from Markdown while skipping code blocks and inline code."""
    result = MarkdownExtractResult()
    in_fence = False
    fence_marker = ""

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.lstrip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_marker = stripped[:3]
                continue
        else:
            if stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue

        # Remove inline code spans so example URLs in backticks are ignored.
        line = INLINE_CODE_RE.sub("", raw_line)

        found_on_line: list[tuple[int, str, str]] = []
        used_spans: list[tuple[int, int]] = []

        for kind, pattern in (
            ("reference", REF_DEF_RE),
            ("markdown", MD_LINK_RE),
            ("autolink", AUTOLINK_RE),
            ("bare", BARE_URL_RE),
        ):
            for match in pattern.finditer(line):
                span = match.span("url")
                if any(not (span[1] <= s[0] or span[0] >= s[1]) for s in used_spans):
                    continue
                used_spans.append(span)
                url = _clean_url(match.group("url"))
                if url:
                    found_on_line.append((span[0], kind, url))

        found_on_line.sort(key=lambda item: item[0])
        for idx, (_, kind, url) in enumerate(found_on_line, start=1):
            exclusion = classify_public_url(url)
            if exclusion:
                result.excluded.append(
                    ExcludedLink(file=rel_path, line=line_no, url=url, reason=exclusion)
                )
            else:
                result.links.append(
                    LinkReference(
                        target_id=f"link:{rel_path}:{line_no}:{idx}",
                        file=rel_path,
                        line=line_no,
                        url=url,
                        kind=kind,
                    )
                )

    return result


def parse_bundle_version_range(version_range: str) -> tuple[list[int], str | None]:
    """Parse an Azure Functions extensionBundle version range into covered major versions."""
    match = BUNDLE_RANGE_RE.match(version_range)
    if match:
        lower_major = int(match.group("lower").split(".", 1)[0])
        upper_parts = match.group("upper").split(".")
        upper_major = int(upper_parts[0])
        upper_inclusive = version_range.strip().endswith("]")
        upper_is_exact_zero = len(upper_parts) >= 2 and all(
            p == "0" for p in upper_parts[1:]
        )
        max_major = (
            upper_major
            if upper_inclusive or not upper_is_exact_zero
            else upper_major - 1
        )
        if max_major < lower_major:
            return [], f"Empty or inverted version range: {version_range!r}"
        return list(range(lower_major, max_major + 1)), None

    exact = EXACT_VERSION_RE.match(version_range)
    if exact:
        return [int(exact.group("major"))], None

    return [], f"Unrecognized extensionBundle version range syntax: {version_range!r}"


def extract_host_json_bundle(text: str, rel_path: str) -> BundleTarget:
    target_id = f"bundle:{rel_path}:extensionBundle.version"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return BundleTarget(
            target_id=target_id,
            file=rel_path,
            setting="extensionBundle.version",
            bundle_id=None,
            version_range=None,
            major_versions=[],
            parse_issue=f"Invalid JSON: {exc}",
        )

    if not isinstance(data, dict):
        return BundleTarget(
            target_id=target_id,
            file=rel_path,
            setting="extensionBundle.version",
            bundle_id=None,
            version_range=None,
            major_versions=[],
            parse_issue="Top-level host.json value is not a JSON object",
        )

    bundle = data.get("extensionBundle")
    if not isinstance(bundle, dict):
        return BundleTarget(
            target_id=target_id,
            file=rel_path,
            setting="extensionBundle.version",
            bundle_id=None,
            version_range=None,
            major_versions=[],
            parse_issue="Missing or non-object extensionBundle section",
        )

    bundle_id = bundle.get("id")
    version_val = bundle.get("version")
    if not isinstance(version_val, str) or not version_val.strip():
        return BundleTarget(
            target_id=target_id,
            file=rel_path,
            setting="extensionBundle.version",
            bundle_id=str(bundle_id) if bundle_id is not None else None,
            version_range=None,
            major_versions=[],
            parse_issue="Missing or empty extensionBundle.version",
        )

    majors, err = parse_bundle_version_range(version_val)
    return BundleTarget(
        target_id=target_id,
        file=rel_path,
        setting="extensionBundle.version",
        bundle_id=str(bundle_id) if bundle_id is not None else None,
        version_range=version_val,
        major_versions=majors,
        parse_issue=err,
    )


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


def discover_targets(
    repo_root: Path, targets: list[str]
) -> tuple[list[Path], list[Path], list[UnresolvedItem]]:
    """Discover Markdown files and Functions host.json files."""
    md_files: list[Path] = []
    host_files: list[Path] = []
    unresolved: list[UnresolvedItem] = []

    if not targets:
        rc, out, err = _run_git(
            repo_root,
            [
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "*.md",
                "host.json",
                "**/host.json",
            ],
        )
        if rc == 0:
            for line in out.splitlines():
                if not line.strip():
                    continue
                candidate = repo_root / line.strip()
                if not candidate.is_file():
                    continue
                if candidate.name == "host.json":
                    host_files.append(candidate)
                elif candidate.suffix.lower() == ".md":
                    md_files.append(candidate)
        else:
            if (repo_root / ".git").exists():
                unresolved.append(
                    UnresolvedItem(
                        file=".",
                        reason_code="target-unresolved",
                        detail=f"git ls-files failed (rc={rc}): {err.strip()}",
                    )
                )
            else:
                md_files.extend(sorted(repo_root.rglob("*.md")))
                host_files.extend(sorted(repo_root.rglob("host.json")))
        return sorted(set(md_files)), sorted(set(host_files)), unresolved

    for raw in targets:
        path = Path(raw)
        resolved = path if path.is_absolute() else (repo_root / path)
        if not resolved.exists():
            unresolved.append(
                UnresolvedItem(
                    file=raw,
                    reason_code="target-unresolved",
                    detail="Target path does not exist",
                )
            )
            continue
        if resolved.is_file():
            if resolved.name == "host.json":
                host_files.append(resolved)
            else:
                md_files.append(resolved)
        elif resolved.is_dir():
            md_files.extend(sorted(resolved.rglob("*.md")))
            host_files.extend(sorted(resolved.rglob("host.json")))
        else:
            unresolved.append(
                UnresolvedItem(
                    file=raw,
                    reason_code="target-unresolved",
                    detail="Target path is neither a file nor a directory",
                )
            )

    return sorted(set(md_files)), sorted(set(host_files)), unresolved


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
    md_files, host_files, unresolved = discover_targets(repo_root, targets)
    links: list[LinkReference] = []
    excluded_links: list[ExcludedLink] = []
    bundles: list[BundleTarget] = []
    scanned_md: list[str] = []
    scanned_host: list[str] = []

    for path in md_files:
        try:
            rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            unresolved.append(
                UnresolvedItem(
                    file=rel,
                    reason_code="target-unresolved",
                    detail=f"Failed to read Markdown file: {exc}",
                )
            )
            continue
        scanned_md.append(rel)
        extracted = extract_markdown_links(text, rel)
        links.extend(extracted.links)
        excluded_links.extend(extracted.excluded)

    for path in host_files:
        try:
            rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            unresolved.append(
                UnresolvedItem(
                    file=rel,
                    reason_code="target-unresolved",
                    detail=f"Failed to read host.json: {exc}",
                )
            )
            continue
        scanned_host.append(rel)
        bundles.append(extract_host_json_bundle(text, rel))

    url_map: dict[str, list[str]] = {}
    for ref in links:
        url_map.setdefault(ref.url, []).append(ref.target_id)

    unique_urls = [
        {"url": url, "target_ids": ids} for url, ids in sorted(url_map.items())
    ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": str(repo_root),
        "git": get_git_context(repo_root),
        "targets": targets or ["."],
        "summary": {
            "markdown_files_scanned": len(scanned_md),
            "host_json_files_scanned": len(scanned_host),
            "link_references_count": len(links),
            "unique_urls_count": len(unique_urls),
            "excluded_links_count": len(excluded_links),
            "bundle_targets_count": len(bundles),
            "unresolved_count": len(unresolved),
        },
        "markdown_files": scanned_md,
        "host_json_files": scanned_host,
        "links": [asdict(item) for item in links],
        "unique_urls": unique_urls,
        "excluded_links": [asdict(item) for item in excluded_links],
        "bundles": [asdict(item) for item in bundles],
        "unresolved": [asdict(item) for item in unresolved],
    }


def _probe_url(url: str, *, timeout: float) -> dict[str, Any]:
    exclusion = classify_public_url(url)
    if exclusion:
        return {
            "url": url,
            "classification": "excluded",
            "http_status": None,
            "final_url": None,
            "error": exclusion,
        }

    headers = {"User-Agent": USER_AGENT}
    last_status: int | None = None
    last_final_url: str | None = None
    last_error: str | None = None

    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                final_url = resp.url
                return {
                    "url": url,
                    "classification": "ok",
                    "http_status": status,
                    "final_url": final_url,
                    "method": method,
                    "error": None,
                }
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_final_url = exc.url
            last_error = f"HTTP {exc.code}: {exc.reason}"
            with contextlib.suppress(OSError):
                exc.read(0)
            if method == "HEAD" and exc.code in (400, 403, 405, 501):
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_status = None
            last_final_url = None
            last_error = str(exc)
            break

    classification = "broken" if last_status in (404, 410) else "unverified"

    return {
        "url": url,
        "classification": classification,
        "http_status": last_status,
        "final_url": last_final_url,
        "error": last_error,
    }


def check_links_for_inventory(
    inventory: dict[str, Any], *, timeout: float, workers: int
) -> dict[str, Any]:
    unique_urls: list[dict[str, Any]] = inventory.get("unique_urls", [])
    results_by_url: dict[str, dict[str, Any]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_probe_url, item["url"], timeout=timeout): item["url"]
            for item in unique_urls
        }
        for fut, url in futures.items():
            results_by_url[url] = fut.result()

    url_results = [
        {**results_by_url[item["url"]], "target_ids": item["target_ids"]}
        for item in unique_urls
    ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "unique_urls_checked": len(url_results),
            "ok": sum(1 for r in url_results if r["classification"] == "ok"),
            "broken": sum(1 for r in url_results if r["classification"] == "broken"),
            "unverified": sum(
                1 for r in url_results if r["classification"] == "unverified"
            ),
        },
        "urls": url_results,
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
    expected_ids = {item["target_id"] for item in inventory.get("links", [])} | {
        item["target_id"] for item in inventory.get("bundles", [])
    }
    items = assessments.get("targets", [])
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    invalid_entries: list[dict[str, Any]] = []
    unjustified_not_processed: list[str] = []

    status_counts = {"verified": 0, "unverified": 0, "excluded": 0}
    unverified_reason_counts: dict[str, int] = dict.fromkeys(
        VALID_UNVERIFIED_REASONS, 0
    )
    issue_count = 0

    for entry in items:
        tid = str(entry.get("target_id", ""))
        if tid in seen_ids:
            duplicate_ids.append(tid)
        seen_ids.add(tid)

        status = entry.get("status")
        if status not in VALID_STATUSES:
            invalid_entries.append(
                {"target_id": tid, "error": f"Invalid status: {status!r}"}
            )
            continue
        status_counts[status] += 1

        if status == "unverified":
            reason = entry.get("reason_code")
            if reason not in VALID_UNVERIFIED_REASONS:
                invalid_entries.append(
                    {
                        "target_id": tid,
                        "error": f"Invalid unverified reason_code: {reason!r}",
                    }
                )
            else:
                unverified_reason_counts[reason] += 1
                if reason == "not-processed" and not entry.get("external_constraint"):
                    unjustified_not_processed.append(tid)

        if entry.get("has_issue"):
            issue_count += 1

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
            "expected_targets": len(expected_ids),
            "assessed_targets": len(seen_ids),
            "status": status_counts,
            "unverified_reasons": unverified_reason_counts,
            "issues": issue_count,
            "unresolved_targets": len(unresolved_inventory),
        },
        "missing_target_ids": missing_ids,
        "extra_target_ids": extra_ids,
        "duplicate_target_ids": sorted(set(duplicate_ids)),
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
        description="Public Markdown link & Functions extensionBundle extractor, checker, and coverage verifier"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser(
        "extract", help="Extract public Markdown links and host.json extensionBundle"
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

    p_check = sub.add_parser(
        "check-links", help="Check HTTP reachability of deduplicated public URLs"
    )
    p_check.add_argument(
        "--inventory", required=True, help="Path to JSON generated by extract"
    )
    p_check.add_argument("--output", help="Write JSON output to file")
    p_check.add_argument(
        "--timeout", type=float, default=15.0, help="HTTP timeout seconds (default: 15)"
    )
    p_check.add_argument(
        "--workers", type=int, default=8, help="Concurrent worker count (default: 8)"
    )

    p_verify = sub.add_parser(
        "verify-coverage",
        help="Verify that all extracted links and host.json bundles are covered",
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

    if args.command == "check-links":
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        result = check_links_for_inventory(
            inventory, timeout=args.timeout, workers=args.workers
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
