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


def test_check_reports_kubernetes_exclusions_without_inventory(
    repository: Path,
) -> None:
    result = repo_health.build_result(repository)

    assert "inventory" not in result
    assert result["coverage"]["excluded_kubernetes_documents"] == [
        {
            "path": "k8s/app.yaml",
            "location": "document:1",
            "kind": "Kustomization",
            "reason": "kind excluded from Kubernetes schema validation",
        }
    ]


def test_check_validates_three_repository_invariants(repository: Path) -> None:
    result = repo_health.build_result(repository)

    assert [(item["id"], item["status"]) for item in result["checks"]] == [
        ("kubernetes-version", "pass"),
        ("gh-aw-compiler-version", "pass"),
        ("docker-base-digest", "pass"),
    ]
    assert result["findings"] == []


@pytest.mark.parametrize("read_failure", ["invalid-encoding", "permission-denied"])
def test_kubernetes_read_failure_preserves_other_results(
    repository: Path,
    read_failure: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unreadable = repository / "k8s/broken.yaml"
    write(unreadable, "kind: Kustomization\n")
    write(repository / "k8s/z-valid.yaml", "kind: Kustomization\n")
    write(repository / "infra/sli/main.parameters.json", '{"parameters":{}}\n')
    if read_failure == "invalid-encoding":
        unreadable.write_bytes(b"kind: \xff\n")
    else:
        original_read_text = Path.read_text

        def read_text(path: Path, *, encoding: str | None = None) -> str:
            if path == unreadable:
                raise PermissionError(13, "Permission denied", str(path))
            return original_read_text(path, encoding=encoding)

        monkeypatch.setattr(Path, "read_text", read_text)

    assert (
        repo_health.main(["--root", str(repository), "check", "--format", "json"]) == 1
    )
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert [(item["id"], item["status"]) for item in result["checks"]] == [
        ("kubernetes-version", "pass"),
        ("gh-aw-compiler-version", "pass"),
        ("docker-base-digest", "pass"),
    ]
    assert {item["rule_id"] for item in result["findings"]} == {
        "bicep-parameters",
        "kubernetes-document-read",
    }
    read_findings = [
        item
        for item in result["findings"]
        if item["rule_id"] == "kubernetes-document-read"
    ]
    assert len(read_findings) == 1
    assert read_findings[0]["status"] == "fail"
    assert read_findings[0]["path"] == "k8s/broken.yaml"
    assert "could not read Kubernetes document" in read_findings[0]["message"]
    assert {
        item["path"] for item in result["coverage"]["excluded_kubernetes_documents"]
    } == {"k8s/app.yaml", "k8s/z-valid.yaml"}

    text = repo_health.text_output(result)
    assert "[fail] kubernetes-document-read: k8s/broken.yaml:file" in text
    assert "[excluded] k8s/z-valid.yaml:document:1" in text
    assert "ok: repository consistency checks passed" not in text


def test_check_reports_version_and_digest_mismatches(repository: Path) -> None:
    write(repository / "scripts/tasks.py", 'K8S_VERSION = "1.34"\n')
    write(repository / "src/api/Dockerfile", "FROM python:3.14-slim\n")

    result = repo_health.build_result(repository)

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


def test_check_includes_untracked_dockerfiles_and_kubernetes_documents(
    repository: Path,
) -> None:
    write(repository / "new/Dockerfile", "FROM example:latest\n")
    write(repository / "k8s/new.yaml", "kind: Kustomization\n")

    result = repo_health.build_result(repository)

    assert any(item["rule_id"] == "docker-base-digest" for item in result["findings"])
    assert any(
        item["path"] == "k8s/new.yaml"
        for item in result["coverage"]["excluded_kubernetes_documents"]
    )


def test_check_does_not_read_specialist_review_inputs(repository: Path) -> None:
    (repository / "README.md").write_bytes(b"\xff")
    write(repository / "src/external-sli-publisher/host.json", "{invalid")
    write(
        repository / "infra/main.bicep",
        "param kubernetesVersion string = '1.35'\n"
        "// resource commented 'Microsoft.Example/type@2000-01-01' = {}\n"
        "resource unsupported\n",
    )

    result = repo_health.build_result(repository)

    assert result["findings"] == []
    assert all(item["status"] == "pass" for item in result["checks"])


def test_unreadable_dockerfile_is_not_reported_as_digest_pinned(
    repository: Path,
) -> None:
    (repository / "src/api/Dockerfile").write_bytes(b"\xff")

    result = repo_health.build_result(repository)

    assert [(item["id"], item["status"]) for item in result["checks"]][-1] == (
        "docker-base-digest",
        "fail",
    )


def test_missing_base_images_has_explicit_failure(repository: Path) -> None:
    write(repository / "src/api/Dockerfile", "# No FROM declaration\n")

    result = repo_health.build_result(repository)

    assert result["checks"][-1] == {
        "id": "docker-base-digest",
        "status": "fail",
        "message": "no Docker base images found",
    }


def test_json_output_is_deterministic(repository: Path) -> None:
    first = repo_health.json_output(repo_health.build_result(repository))
    second = repo_health.json_output(repo_health.build_result(repository))

    assert first == second
    assert json.loads(first)["schema_version"] == "3.0"


def test_report_reads_saved_json_without_scanning(
    repository: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = repo_health.build_result(repository)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert repo_health.main(["report", str(report_path)]) == 0
    assert "ok: repository consistency checks passed" in capsys.readouterr().out


def test_inventory_cli_is_not_available(repository: Path) -> None:
    with pytest.raises(SystemExit) as error:
        repo_health.main(["--root", str(repository), "inventory"])
    assert error.value.code == 2


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
