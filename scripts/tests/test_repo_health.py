from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


repo_health = load_module("repository_health", REPO_ROOT / "scripts" / "repo_health.py")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_config(
    root: Path,
    *,
    enforce: bool,
    exception: bool,
    review_by: str = "2026-11-30",
) -> None:
    exception_table = (
        f"""
[rules.exception]
reason = "Known fixture mismatch."
owner = "test"
tracking = "plan:test-baseline-fix"
review_by = {review_by}
resolution = "Update all fixture coordinates."
canonical_values = ["1.35"]
target_fingerprint = [
  {{ path = ".github/workflows/ci.yml", selector = "workflow-kubernetes-version", value = "1.34.0" }},
]
"""
        if exception
        else ""
    )
    write(
        root / ".github" / "repo-health.toml",
        f"""schema_version = 2

[[rules]]
id = "kubernetes-version"
enforce = {str(enforce).lower()}

[rules.canonical]
path = "infra/main.bicep"
selector = "bicep-parameter:kubernetesVersion"

[[rules.targets]]
path = ".github/workflows/ci.yml"
selector = "workflow-kubernetes-version"
{exception_table}""",
    )


def initialize_repository(root: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.fail("git is required for repository health tests", pytrace=False)
    subprocess.run(
        [git, "init", "--quiet"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [git, "add", "."],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            git,
            "-c",
            "user.name=Repo Health Tests",
            "-c",
            "user.email=repo-health@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    write(
        tmp_path / "pyproject.toml",
        '[tool.uv.workspace]\nmembers = ["src/api"]\n',
    )
    write(tmp_path / "uv.lock", "version = 1\n")
    write(tmp_path / "src/api/requirements.txt", "fastapi==1.0\n")
    write(
        tmp_path / ".github/workflows/ci.yml",
        "steps:\n  - uses: actions/checkout@v4\n"
        "  - run: kubeconform -kubernetes-version 1.34.0\n",
    )
    write(
        tmp_path / ".github/workflows/copilot-setup-steps.yml",
        "steps:\n"
        "  - uses: github/gh-aw-actions/setup-cli@sha\n"
        "    with:\n"
        "      version: v0.71.1\n",
    )
    write(
        tmp_path / ".github/workflows/example.lock.yml",
        '# gh-aw-metadata: {"compiler_version":"v0.79.6"}\n',
    )
    write(
        tmp_path / ".github/aw/actions-lock.json",
        '{"entries": {}}\n',
    )
    write(tmp_path / ".github/agents/check.agent.md", "# Agent\n")
    write(tmp_path / ".github/skills/check/SKILL.md", "# Skill\n")
    write(tmp_path / ".github/hooks/hooks.json", '{"hooks": {}}\n')
    write(
        tmp_path / "infra/main.bicep",
        "param kubernetesVersion string = '1.35'\n"
        "resource cluster 'Microsoft.ContainerService/managedClusters@2025-07-01' = {}\n",
    )
    write(tmp_path / "scripts/tasks.py", 'K8S_VERSION = "1.33.0"\n')
    write(
        tmp_path / "src/api/Dockerfile",
        "FROM python:3.14-slim\nFROM example.test/runtime@sha256:abc\n",
    )
    write(
        tmp_path / "azure.yaml",
        "helm:\n  releases:\n    - chart: chaos-mesh/chaos-mesh\n"
        "      version: 2.8.3\n",
    )
    write(
        tmp_path / "k8s/apps/app/deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\n",
    )
    write(
        tmp_path / "k8s/apps/app/kustomization.yaml",
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n",
    )
    write(tmp_path / "README.md", "See ADR-001 and docs/workarounds.md.\n")
    write(tmp_path / "docs/adr/ADR-001-test.md", "# ADR\n")
    write(tmp_path / "docs/features/current.md", "# Feature\n")
    write(tmp_path / "docs/workarounds.md", "# Workarounds\n")
    write_config(tmp_path, enforce=False, exception=True)
    initialize_repository(tmp_path)
    return tmp_path


def test_inventory_extracts_supported_coordinates(repository: Path) -> None:
    tracked = repo_health.list_tracked_files(repository)
    scan = repo_health.scan_inventory(repository, tracked)
    categories = {coordinate.category for coordinate in scan.coordinates}

    assert {
        "agent-source",
        "bicep-resource-api",
        "docker-base-image",
        "documentation-reference",
        "documentation-source",
        "helm-chart",
        "helm-version",
        "hook-source",
        "kubernetes-manifest",
        "kustomize",
        "python-lock",
        "python-manifest",
        "python-requirements",
        "python-workspace-member",
        "repository-health-config",
        "skill-source",
        "version-constant",
        "workflow-action",
        "workflow-lock",
        "workflow-source",
    } <= categories
    assert not scan.extraction_failures


def test_zero_coordinate_extraction_fails(repository: Path) -> None:
    write(repository / ".github/workflows/ci.yml", "steps: []\n")
    rules = repo_health.load_rules(repository / ".github/repo-health.toml")

    checks = repo_health.run_checks(repository, rules, date(2026, 8, 25))

    assert checks[0].status == "fail"
    assert checks[0].message.startswith("target extraction returned zero coordinates")


@pytest.mark.parametrize("failed_location_index", [0, 1, 2])
def test_extraction_failure_finding_uses_failed_configured_location(
    repository: Path,
    failed_location_index: int,
) -> None:
    locations = [
        repo_health.Location("infra/main.bicep", "bicep-parameter:kubernetesVersion"),
        repo_health.Location(".github/workflows/ci.yml", "workflow-kubernetes-version"),
        repo_health.Location("scripts/tasks.py", "python-constant:K8S_VERSION"),
    ]
    failed_location = repo_health.Location(
        f"missing-{failed_location_index}.txt",
        locations[failed_location_index].selector,
    )
    locations[failed_location_index] = failed_location
    rule = repo_health.Rule(
        "kubernetes-version",
        locations[0],
        tuple(locations[1:]),
        True,
        None,
    )

    checks = repo_health.run_checks(repository, [rule], date(2026, 8, 25))
    findings = repo_health._check_findings(checks)

    assert checks[0].failure_location == failed_location
    assert findings[0].path == failed_location.path
    assert findings[0].location == failed_location.selector


def test_matching_and_mismatching_coordinates(repository: Path) -> None:
    rule = repo_health.Rule(
        "kubernetes-version",
        repo_health.Location("infra/main.bicep", "bicep-parameter:kubernetesVersion"),
        (
            repo_health.Location(
                ".github/workflows/ci.yml", "workflow-kubernetes-version"
            ),
        ),
        True,
        None,
    )

    mismatch = repo_health.run_checks(repository, [rule], date(2026, 8, 25))
    write(
        repository / ".github/workflows/ci.yml",
        "steps:\n  - run: kubeconform -kubernetes-version 1.35.0\n",
    )
    match = repo_health.run_checks(repository, [rule], date(2026, 8, 25))

    assert mismatch[0].status == "fail"
    assert match[0].status == "pass"


def test_exception_is_valid_until_review_date(repository: Path) -> None:
    rules = repo_health.load_rules(repository / ".github/repo-health.toml")

    checks = repo_health.run_checks(repository, rules, date(2026, 8, 25))

    assert checks[0].status == "excluded"
    assert checks[0].exception is not None
    assert checks[0].exception["tracking"] == "plan:test-baseline-fix"
    assert checks[0].exception["target_fingerprint"] == [
        {
            "path": ".github/workflows/ci.yml",
            "selector": "workflow-kubernetes-version",
            "value": "1.34.0",
        }
    ]


def test_exception_rejects_a_different_mismatch(repository: Path) -> None:
    write(
        repository / ".github/workflows/ci.yml",
        "steps:\n  - run: kubeconform -kubernetes-version 1.32.0\n",
    )
    rules = repo_health.load_rules(repository / ".github/repo-health.toml")

    checks = repo_health.run_checks(repository, rules, date(2026, 8, 25))

    assert checks[0].status == "fail"
    assert "differs from the exception snapshot" in checks[0].message


def test_exception_fingerprint_preserves_target_path_selector_associations(
    repository: Path,
) -> None:
    policy = repo_health.ExceptionPolicy(
        reason="Known fixture mismatch.",
        owner="test",
        tracking="plan:test-baseline-fix",
        review_by=date(2026, 11, 30),
        resolution="Update all fixture coordinates.",
        canonical_values=("1.35",),
        target_fingerprint=(
            repo_health.TargetFingerprint(
                ".github/workflows/ci.yml",
                "workflow-kubernetes-version",
                "1.34.0",
            ),
            repo_health.TargetFingerprint(
                "scripts/tasks.py",
                "python-constant:K8S_VERSION",
                "1.33.0",
            ),
        ),
    )
    rule = repo_health.Rule(
        "kubernetes-version",
        repo_health.Location("infra/main.bicep", "bicep-parameter:kubernetesVersion"),
        (
            repo_health.Location(
                ".github/workflows/ci.yml", "workflow-kubernetes-version"
            ),
            repo_health.Location("scripts/tasks.py", "python-constant:K8S_VERSION"),
        ),
        False,
        policy,
    )

    original = repo_health.run_checks(repository, [rule], date(2026, 8, 25))
    write(
        repository / "scripts/tasks.py",
        '# Unrelated line that moves the target coordinate.\nK8S_VERSION = "1.33.0"\n',
    )
    moved = repo_health.run_checks(repository, [rule], date(2026, 8, 25))
    write(
        repository / ".github/workflows/ci.yml",
        "steps:\n  - uses: actions/checkout@v4\n"
        "  - run: kubeconform -kubernetes-version 1.33.0\n",
    )
    write(repository / "scripts/tasks.py", 'K8S_VERSION = "1.34.0"\n')
    swapped = repo_health.run_checks(repository, [rule], date(2026, 8, 25))

    assert original[0].status == "excluded"
    assert moved[0].status == "excluded"
    assert swapped[0].status == "fail"
    assert "differs from the exception snapshot" in swapped[0].message


def test_exception_fingerprint_compares_repeated_targets_as_a_multiset(
    repository: Path,
) -> None:
    dockerfile = repository / "src/api/Dockerfile"
    write(
        dockerfile,
        "FROM python:3.14-slim\n"
        "FROM ghcr.io/astral-sh/uv:0.12.2\n"
        "FROM python:3.14-slim\n",
    )
    policy = repo_health.ExceptionPolicy(
        reason="Known fixture mismatch.",
        owner="test",
        tracking="plan:test-baseline-fix",
        review_by=date(2026, 11, 30),
        resolution="Pin every base image.",
        canonical_values=(
            "ghcr.io/astral-sh/uv:0.12.2",
            "python:3.14-slim",
            "python:3.14-slim",
        ),
        target_fingerprint=(
            repo_health.TargetFingerprint(
                "src/api/Dockerfile", "docker-from", "ghcr.io/astral-sh/uv:0.12.2"
            ),
            repo_health.TargetFingerprint(
                "src/api/Dockerfile", "docker-from", "python:3.14-slim"
            ),
            repo_health.TargetFingerprint(
                "src/api/Dockerfile", "docker-from", "python:3.14-slim"
            ),
        ),
    )
    rule = repo_health.Rule(
        "docker-base-digest",
        repo_health.Location("src/api/Dockerfile", "docker-from"),
        (repo_health.Location("src/api/Dockerfile", "docker-from"),),
        False,
        policy,
    )

    original = repo_health.run_checks(repository, [rule], date(2026, 8, 25))
    write(
        dockerfile,
        "FROM python:3.14-slim\n"
        "FROM python:3.14-slim\n"
        "FROM ghcr.io/astral-sh/uv:0.12.2\n",
    )
    reordered = repo_health.run_checks(repository, [rule], date(2026, 8, 25))
    write(
        dockerfile,
        "FROM python:3.14-slim\n"
        "FROM ghcr.io/astral-sh/uv:0.12.2\n"
        "FROM ghcr.io/astral-sh/uv:0.12.2\n",
    )
    changed_multiplicity = repo_health.run_checks(repository, [rule], date(2026, 8, 25))

    assert original[0].status == "excluded"
    assert reordered[0].status == "excluded"
    assert changed_multiplicity[0].status == "fail"
    assert "differs from the exception snapshot" in changed_multiplicity[0].message


def test_expired_exception_fails(repository: Path) -> None:
    rules = repo_health.load_rules(repository / ".github/repo-health.toml")

    checks = repo_health.run_checks(repository, rules, date(2026, 12, 1))

    assert checks[0].status == "fail"
    assert "expired" in checks[0].message


def test_resolved_exception_fails(repository: Path) -> None:
    write(
        repository / ".github/workflows/ci.yml",
        "steps:\n  - run: kubeconform -kubernetes-version 1.35.0\n",
    )
    rules = repo_health.load_rules(repository / ".github/repo-health.toml")

    checks = repo_health.run_checks(repository, rules, date(2026, 8, 25))

    assert checks[0].status == "fail"
    assert "exception remains" in checks[0].message


def test_incomplete_report_only_exception_is_rejected(repository: Path) -> None:
    write_config(repository, enforce=False, exception=False)

    with pytest.raises(repo_health.RepoHealthError, match="exception is required"):
        repo_health.load_rules(repository / ".github/repo-health.toml")


def test_enforced_rule_rejects_exception(repository: Path) -> None:
    write_config(repository, enforce=True, exception=True)

    with pytest.raises(
        repo_health.RepoHealthError, match="forbidden when enforce=true"
    ):
        repo_health.load_rules(repository / ".github/repo-health.toml")


def test_exception_review_by_rejects_local_datetime(repository: Path) -> None:
    write_config(
        repository,
        enforce=False,
        exception=True,
        review_by="2026-11-30T12:00:00",
    )

    with pytest.raises(repo_health.RepoHealthError, match="must be a TOML local date"):
        repo_health.load_rules(repository / ".github/repo-health.toml")


def test_configuration_rejects_embedded_regex(repository: Path) -> None:
    config_path = repository / ".github/repo-health.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'selector = "bicep-parameter:kubernetesVersion"',
            'selector = "bicep-parameter:kubernetesVersion"\nregex = "1\\\\.35"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(repo_health.RepoHealthError, match="unknown keys: regex"):
        repo_health.load_rules(config_path)


def test_configuration_rejects_legacy_schema_version(repository: Path) -> None:
    config_path = repository / ".github/repo-health.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "schema_version = 2", "schema_version = 1"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        repo_health.RepoHealthError, match="configuration schema_version must be 2"
    ):
        repo_health.load_rules(config_path)


def test_configuration_rejects_fingerprint_location(repository: Path) -> None:
    config_path = repository / ".github/repo-health.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'selector = "workflow-kubernetes-version", value = "1.34.0"',
            'selector = "workflow-kubernetes-version", '
            'location = "line:3:kubernetes-version", value = "1.34.0"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(repo_health.RepoHealthError, match="unknown keys: location"):
        repo_health.load_rules(config_path)


def test_inventory_extracts_each_kubernetes_yaml_document(repository: Path) -> None:
    manifest = repository / "k8s/apps/app/deployment.yaml"
    write(
        manifest,
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "data:\n"
        '  quoted: "first line\n'
        "    --- # quoted scalar content\n"
        '    last line"\n'
        "  block: |\n"
        "    --- # block scalar content\n"
        "# --- is only a comment\n"
        "--- \t# document separator\n"
        "apiVersion: v1\n"
        "kind: Service\n",
    )

    scan = repo_health.scan_inventory(
        repository, repo_health.list_tracked_files(repository)
    )
    coordinates = [
        coordinate
        for coordinate in scan.coordinates
        if coordinate.path == "k8s/apps/app/deployment.yaml"
        and coordinate.category == "kubernetes-manifest"
    ]

    assert [(item.location, item.value) for item in coordinates] == [
        ("document:1", "apps/v1/Deployment"),
        ("document:2", "v1/Service"),
    ]


def test_yaml_document_number_ignores_anchored_and_tagged_block_scalars(
    repository: Path,
) -> None:
    manifest = repository / "k8s/apps/app/deployment.yaml"
    write(
        manifest,
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "data:\n"
        "  anchored: &script |\n"
        "    ---\n"
        "  tagged: !!str >-\n"
        "    ---\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: Service\n",
    )

    scan = repo_health.scan_inventory(
        repository, repo_health.list_tracked_files(repository)
    )
    coordinates = [
        coordinate
        for coordinate in scan.coordinates
        if coordinate.path == "k8s/apps/app/deployment.yaml"
        and coordinate.category == "kubernetes-manifest"
    ]

    assert [(item.location, item.value) for item in coordinates] == [
        ("document:1", "apps/v1/Deployment"),
        ("document:2", "v1/Service"),
    ]


def test_python_version_coordinate_marks_file_as_recognized(repository: Path) -> None:
    tracked = repo_health.list_tracked_files(repository)
    scan = repo_health.scan_inventory(repository, tracked)
    result = repo_health.build_result(repository, include_checks=False)

    assert "scripts/tasks.py" in scan.recognized
    assert "scripts/tasks.py" not in result["coverage"]["unsupported_files"]["paths"]


def test_json_is_deterministic_and_scan_does_not_modify_files(
    repository: Path,
) -> None:
    before = {
        path: (repository / path).read_bytes()
        for path in repo_health.list_tracked_files(repository)
    }

    first = repo_health.json_output(
        repo_health.build_result(repository, include_checks=True)
    )
    second = repo_health.json_output(
        repo_health.build_result(repository, include_checks=True)
    )
    after = {
        path: (repository / path).read_bytes()
        for path in repo_health.list_tracked_files(repository)
    }

    assert first == second
    assert before == after
    parsed = json.loads(first)
    assert list(parsed) == sorted(parsed)
    assert parsed["schema_version"] == "2.0"
    assert {
        "schema_version",
        "repository_root",
        "inventory",
        "checks",
        "coverage",
        "findings",
        "environment_limitations",
    } <= parsed.keys()


def test_cli_exit_codes(repository: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        repo_health.main(["--root", str(repository), "inventory", "--format", "json"])
        == 0
    )
    inventory_output = json.loads(capsys.readouterr().out)
    assert inventory_output["checks"] == []

    assert (
        repo_health.main(["--root", str(repository), "check", "--format", "json"]) == 0
    )
    check_output = json.loads(capsys.readouterr().out)
    assert check_output["checks"][0]["status"] == "excluded"

    write_config(repository, enforce=True, exception=False)
    assert (
        repo_health.main(["--root", str(repository), "check", "--format", "text"]) == 1
    )
    assert "error: [fail]" in capsys.readouterr().out


def test_extraction_failure_is_explicit_and_fails_check(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repository / "pyproject.toml", "[tool.invalid\n")

    assert (
        repo_health.main(["--root", str(repository), "inventory", "--format", "text"])
        == 0
    )
    inventory_output = capsys.readouterr().out
    assert "[unverified] inventory-extraction" in inventory_output

    assert (
        repo_health.main(["--root", str(repository), "check", "--format", "text"]) == 1
    )
    check_text = capsys.readouterr().out
    assert "error: [fail] inventory-extraction" in check_text

    assert (
        repo_health.main(["--root", str(repository), "check", "--format", "json"]) == 1
    )
    check_output = json.loads(capsys.readouterr().out)
    extraction_findings = [
        finding
        for finding in check_output["findings"]
        if finding["rule_id"] == "inventory-extraction"
    ]
    assert extraction_findings[0]["status"] == "fail"
    assert check_output["coverage"]["extraction_failures"]


def test_deleted_tracked_file_is_an_explicit_extraction_failure(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repository / "scripts/tasks.py").unlink()

    assert (
        repo_health.main(["--root", str(repository), "inventory", "--format", "json"])
        == 0
    )
    inventory_output = json.loads(capsys.readouterr().out)
    assert inventory_output["coverage"]["extraction_failures"] == [
        {
            "path": "scripts/tasks.py",
            "reason": "tracked file does not exist: scripts/tasks.py",
        }
    ]
    inventory_findings = [
        finding
        for finding in inventory_output["findings"]
        if finding["rule_id"] == "inventory-extraction"
    ]
    assert inventory_findings[0]["status"] == "unverified"

    assert (
        repo_health.main(["--root", str(repository), "check", "--format", "json"]) == 1
    )
    captured = capsys.readouterr()
    check_output = json.loads(captured.out)
    extraction_findings = [
        finding
        for finding in check_output["findings"]
        if finding["rule_id"] == "inventory-extraction"
    ]
    assert extraction_findings[0]["status"] == "fail"
    assert "Traceback" not in captured.err


def test_report_reads_saved_json_without_scanning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report: dict[str, object] = {
        "schema_version": "2.0",
        "repository_root": str(tmp_path),
        "inventory": [],
        "checks": [],
        "coverage": {
            "tracked_files": 0,
            "recognized_files": 0,
            "inventory_coordinates": 0,
        },
        "findings": [],
        "environment_limitations": [],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    def fail_scan(*_args: object, **_kwargs: object) -> dict[str, object]:
        pytest.fail("report must not scan the repository")

    monkeypatch.setattr(repo_health, "build_result", fail_scan)

    assert repo_health.main(["report", str(report_path)]) == 0
    assert "ok: inventory 0 coordinates" in capsys.readouterr().out

    report_path.write_text("{}", encoding="utf-8")
    assert repo_health.main(["report", str(report_path)]) == 1
    assert "error: report input is missing keys" in capsys.readouterr().err


def test_report_rejects_legacy_schema_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report: dict[str, object] = {
        "schema_version": "1.0",
        "repository_root": str(tmp_path),
        "inventory": [],
        "checks": [],
        "coverage": {"tracked_files": 0, "recognized_files": 0},
        "findings": [],
        "environment_limitations": [],
    }
    report_path = tmp_path / "legacy-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert repo_health.main(["report", str(report_path)]) == 1
    error = capsys.readouterr().err
    assert "report input schema_version must be 2.0" in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    ("section", "value", "message"),
    [
        ("checks", ["not-an-object"], r"checks\[0\] must be an object"),
        (
            "findings",
            [{"status": "fail"}],
            r"findings\[0\]\.rule_id must be a str",
        ),
        (
            "environment_limitations",
            [{"area": "network", "status": ["excluded"], "reason": "offline"}],
            r"environment_limitations\[0\]\.status must be a str",
        ),
    ],
)
def test_report_rejects_invalid_nested_schema_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    section: str,
    value: object,
    message: str,
) -> None:
    report: dict[str, object] = {
        "schema_version": "2.0",
        "repository_root": str(tmp_path),
        "inventory": [],
        "checks": [],
        "coverage": {"tracked_files": 0, "recognized_files": 0},
        "findings": [],
        "environment_limitations": [],
    }
    report[section] = value
    report_path = tmp_path / "invalid-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert repo_health.main(["report", str(report_path)]) == 1
    error = capsys.readouterr().err
    assert "Traceback" not in error
    assert re.search(message, error)


def test_git_failure_has_clear_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(repo_health.RepoHealthError, match="git ls-files"):
        repo_health.list_tracked_files(tmp_path)
