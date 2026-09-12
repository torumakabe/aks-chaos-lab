from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
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


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git = shutil.which("git")
    if git is None:
        pytest.fail("git is required", pytrace=False)
    write(
        tmp_path / ".github/repo-health.toml",
        'kubernetes_schema_excluded_kinds = ["Kustomization"]\n',
    )
    write(
        tmp_path / ".github/workflows/copilot-setup-steps.yml",
        "with:\n  version: v0.88.7\n",
    )
    write(
        tmp_path / ".github/workflows/aks-updates-analyzer.lock.yml",
        '# gh-aw-metadata: {"compiler_version":"v0.88.7"}\n',
    )
    write(
        tmp_path / "infra/main.bicep",
        "param kubernetesVersion string = '1.35'\n"
        "resource cluster 'Microsoft.ContainerService/managedClusters@2025-07-01' = {}\n",
    )
    write(tmp_path / "infra/main.parameters.json", '{"parameters":{}}\n')
    write(tmp_path / "infra/sli/main.bicep", "param environment string\n")
    write(
        tmp_path / "infra/sli/main.parameters.json",
        '{"parameters":{"environment":{"value":"test"}}}\n',
    )
    write(tmp_path / "scripts/tasks.py", 'K8S_VERSION = "1.35"\n')
    write(
        tmp_path / "src/api/Dockerfile",
        "FROM python:3.14-slim@sha256:abc\n",
    )
    write(
        tmp_path / "src/external-sli-publisher/host.json",
        '{"extensionBundle":{"version":"[4.*, 5.0.0)"}}\n',
    )
    write(
        tmp_path / "k8s/app.yaml",
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n",
    )
    write(tmp_path / "README.md", "See https://example.com/docs.\n")
    subprocess.run([git, "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run([git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            git,
            "-c",
            "user.name=Tests",
            "-c",
            "user.email=tests@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_inventory_contains_only_review_inputs(repository: Path) -> None:
    result = repo_health.build_result(repository, include_checks=False)
    categories = {item["category"] for item in result["inventory"]}

    assert categories == {
        "bicep-resource-api",
        "docker-base-image",
        "documentation-external-link",
        "function-extension-bundle",
        "kubernetes-manifest",
    }
    assert result["checks"] == []
    assert result["findings"] == []
    assert result["coverage"]["excluded_kubernetes_documents"][0]["value"] == (
        "Kustomization"
    )


def test_check_validates_three_repository_invariants(repository: Path) -> None:
    result = repo_health.build_result(repository, include_checks=True)

    assert [(item["id"], item["status"]) for item in result["checks"]] == [
        ("kubernetes-version", "pass"),
        ("gh-aw-compiler-version", "pass"),
        ("docker-base-digest", "pass"),
    ]
    assert result["findings"] == []


def test_check_reports_version_and_digest_mismatches(repository: Path) -> None:
    write(repository / "scripts/tasks.py", 'K8S_VERSION = "1.34"\n')
    write(repository / "src/api/Dockerfile", "FROM python:3.14-slim\n")

    result = repo_health.build_result(repository, include_checks=True)

    failed = {item["rule_id"] for item in result["findings"]}
    assert failed == {"kubernetes-version", "docker-base-digest"}


def test_bicep_parameters_reject_unknown_and_missing_values(repository: Path) -> None:
    write(
        repository / "infra/sli/main.parameters.json",
        '{"parameters":{"unexpected":{"value":"test"}}}\n',
    )

    findings = repo_health.validate_bicep_parameter_files(repository)

    assert {(item.location, item.message) for item in findings} == {
        (
            "environment",
            "required parameter from infra/sli/main.bicep is missing",
        ),
        (
            "unexpected",
            "parameter is not declared by infra/sli/main.bicep",
        ),
    }


def test_exclusion_config_rejects_duplicates(repository: Path) -> None:
    config = repository / ".github/repo-health.toml"
    write(config, 'kubernetes_schema_excluded_kinds = ["Gateway", "Gateway"]\n')

    with pytest.raises(repo_health.RepoHealthError, match="duplicate"):
        repo_health.load_kubernetes_schema_excluded_kinds(config)


def test_inventory_includes_untracked_files(repository: Path) -> None:
    write(repository / "docs/new.md", "https://example.com/new\n")

    result = repo_health.build_result(repository, include_checks=False)

    assert any(item["path"] == "docs/new.md" for item in result["inventory"])


def test_json_output_is_deterministic(repository: Path) -> None:
    first = repo_health.json_output(
        repo_health.build_result(repository, include_checks=True)
    )
    second = repo_health.json_output(
        repo_health.build_result(repository, include_checks=True)
    )

    assert first == second
    assert json.loads(first)["schema_version"] == "2.1"


def test_report_reads_saved_json_without_scanning(
    repository: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = repo_health.build_result(repository, include_checks=False)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert repo_health.main(["report", str(report_path)]) == 0
    assert "ok: inventory" in capsys.readouterr().out


def test_report_rejects_invalid_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text('{"schema_version":"1.0"}', encoding="utf-8")

    assert repo_health.main(["report", str(report_path)]) == 1
    assert "missing keys" in capsys.readouterr().err


def test_validate_bicep_parameters_cli(repository: Path) -> None:
    assert (
        repo_health.main(["--root", str(repository), "validate-bicep-parameters"]) == 0
    )


def test_git_failure_has_clear_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(repo_health.RepoHealthError, match="git ls-files"):
        repo_health.list_repository_files(tmp_path)
