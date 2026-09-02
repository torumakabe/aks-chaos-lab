from __future__ import annotations

import email.message
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tomllib
import urllib.error
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def load_tasks() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "review_repository_tasks", SCRIPTS_DIR / "tasks.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/tasks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tasks = load_tasks()
real_probe_review_tool = tasks.probe_review_tool


@pytest.fixture(autouse=True)
def assume_review_tools_pass_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "probe_review_tool", lambda _tool: None)


FAST_CHECK_NAMES = tuple(check.name for check in tasks.FAST_REVIEW_CHECKS)
FAST_TARGET_CALLS = tuple(check.target_name for check in tasks.FAST_REVIEW_CHECKS)


def review_command_stub(
    calls: list[str],
    handler: Callable[
        [list[str] | tuple[str, ...], str],
        subprocess.CompletedProcess[bytes] | None,
    ]
    | None = None,
) -> Callable[..., subprocess.CompletedProcess[bytes]]:
    """Build a run_isolated_review_command stub that records each target."""

    def run_check(
        args: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        target = args[2]
        calls.append(target)
        if handler is not None:
            outcome = handler(args, target)
            if outcome is not None:
                return outcome
        return subprocess.CompletedProcess(args, 0)

    return run_check


def test_review_targets_are_registered() -> None:
    assert tasks.TARGETS["review-repo-fast"] is tasks.target_review_repo_fast
    assert tasks.TARGETS["review-repo-full"] is tasks.target_review_repo_full
    assert (
        tasks.TARGETS["validate-bicep-parameters"]
        is tasks.target_validate_bicep_parameters
    )
    assert tasks.TARGETS["validate-helm-values"] is tasks.target_validate_helm_values


def test_kubernetes_validation_includes_nested_yaml_and_yml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "k8s/nested").mkdir(parents=True)
    (repository / "k8s/root.yaml").write_text("kind: Namespace\n", encoding="utf-8")
    (repository / "k8s/nested/resource.yml").write_text(
        "kind: ConfigMap\n", encoding="utf-8"
    )
    (repository / "k8s/ignored.txt").write_text("ignored\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "target_check_docker", lambda: None)
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: (
            calls.append(tuple(args)) or subprocess.CompletedProcess(args, 0)
        ),
    )

    tasks.target_lint_k8s()

    assert calls[0][-2:] == (
        "k8s/nested/resource.yml",
        "k8s/root.yaml",
    )
    assert "-skip" in calls[0]
    assert tasks.KUBECONFORM_SKIP in calls[0]


def test_helm_values_validation_uses_pinned_chart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    values = repository / tasks.CHAOS_MESH_VALUES
    values.parent.mkdir(parents=True)
    values.write_text("chaosDaemon:\n  runtime: containerd\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "require_command", lambda _command: None)
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **kwargs: (
            calls.append((tuple(args), kwargs)) or subprocess.CompletedProcess(args, 0)
        ),
    )

    tasks.target_validate_helm_values()

    assert calls[0][0] == (
        "helm",
        "repo",
        "add",
        "chaos-mesh",
        tasks.CHAOS_MESH_REPOSITORY,
    )
    command, render_options = calls[1]
    assert command[:4] == (
        "helm",
        "template",
        "chaos-mesh",
        "chaos-mesh/chaos-mesh",
    )
    assert tasks.CHAOS_MESH_CHART_VERSION in command
    assert str(values) in command
    assert render_options["timeout"] == 180
    assert render_options["env"] == calls[0][1]["env"]


def test_chaos_mesh_chart_version_is_read_from_azure_yaml(tmp_path: Path) -> None:
    azure_yaml = tmp_path / "azure.yaml"
    azure_yaml.write_text(
        "releases:\n"
        "  - name: chaos-mesh\n"
        "    chart: chaos-mesh/chaos-mesh\n"
        "    version: 9.9.9\n"
        "    values: infra/helm/chaos-mesh-values.yaml\n",
        encoding="utf-8",
    )

    assert tasks.read_chaos_mesh_chart_version(azure_yaml) == "9.9.9"


def test_chaos_mesh_chart_version_rejects_ambiguous_azure_yaml(tmp_path: Path) -> None:
    azure_yaml = tmp_path / "azure.yaml"
    azure_yaml.write_text(
        "releases:\n"
        "  - name: chaos-mesh\n"
        "    chart: chaos-mesh/chaos-mesh\n"
        "    version: 1.0.0\n"
        "  - name: chaos-mesh-second\n"
        "    chart: chaos-mesh/chaos-mesh\n"
        "    version: 2.0.0\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        tasks.read_chaos_mesh_chart_version(azure_yaml)

    assert error.value.code == 1


class _FakeChecksumResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeChecksumResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_fetch_lefthook_checksum_returns_the_matching_asset_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_checksum = "a" * 64
    expected_checksum = "b" * 64
    payload = (
        f"{other_checksum}  lefthook_9.9.9_Freebsd_arm64.gz\n"
        f"{expected_checksum}  lefthook_9.9.9_Linux_x86_64.gz\n"
    ).encode()
    monkeypatch.setattr(
        tasks.urllib.request,
        "urlopen",
        lambda _url, timeout=None: _FakeChecksumResponse(payload),
    )

    checksum = tasks.fetch_lefthook_checksum("9.9.9")

    assert checksum == expected_checksum


def test_fetch_lefthook_checksum_rejects_ambiguous_asset_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        f"{'a' * 64}  lefthook_9.9.9_Linux_x86_64.gz\n"
        f"{'c' * 64}  lefthook_9.9.9_Linux_x86_64.gz\n"
    ).encode()
    monkeypatch.setattr(
        tasks.urllib.request,
        "urlopen",
        lambda _url, timeout=None: _FakeChecksumResponse(payload),
    )

    with pytest.raises(tasks.LefthookChecksumUnavailableError):
        tasks.fetch_lefthook_checksum("9.9.9")


def test_fetch_lefthook_checksum_raises_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_network_error(_url: str, timeout: float | None = None) -> None:
        raise tasks.urllib.error.URLError("simulated DNS failure")

    monkeypatch.setattr(tasks.urllib.request, "urlopen", raise_network_error)

    with pytest.raises(tasks.LefthookChecksumUnavailableError, match="network"):
        tasks.fetch_lefthook_checksum("9.9.9")


def _write_ci_workflow(repository: Path, version: str, checksum: str) -> Path:
    workflow_path = repository / tasks.LEFTHOOK_CI_WORKFLOW
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        "      - name: Install Lefthook\n"
        "        run: |\n"
        f"          LEFTHOOK_VERSION={version}\n"
        f"          LEFTHOOK_SHA256={checksum}\n",
        encoding="utf-8",
    )
    return workflow_path


def test_evaluate_lefthook_pin_reports_update_available_when_release_is_newer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_ci_workflow(repository, "2.1.10", "a" * 64)
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", lambda _version: "a" * 64)
    monkeypatch.setattr(tasks, "fetch_lefthook_latest_release", lambda: "2.1.12")

    finding = tasks.evaluate_lefthook_pin()

    assert finding.status == "unverified"
    assert finding.reason_code == tasks.FRESHNESS_REASON_UPDATE_AVAILABLE
    assert finding.current == "2.1.10"
    assert finding.published == "2.1.12"
    assert "requires maintainer review before updating" in finding.detail


def test_evaluate_lefthook_pin_fails_on_checksum_mismatch_before_release_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_ci_workflow(repository, "2.1.10", "a" * 64)
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", lambda _version: "b" * 64)

    def _fail_release() -> str:
        raise AssertionError("must not query the latest release on a mismatch")

    monkeypatch.setattr(tasks, "fetch_lefthook_latest_release", _fail_release)

    finding = tasks.evaluate_lefthook_pin()

    assert finding.status == "fail"
    assert finding.reason_code == tasks.FRESHNESS_REASON_CHECKSUM_MISMATCH


def test_evaluate_lefthook_pin_marks_missing_checksum_evidence_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_ci_workflow(repository, "2.1.10", "a" * 64)
    monkeypatch.setattr(tasks, "ROOT", repository)

    def _raise(_version: str) -> str:
        raise tasks.LefthookChecksumUnavailableError("network down")

    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", _raise)

    finding = tasks.evaluate_lefthook_pin()

    assert finding.status == "unverified"
    assert finding.reason_code == tasks.FRESHNESS_REASON_EVIDENCE_UNAVAILABLE
    assert "official Lefthook freshness evidence was unavailable" in finding.detail


def test_evaluate_lefthook_pin_marks_missing_release_evidence_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_ci_workflow(repository, "2.1.10", "a" * 64)
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", lambda _version: "a" * 64)

    def _raise() -> str:
        raise tasks.LefthookReleaseUnavailableError("network down")

    monkeypatch.setattr(tasks, "fetch_lefthook_latest_release", _raise)

    finding = tasks.evaluate_lefthook_pin()

    assert finding.status == "unverified"
    assert finding.reason_code == tasks.FRESHNESS_REASON_EVIDENCE_UNAVAILABLE


def test_evaluate_lefthook_pin_passes_when_current_and_checksum_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_ci_workflow(repository, "2.1.12", "a" * 64)
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", lambda _version: "a" * 64)
    monkeypatch.setattr(tasks, "fetch_lefthook_latest_release", lambda: "2.1.12")

    finding = tasks.evaluate_lefthook_pin()

    assert finding.status == "pass"
    assert finding.reason_code == tasks.FRESHNESS_REASON_CURRENT


def test_fetch_lefthook_latest_release_strips_v_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "open_github_url",
        lambda _url, _timeout: _FakeChecksumResponse(b'{"tag_name": "v2.1.12"}'),
    )

    assert tasks.fetch_lefthook_latest_release() == "2.1.12"


def test_update_lefthook_pin_rewrites_version_and_checksum_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    workflow_path = _write_ci_workflow(repository, "1.0.0", "a" * 64)
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", lambda _version: "c" * 64)

    tasks.target_update_lefthook_pin("2.0.0")

    updated = workflow_path.read_text(encoding="utf-8")
    assert "          LEFTHOOK_VERSION=2.0.0\n" in updated
    assert f"          LEFTHOOK_SHA256={'c' * 64}\n" in updated
    assert "LEFTHOOK_VERSION=1.0.0" not in updated
    assert f"LEFTHOOK_SHA256={'a' * 64}" not in updated


def test_update_lefthook_pin_preserves_original_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    workflow_path = _write_ci_workflow(repository, "1.0.0", "a" * 64)
    original = workflow_path.read_bytes()
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", lambda _version: "c" * 64)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(tasks.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        tasks.target_update_lefthook_pin("2.0.0")

    assert workflow_path.read_bytes() == original
    assert list(workflow_path.parent.glob(f".{workflow_path.name}.*.tmp")) == []


def test_update_lefthook_pin_keeps_the_real_workflow_parseable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Apply the update to a copy of the real ci.yml and re-parse the result.

    The pin patterns anchor on the whole line, so a whole-match substitution
    silently deleted the run-step indentation and produced a workflow YAML
    parsers reject. Asserting only on the rewritten line would not have caught
    that, so this drives the real target file through the real target and then
    parses it with the same YAML parser the repository's other workflow tests
    use.
    """
    repository = tmp_path / "repository"
    workflow_path = repository / tasks.LEFTHOOK_CI_WORKFLOW
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = (REPO_ROOT / tasks.LEFTHOOK_CI_WORKFLOW).read_bytes()
    workflow_path.write_bytes(original_bytes)
    original = workflow_path.read_text(encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "fetch_lefthook_checksum", lambda _version: "d" * 64)

    tasks.target_update_lefthook_pin("9.8.7")

    updated_bytes = workflow_path.read_bytes()
    updated = workflow_path.read_text(encoding="utf-8")
    document = yaml.safe_load(updated)
    assert isinstance(document, dict)
    assert "jobs" in document
    version_line = next(
        line for line in updated.splitlines() if "LEFTHOOK_VERSION=" in line
    )
    checksum_line = next(
        line for line in updated.splitlines() if "LEFTHOOK_SHA256=" in line
    )
    original_version_line = next(
        line for line in original.splitlines() if "LEFTHOOK_VERSION=" in line
    )
    original_checksum_line = next(
        line for line in original.splitlines() if "LEFTHOOK_SHA256=" in line
    )
    indent = len(original_version_line) - len(original_version_line.lstrip())
    assert indent > 0
    assert version_line == " " * indent + "LEFTHOOK_VERSION=9.8.7"
    assert checksum_line == " " * indent + f"LEFTHOOK_SHA256={'d' * 64}"
    # Only the two pinned values may change; every other byte, including the
    # file's line endings, has to survive the rewrite untouched.
    assert updated_bytes.count(b"\r\n") == original_bytes.count(b"\r\n")
    assert len(updated.splitlines()) == len(original.splitlines())
    assert [
        line
        for line in updated.splitlines()
        if line not in {version_line, checksum_line}
    ] == [
        line
        for line in original.splitlines()
        if line not in {original_version_line, original_checksum_line}
    ]


def test_update_lefthook_pin_rejects_malformed_version_without_network_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_ci_workflow(repository, "1.0.0", "a" * 64)
    monkeypatch.setattr(tasks, "ROOT", repository)
    fetch_calls: list[str] = []
    monkeypatch.setattr(
        tasks,
        "fetch_lefthook_checksum",
        lambda version: fetch_calls.append(version) or "c" * 64,
    )

    with pytest.raises(SystemExit) as error:
        tasks.target_update_lefthook_pin("v2.0.0")

    assert error.value.code == 1
    assert fetch_calls == []


def test_renovate_config_matches_its_own_declared_contract() -> None:
    assert tasks.renovate_contract_violations(tasks.load_renovate_config()) == []


def _mutated_renovate_config(**overrides: object) -> dict[str, Any]:
    mutated = cast(dict[str, Any], json.loads(json.dumps(tasks.load_renovate_config())))
    mutated.update(overrides)
    return mutated


def test_renovate_covers_every_scheduled_version_update_ecosystem() -> None:
    """Renovate is the only scheduled version update mechanism.

    The built-in managers own the Python workspace, GitHub Actions, and
    Dockerfiles; the custom managers own every coordinate a built-in manager
    cannot read. Together they must cover the whole agreed target list.
    """
    config = tasks.load_renovate_config()

    assert config["enabledManagers"] == [
        "pep621",
        "github-actions",
        "dockerfile",
        "custom.regex",
    ]
    assert {item.dep_name_template for item in tasks.RENOVATE_MANAGER_EXPECTATIONS} == {
        "chaos-mesh",
        "rhysd/actionlint",
        "ghcr.io/yannh/kubeconform",
        "renovate/renovate",
        "Azure/bicep",
        "Azure/azure-dev",
        "uv",
    }


def test_renovate_config_contract_rejects_automerge() -> None:
    violations = tasks.renovate_contract_violations(
        _mutated_renovate_config(automerge=True)
    )

    assert any("automerge" in violation for violation in violations)


def test_renovate_config_contract_rejects_a_different_pr_hourly_limit() -> None:
    violations = tasks.renovate_contract_violations(
        _mutated_renovate_config(prHourlyLimit=2)
    )

    assert any("prHourlyLimit" in violation for violation in violations)


def test_renovate_config_contract_requires_an_explicit_pr_hourly_limit() -> None:
    config = tasks.load_renovate_config()
    del config["prHourlyLimit"]

    violations = tasks.renovate_contract_violations(config)

    assert any("prHourlyLimit" in violation for violation in violations)


def test_renovate_config_contract_rejects_a_disabled_manager() -> None:
    violations = tasks.renovate_contract_violations(
        _mutated_renovate_config(enabledManagers=["custom.regex"])
    )

    assert any("enabledManagers" in violation for violation in violations)


def test_renovate_config_contract_requires_the_dependency_dashboard() -> None:
    violations = tasks.renovate_contract_violations(
        _mutated_renovate_config(dependencyDashboard=False)
    )

    assert any("dependencyDashboard" in violation for violation in violations)


def test_renovate_config_contract_rejects_global_dashboard_approval() -> None:
    violations = tasks.renovate_contract_violations(
        _mutated_renovate_config(dependencyDashboardApproval=True)
    )

    assert any("dependencyDashboardApproval" in violation for violation in violations)


def test_renovate_config_contract_requires_explicit_dashboard_approval_default() -> (
    None
):
    config = tasks.load_renovate_config()
    del config["dependencyDashboardApproval"]

    violations = tasks.renovate_contract_violations(config)

    assert any("dependencyDashboardApproval" in violation for violation in violations)


def test_renovate_config_contract_requires_gh_aw_generated_paths_to_be_ignored() -> (
    None
):
    violations = tasks.renovate_contract_violations(
        _mutated_renovate_config(ignorePaths=[".github/aw/**"])
    )

    assert any("ignorePaths" in violation for violation in violations)


def test_renovate_config_contract_rejects_a_missing_package_rule() -> None:
    config = tasks.load_renovate_config()
    rules = [
        rule
        for rule in config["packageRules"]
        if rule["description"] != "uv-single-pull-request"
    ]

    violations = tasks.renovate_contract_violations(
        _mutated_renovate_config(packageRules=rules)
    )

    assert any("uv-single-pull-request" in violation for violation in violations)


def test_renovate_config_contract_rejects_a_corrupted_custom_manager() -> None:
    config = cast(dict[str, Any], json.loads(json.dumps(tasks.load_renovate_config())))
    for manager in config["customManagers"]:
        if manager["description"] == "bicep-cli-version":
            manager["datasourceTemplate"] = "docker"

    violations = tasks.renovate_contract_violations(config)

    assert any("bicep-cli-version" in violation for violation in violations)


def test_renovate_custom_managers_never_duplicate_a_builtin_manager() -> None:
    """No custom manager may re-extract a file a built-in manager reads."""
    for expectation in tasks.RENOVATE_MANAGER_EXPECTATIONS:
        assert not expectation.target_path.endswith("Dockerfile")
        assert not expectation.target_path.startswith("src/")
    assert tasks._renovate_manager_overlap_violations() == []


def test_uv_pin_coordinates_move_in_one_pull_request() -> None:
    """The uv lower bound and the Docker uv image must land together.

    Renovate groups both coordinates, and CI runs check-uv-version so a pull
    request that moved only one of them fails instead of merging a half-applied
    pin.
    """
    config = tasks.load_renovate_config()
    group = next(
        rule
        for rule in config["packageRules"]
        if rule["description"] == "uv-single-pull-request"
    )

    assert group["groupName"] == "uv"
    assert set(group["matchPackageNames"]) == {"uv", "ghcr.io/astral-sh/uv"}
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'scripts/tasks.py" check-uv-version' in ci
    assert 'scripts/tasks.py" check-version-pins' in ci


def test_python_dependency_updates_stay_candidate_detection_only() -> None:
    """Renovate must not regenerate uv.lock; refresh-uv-lock.yml owns that."""
    config = tasks.load_renovate_config()
    assert config["dependencyDashboardApproval"] is False
    rule = next(
        item
        for item in config["packageRules"]
        if item["description"] == "python-workspace-candidate-detection-only"
    )

    assert rule["matchManagers"] == ["pep621"]
    assert rule["dependencyDashboardApproval"] is True
    assert (REPO_ROOT / ".github" / "workflows" / "refresh-uv-lock.yml").exists()


def test_renovate_manager_expectations_match_exactly_one_coordinate_each() -> None:
    for expectation in tasks.RENOVATE_MANAGER_EXPECTATIONS:
        text = (tasks.ROOT / expectation.target_path).read_text(encoding="utf-8")
        matches = list(tasks.re.finditer(expectation.pattern, text))
        assert len(matches) == expectation.expected_match_count, expectation.description


def test_renovate_does_not_manage_lefthook_or_gh_aw() -> None:
    """Both pins are deliberately excluded from Renovate.

    Renovate cannot regenerate LEFTHOOK_SHA256 in the same change (workarounds
    D-12), and the gh-aw compiler pin is decided together with the workflow
    locks that ``gh aw compile`` generates. The scheduled checker detects both
    update candidates instead.
    """
    config = tasks.load_renovate_config()
    serialized = json.dumps(config)

    assert "LEFTHOOK_VERSION" not in serialized
    assert "gh-aw" not in {
        item.description for item in tasks.RENOVATE_MANAGER_EXPECTATIONS
    }
    assert "gh_aw_setup" not in serialized
    assert config["packageRules"][0]["enabled"] is False
    assert "github/gh-aw-actions" in config["packageRules"][0]["matchPackageNames"]
    assert set(tasks.FRESHNESS_CHECK_SUBJECTS) == {
        tasks.FRESHNESS_SUBJECT_GH_AW,
        tasks.FRESHNESS_SUBJECT_LEFTHOOK,
        tasks.FRESHNESS_SUBJECT_RENOVATE_ACTIVITY,
    }


# An independent renovate --dry-run=extract JSON fixture, hand-written rather
# than derived from RENOVATE_MANAGER_EXPECTATIONS, so the parser/validator
# contract is not just the expectation table agreeing with itself. Renovate
# emits every custom.regex manager under the "regex" group and can repeat a
# dependency, so the fixture deliberately duplicates one entry.
_RENOVATE_EXTRACTION_FIXTURE = (
    json.dumps(
        {
            "msg": "Extracted dependencies",
            "packageFiles": {
                "regex": [
                    {
                        "packageFile": "azure.yaml",
                        "deps": [{"depName": "chaos-mesh", "currentValue": "2.8.3"}],
                    },
                    {
                        "packageFile": "azure.yaml",
                        "deps": [{"depName": "chaos-mesh", "currentValue": "2.8.3"}],
                    },
                    {
                        "packageFile": "scripts/tasks.py",
                        "deps": [
                            {"depName": "rhysd/actionlint", "currentValue": "1.7.12"}
                        ],
                    },
                    {
                        "packageFile": "scripts/tasks.py",
                        "deps": [
                            {
                                "depName": "ghcr.io/yannh/kubeconform",
                                "currentValue": "v0.7.0",
                            }
                        ],
                    },
                    {
                        "packageFile": "scripts/tasks.py",
                        "deps": [
                            {"depName": "renovate/renovate", "currentValue": "44.51.2"}
                        ],
                    },
                    {
                        "packageFile": ".github/workflows/ci.yml",
                        "deps": [{"depName": "Azure/bicep", "currentValue": "0.46.1"}],
                    },
                    {
                        "packageFile": "azure.yaml",
                        "deps": [
                            {
                                "depName": "Azure/azure-dev",
                                "currentValue": "1.28.1",
                            }
                        ],
                    },
                    {
                        "packageFile": "pyproject.toml",
                        "deps": [{"depName": "uv", "currentValue": "0.12.2"}],
                    },
                ]
            },
        }
    )
    + "\n"
).encode()

_RENOVATE_EXTRACTION_EXPECTED = {
    ("regex", "azure.yaml", "chaos-mesh", "2.8.3"),
    ("regex", "scripts/tasks.py", "rhysd/actionlint", "1.7.12"),
    ("regex", "scripts/tasks.py", "ghcr.io/yannh/kubeconform", "v0.7.0"),
    ("regex", "scripts/tasks.py", "renovate/renovate", "44.51.2"),
    ("regex", ".github/workflows/ci.yml", "Azure/bicep", "0.46.1"),
    ("regex", "azure.yaml", "Azure/azure-dev", "1.28.1"),
    ("regex", "pyproject.toml", "uv", "0.12.2"),
}


def test_parse_renovate_extraction_collapses_duplicates() -> None:
    parsed = tasks.parse_renovate_extraction(_RENOVATE_EXTRACTION_FIXTURE)

    assert parsed == _RENOVATE_EXTRACTION_EXPECTED


def test_parse_renovate_extraction_raises_when_no_record_present() -> None:
    with pytest.raises(tasks.RenovateExtractionError):
        tasks.parse_renovate_extraction(b'{"msg": "some other log line"}\n')


def test_expected_renovate_extractions_covers_the_real_coordinates() -> None:
    expected = tasks.expected_renovate_extractions()

    dep_names = {dep_name for _manager, _file, dep_name, _value in expected}
    assert dep_names == {
        "chaos-mesh",
        "rhysd/actionlint",
        "ghcr.io/yannh/kubeconform",
        "renovate/renovate",
        "Azure/bicep",
        "Azure/azure-dev",
        "uv",
    }
    assert "evilmartians/lefthook" not in dep_names
    assert all(manager == "regex" for manager, _file, _dep, _value in expected)


def test_check_renovate_config_stays_out_of_the_offline_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The official validator needs Docker, so no review target may call it."""
    assert "check-renovate-config" not in {
        check.target_name
        for check in (*tasks.FAST_REVIEW_CHECKS, *tasks.FULL_REVIEW_CHECKS)
    }
    monkeypatch.setattr(tasks.shutil, "which", lambda _command: None)

    with pytest.raises(SystemExit) as error:
        tasks.target_check_renovate_config()

    assert error.value.code == 1


def test_check_renovate_config_reports_an_extraction_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        tasks, "run_renovate_config_validator", lambda: "INFO: Config validated"
    )
    monkeypatch.setattr(tasks, "run_renovate_extraction", lambda: b"")
    monkeypatch.setattr(tasks, "parse_renovate_extraction", lambda _stdout: set())

    with pytest.raises(SystemExit) as error:
        tasks.target_check_renovate_config()

    assert error.value.code == 1
    assert "--dry-run=extract result does not match" in capsys.readouterr().err


def test_check_renovate_activity_reports_only_the_activity_finding(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        tasks,
        "evaluate_renovate_activity",
        lambda: tasks.FreshnessFinding(
            "Renovate app activity",
            ".github/renovate.json",
            "unverified",
            tasks.FRESHNESS_REASON_RENOVATE_NOT_OBSERVED,
            "owner/repo",
            None,
            (),
            "Renovate app activity could not be confirmed",
        ),
    )

    with pytest.raises(SystemExit) as error:
        tasks.target_check_renovate_activity()

    assert error.value.code == 1
    output = capsys.readouterr()
    assert "Renovate app activity: unverified (renovate-not-observed)" in output.out
    assert "Renovate custom managers" not in output.out + output.err


def _dashboard_issue(
    updated_at: str,
    *,
    title: str = tasks.RENOVATE_DASHBOARD_TITLE,
    login: str = "renovate[bot]",
    user_type: str = "Bot",
    pull_request: bool = False,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "title": title,
        "updated_at": updated_at,
        "html_url": "https://github.com/owner/repo/issues/1",
        "user": {"login": login, "type": user_type},
    }
    if pull_request:
        issue["pull_request"] = {"url": "https://example.invalid/pull/1"}
    return issue


def _renovate_pull_request(
    updated_at: str,
    created_at: str | None = None,
    *,
    number: int = 7,
    login: str = "renovate[bot]",
    user_type: str = "Bot",
    is_pull_request: bool = True,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": "Update dependency rhysd/actionlint",
        "created_at": created_at if created_at is not None else updated_at,
        "updated_at": updated_at,
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "user": {"login": login, "type": user_type},
    }
    if is_pull_request:
        item["pull_request"] = {
            "html_url": f"https://github.com/owner/repo/pull/{number}"
        }
    return item


def _iso_days_ago(days: float) -> str:
    moment = tasks.datetime.datetime.now(
        tz=tasks.datetime.UTC
    ) - tasks.datetime.timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _issues_url(page: int, repository: str = "owner/repo") -> str:
    return tasks.renovate_dashboard_issues_urls(repository)[page - 1]


def _search_url(repository: str = "owner/repo") -> str:
    return tasks.renovate_pull_requests_search_url(repository)


def _search_payload(
    items: list[dict[str, Any]], *, incomplete: bool = False
) -> dict[str, Any]:
    return {
        "total_count": len(items),
        "incomplete_results": incomplete,
        "items": items,
    }


def _stub_github_json(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, object]
) -> list[str]:
    """Serve one canned JSON payload (or exception) per URL, recording calls."""
    requested: list[str] = []

    def open_url(url: str, _timeout: int) -> _FakeChecksumResponse:
        requested.append(url)
        if url not in responses:
            pytest.fail(f"unexpected GitHub request: {url}")
        payload = responses[url]
        if isinstance(payload, Exception):
            raise payload
        return _FakeChecksumResponse(json.dumps(payload).encode())

    monkeypatch.setattr(tasks, "open_github_url", open_url)
    monkeypatch.setattr(tasks, "resolve_github_repository", lambda: "owner/repo")
    return requested


def test_renovate_activity_passes_on_a_recent_dashboard_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [_dashboard_issue(_iso_days_ago(1))],
            _search_url(): _search_payload([]),
        },
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "pass"
    assert finding.reason_code == tasks.FRESHNESS_REASON_RENOVATE_ACTIVITY_OBSERVED
    assert "https://github.com/owner/repo/issues/1" in finding.evidence


def test_renovate_activity_passes_on_a_recent_pull_request_without_a_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renovate only writes the dashboard when it has something to change.

    A repository with no open dashboard can still be actively served by the
    app, so a recent Renovate-authored pull request is on its own enough.
    """
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [],
            _search_url(): _search_payload(
                [_renovate_pull_request(_iso_days_ago(2), _iso_days_ago(3))]
            ),
        },
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "pass"
    assert finding.reason_code == tasks.FRESHNESS_REASON_RENOVATE_ACTIVITY_OBSERVED
    assert "Renovate pull request" in finding.detail
    assert "https://github.com/owner/repo/pull/7" in finding.evidence


def test_renovate_activity_takes_the_newest_of_every_observed_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long-untouched dashboard is not evidence of an inactive app."""
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [
                _dashboard_issue(
                    _iso_days_ago(tasks.RENOVATE_ACTIVITY_WINDOW_DAYS + 90)
                )
            ],
            _search_url(): _search_payload(
                [_renovate_pull_request(_iso_days_ago(3), _iso_days_ago(400))]
            ),
        },
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "pass"
    assert finding.published == _iso_days_ago(3)


def test_renovate_activity_is_unverified_when_nothing_was_ever_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_github_json(
        monkeypatch,
        {_issues_url(1): [], _search_url(): _search_payload([])},
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "unverified"
    assert finding.reason_code == tasks.FRESHNESS_REASON_RENOVATE_NOT_OBSERVED
    assert "activity could not be confirmed" in finding.detail


def test_renovate_activity_outside_the_window_does_not_claim_the_app_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _iso_days_ago(tasks.RENOVATE_ACTIVITY_WINDOW_DAYS + 1)
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [_dashboard_issue(stale)],
            _search_url(): _search_payload([_renovate_pull_request(stale)]),
        },
    )

    finding = tasks.evaluate_renovate_activity()

    assert tasks.RENOVATE_ACTIVITY_WINDOW_DAYS == 14
    assert finding.status == "unverified"
    assert finding.reason_code == tasks.FRESHNESS_REASON_RENOVATE_ACTIVITY_UNOBSERVED
    assert "could not be confirmed from recent public activity" in finding.detail
    assert "does not establish that the app was removed or disabled" in finding.detail


def test_renovate_activity_accepts_the_boundary_of_the_observation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = _iso_days_ago(tasks.RENOVATE_ACTIVITY_WINDOW_DAYS - 0.5)
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [_dashboard_issue(fresh)],
            _search_url(): _search_payload([]),
        },
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "pass"


def test_renovate_activity_is_unverified_when_a_lookup_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A throttled API says nothing about Renovate, so it is an evidence gap."""
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [],
            _search_url(): urllib.error.HTTPError(
                _search_url(), 403, "rate limit exceeded", email.message.Message(), None
            ),
        },
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "unverified"
    assert finding.reason_code == tasks.FRESHNESS_REASON_EVIDENCE_UNAVAILABLE
    assert "rate limit" in finding.detail


def test_renovate_activity_prefers_a_fresh_observation_over_a_partial_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failing endpoint cannot erase activity the other one proved."""
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [_dashboard_issue(_iso_days_ago(1))],
            _search_url(): urllib.error.HTTPError(
                _search_url(), 429, "too many requests", email.message.Message(), None
            ),
        },
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "pass"
    assert finding.reason_code == tasks.FRESHNESS_REASON_RENOVATE_ACTIVITY_OBSERVED


def test_renovate_activity_is_unverified_when_the_repository_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> str:
        raise tasks.RenovateEvidenceUnavailableError("no origin remote")

    monkeypatch.setattr(tasks, "resolve_github_repository", unavailable)

    finding = tasks.evaluate_renovate_activity()

    assert finding.status == "unverified"
    assert finding.reason_code == tasks.FRESHNESS_REASON_EVIDENCE_UNAVAILABLE
    assert "freshness evidence was unavailable" in finding.detail


def test_renovate_activity_never_reports_fail_for_configuration_defects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration is check-renovate-config's business, not this target's.

    The activity evaluator must not even read renovate.json: doing so gave one
    defect two owners and let an external observation return ``fail``.
    """

    def unreadable() -> dict[str, Any]:
        pytest.fail("evaluate_renovate_activity must not read renovate.json")

    monkeypatch.setattr(tasks, "load_renovate_config", unreadable)
    _stub_github_json(
        monkeypatch,
        {_issues_url(1): [], _search_url(): _search_payload([])},
    )

    finding = tasks.evaluate_renovate_activity()

    assert finding.status != "fail"


def test_renovate_dashboard_lookup_pages_past_a_full_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy issue tracker must not make a present dashboard look absent."""
    filler = [
        _dashboard_issue(_iso_days_ago(1), title=f"unrelated {index}", login="someone")
        for index in range(tasks.RENOVATE_ISSUES_PER_PAGE)
    ]
    requested = _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): filler,
            _issues_url(2): [_dashboard_issue(_iso_days_ago(1))],
        },
    )

    issue = tasks.fetch_renovate_dashboard_issue("owner/repo")

    assert issue is not None
    assert requested == [_issues_url(1), _issues_url(2)]


def test_renovate_dashboard_lookup_is_unverified_when_pages_run_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filler = [
        _dashboard_issue(_iso_days_ago(1), title=f"unrelated {index}", login="someone")
        for index in range(tasks.RENOVATE_ISSUES_PER_PAGE)
    ]
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(page): filler
            for page in range(1, tasks.RENOVATE_ISSUES_MAX_PAGES + 1)
        },
    )

    with pytest.raises(tasks.RenovateEvidenceUnavailableError):
        tasks.fetch_renovate_dashboard_issue("owner/repo")


def test_renovate_dashboard_lookup_rejects_impostor_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the Renovate app's own dashboard issue counts as an observation."""
    payload = [
        _dashboard_issue(_iso_days_ago(1), pull_request=True),
        _dashboard_issue(_iso_days_ago(1), login="someone", user_type="User"),
        _dashboard_issue(_iso_days_ago(1), login="other[bot]"),
        _dashboard_issue(_iso_days_ago(1), title="Dependency Dashboard (draft)"),
    ]
    _stub_github_json(monkeypatch, {_issues_url(1): payload})

    assert tasks.fetch_renovate_dashboard_issue("owner/repo") is None

    payload.append(_dashboard_issue(_iso_days_ago(2)))

    issue = tasks.fetch_renovate_dashboard_issue("owner/repo")

    assert issue is not None
    assert issue["user"]["login"] == "renovate[bot]"


def test_renovate_pull_request_lookup_rejects_non_renovate_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search results are re-checked, so only Renovate-authored PRs count."""
    _stub_github_json(
        monkeypatch,
        {
            _search_url(): _search_payload(
                [
                    _renovate_pull_request(_iso_days_ago(1), is_pull_request=False),
                    _renovate_pull_request(
                        _iso_days_ago(1), login="someone", user_type="User"
                    ),
                    _renovate_pull_request(_iso_days_ago(1), login="other[bot]"),
                    _renovate_pull_request(_iso_days_ago(5), number=9),
                ]
            )
        },
    )

    matched = tasks.fetch_renovate_pull_requests("owner/repo")

    assert [item["html_url"] for item in matched] == [
        "https://github.com/owner/repo/pull/9"
    ]


def test_renovate_pull_request_lookup_is_unverified_on_incomplete_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated search cannot establish that Renovate never opened a PR."""
    _stub_github_json(
        monkeypatch,
        {_search_url(): _search_payload([], incomplete=True)},
    )

    with pytest.raises(tasks.RenovateEvidenceUnavailableError):
        tasks.fetch_renovate_pull_requests("owner/repo")


def test_renovate_activity_lookup_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token-value")
    _stub_github_json(
        monkeypatch,
        {
            _issues_url(1): [_dashboard_issue(_iso_days_ago(1))],
            _search_url(): _search_payload([]),
        },
    )

    headers = tasks.github_api_request_headers()
    finding = tasks.evaluate_renovate_activity()

    assert "secret-token-value" in headers["Authorization"]
    output = capsys.readouterr()
    assert "secret-token-value" not in output.out + output.err
    assert "secret-token-value" not in finding.detail + " ".join(finding.evidence)


def test_resolve_github_repository_prefers_the_environment_then_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/from-env")

    assert tasks.resolve_github_repository() == "owner/from-env"

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(
        tasks,
        "command_output",
        lambda *_args, **_kwargs: "https://github.com/owner/from-remote.git\n",
    )

    assert tasks.resolve_github_repository() == "owner/from-remote"

    monkeypatch.setattr(tasks, "command_output", lambda *_args, **_kwargs: "")

    with pytest.raises(tasks.RenovateEvidenceUnavailableError):
        tasks.resolve_github_repository()


def test_freshness_checks_output_stays_exit_zero_for_fail_and_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The weekly workflow must still receive a JSON document to report on.

    Exiting non-zero here would abort the workflow step before the skill could
    read the findings, hiding the very failures the run exists to surface.
    """
    monkeypatch.setattr(
        tasks,
        "collect_freshness_findings",
        lambda: [
            tasks.FreshnessFinding(
                "Renovate app activity",
                ".github/renovate.json",
                "unverified",
                tasks.FRESHNESS_REASON_RENOVATE_ACTIVITY_UNOBSERVED,
                None,
                None,
                (),
                "activity could not be confirmed",
            ),
            tasks.FreshnessFinding(
                "Lefthook",
                ".github/workflows/ci.yml",
                "fail",
                tasks.FRESHNESS_REASON_CHECKSUM_MISMATCH,
                None,
                None,
                (),
                "checksum mismatch",
            ),
        ],
    )
    output = tmp_path / "freshness.json"

    tasks.target_freshness_checks(output)

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["status"] == "fail"
    assert {finding["reason_code"] for finding in document["findings"]} == {
        "renovate-activity-unobserved",
        "checksum-mismatch",
    }


def test_freshness_document_status_is_fail_first_then_unverified() -> None:
    def _finding(status: str) -> tasks.FreshnessFinding:
        return tasks.FreshnessFinding("s", "c", status, "r", None, None, (), "detail")

    fail_doc = tasks.freshness_document(
        [_finding("pass"), _finding("unverified"), _finding("fail")]
    )
    assert fail_doc["status"] == "fail"
    assert fail_doc["coverage"] == {
        "total": 3,
        "pass": 1,
        "fail": 1,
        "unverified": 1,
        "excluded": 0,
    }

    unverified_doc = tasks.freshness_document(
        [_finding("pass"), _finding("unverified")]
    )
    assert unverified_doc["status"] == "unverified"

    pass_doc = tasks.freshness_document([_finding("pass"), _finding("pass")])
    assert pass_doc["status"] == "pass"


def test_freshness_document_empty_input_is_unverified() -> None:
    document = tasks.freshness_document([])

    assert document["status"] == "unverified"
    assert document["coverage"] == {
        "total": 0,
        "pass": 0,
        "fail": 0,
        "unverified": 0,
        "excluded": 0,
    }


def test_freshness_finding_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unsupported freshness status"):
        tasks.FreshnessFinding(
            "subject",
            "coordinate",
            "unknown",
            "reason",
            None,
            None,
            (),
            "detail",
        )


def test_freshness_finding_rejects_empty_reason_code() -> None:
    with pytest.raises(ValueError, match="reason_code must not be empty"):
        tasks.FreshnessFinding(
            "subject",
            "coordinate",
            "pass",
            "",
            None,
            None,
            (),
            "detail",
        )


def test_freshness_document_serializes_reason_codes_and_evidence() -> None:
    finding = tasks.FreshnessFinding(
        "Lefthook",
        ".github/workflows/ci.yml",
        "unverified",
        tasks.FRESHNESS_REASON_UPDATE_AVAILABLE,
        "2.1.10",
        "2.1.12",
        ("https://example.invalid/releases",),
        "newer release available",
    )
    document = tasks.freshness_document([finding])
    entry = document["findings"][0]

    assert entry["reason_code"] == "update-available"
    assert entry["current"] == "2.1.10"
    assert entry["published"] == "2.1.12"
    assert entry["evidence"] == ["https://example.invalid/releases"]
    assert document["schema_version"] == tasks.FRESHNESS_SCHEMA_VERSION


def test_freshness_checks_target_writes_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        tasks,
        "collect_freshness_findings",
        lambda: [
            tasks.FreshnessFinding(
                "gh-aw", "c", "unverified", "update-available", "a", "b", (), "d"
            )
        ],
    )
    output = tmp_path / "freshness.json"

    tasks.target_freshness_checks(output)

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["status"] == "unverified"
    assert document["findings"][0]["reason_code"] == "update-available"


def test_freshness_checks_target_is_registered() -> None:
    assert "freshness-checks" in tasks.TARGETS


def test_classify_gh_aw_compiler_pin_rejects_known_stale_example() -> None:
    """Pin the known real-world gap (pinned v0.79.6, latest v0.86.2).

    Confirming *a* current release exists upstream is not evidence the pin
    is current. This must not report "pass" for a real, confirmed version
    difference -- it reports "unverified" because bumping the gh-aw compiler
    pin is a deliberate, human-reviewed maintenance decision in this
    repository, not an automatic latest-wins update.
    """
    status, message = tasks.classify_gh_aw_compiler_pin("v0.79.6", "v0.86.2")

    assert status == "unverified"
    assert "v0.79.6" in message
    assert "v0.86.2" in message
    assert "requires maintainer review of workflow compatibility" in message


def test_classify_gh_aw_compiler_pin_passes_when_versions_match() -> None:
    status, message = tasks.classify_gh_aw_compiler_pin("v0.79.6", "v0.79.6")

    assert status == "pass"
    assert "matches the latest stable release" in message


def test_classify_gh_aw_compiler_pin_fails_on_malformed_version() -> None:
    status, message = tasks.classify_gh_aw_compiler_pin("not-a-version", "v0.86.2")

    assert status == "fail"
    assert "not a valid vX.Y.Z version" in message


def test_parse_gh_aw_version_accepts_with_or_without_v_prefix() -> None:
    assert tasks.parse_gh_aw_version("v0.79.6") == (0, 79, 6)
    assert tasks.parse_gh_aw_version("0.79.6") == (0, 79, 6)
    assert tasks.parse_gh_aw_version("v0.79") is None
    assert tasks.parse_gh_aw_version("") is None


def test_read_gh_aw_setup_version_matches_the_real_pin() -> None:
    pinned = tasks.read_gh_aw_setup_version()

    assert tasks.parse_gh_aw_version(pinned) is not None


def test_fetch_gh_aw_latest_release_parses_tag_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"tag_name": "v0.86.2"}).encode("utf-8")
    monkeypatch.setattr(
        tasks.urllib.request,
        "urlopen",
        lambda _url, timeout=None: _FakeChecksumResponse(payload),
    )

    assert tasks.fetch_gh_aw_latest_release() == "v0.86.2"


def test_fetch_gh_aw_latest_release_raises_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_network_error(_url: str, timeout: float | None = None) -> None:
        raise tasks.urllib.error.URLError("simulated DNS failure")

    monkeypatch.setattr(tasks.urllib.request, "urlopen", raise_network_error)

    with pytest.raises(tasks.GhAwReleaseUnavailableError, match="network"):
        tasks.fetch_gh_aw_latest_release()


def test_fetch_gh_aw_latest_release_raises_when_tag_name_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"name": "no tag_name field"}).encode("utf-8")
    monkeypatch.setattr(
        tasks.urllib.request,
        "urlopen",
        lambda _url, timeout=None: _FakeChecksumResponse(payload),
    )

    with pytest.raises(tasks.GhAwReleaseUnavailableError):
        tasks.fetch_gh_aw_latest_release()


def _write_range_fixture(repository: Path, azd_range: str, bundle_version: str) -> None:
    (repository / tasks.AZURE_YAML_PATH).parent.mkdir(parents=True, exist_ok=True)
    (repository / tasks.AZURE_YAML_PATH).write_text(
        f"requiredVersions:\n  azd: '{azd_range}'\n", encoding="utf-8"
    )
    host_json_path = repository / tasks.FUNCTIONS_HOST_JSON_PATH
    host_json_path.parent.mkdir(parents=True, exist_ok=True)
    host_json_path.write_text(
        json.dumps(
            {
                "extensionBundle": {
                    "id": "Microsoft.Azure.Functions.ExtensionBundle",
                    "version": bundle_version,
                }
            }
        ),
        encoding="utf-8",
    )


def test_fast_review_timeout_is_unverified_and_next_check_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(
        args: list[str] | tuple[str, ...],
        target: str,
    ) -> subprocess.CompletedProcess[bytes] | None:
        if target == "check-repo-health":
            raise subprocess.TimeoutExpired(args, tasks.REVIEW_CHECK_TIMEOUT_SECONDS)
        return None

    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        review_command_stub(calls, handler=handler),
    )

    results = tasks.run_fast_review_checks()

    assert calls == list(FAST_TARGET_CALLS)
    assert [(result.name, result.status) for result in results] == [
        ("repo-health", "unverified"),
        ("uv-version", "pass"),
        ("public-lock", "pass"),
        ("publisher-requirements", "pass"),
        ("version-pins", "pass"),
    ]


def test_qa_app_cli_can_skip_publisher_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        tasks,
        "target_qa_app",
        lambda *, check_publisher_requirements=True: calls.append(
            check_publisher_requirements
        ),
    )

    result = tasks.main(["qa-app", "--skip-publisher-requirements"])

    assert result == 0
    assert calls == [False]


def test_review_repo_fast_runs_every_deterministic_target_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        tasks, "run_isolated_review_command", review_command_stub(calls)
    )

    tasks.target_review_repo_fast()

    assert calls == list(FAST_TARGET_CALLS)
    assert len(calls) == len(set(calls))


def test_review_repo_fast_marks_missing_tools_unverified_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        tasks,
        "probe_review_tool",
        lambda command: f"{command} was not found" if command == "uv" else None,
    )
    monkeypatch.setattr(
        tasks, "run_isolated_review_command", review_command_stub(calls)
    )

    tasks.target_review_repo_fast()

    assert calls == [
        "check-repo-health",
        "check-public-lock",
        "check-publisher-requirements",
        "check-version-pins",
    ]
    output = capsys.readouterr()
    assert "[pass] (check-passed) repo-health: check passed" in output.out
    assert (
        "[unverified] (tool-preflight-failed) uv-version: "
        "required tool preflight failed: uv was not found" in output.err
    )
    assert "[pass] (check-passed) public-lock: check passed" in output.out
    assert "Repository review completed with unverified checks" in output.out
    assert "Fast repository review passed" not in output.out


def test_review_repo_fast_marks_real_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def handler(
        args: list[str] | tuple[str, ...],
        target: str,
    ) -> subprocess.CompletedProcess[bytes] | None:
        if target == "check-repo-health":
            return subprocess.CompletedProcess(
                args,
                7,
                stdout=b"",
                stderr=b"AssertionError: repository health failed",
            )
        return None

    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        review_command_stub(calls, handler=handler),
    )

    with pytest.raises(SystemExit) as error:
        tasks.target_review_repo_fast()

    assert error.value.code == 1
    assert calls == list(FAST_TARGET_CALLS)
    assert "[fail] (repository-failure) repo-health:" in capsys.readouterr().err


def test_review_repo_fast_marks_start_failure_unverified_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def handler(
        _args: list[str] | tuple[str, ...],
        target: str,
    ) -> subprocess.CompletedProcess[bytes] | None:
        if target == "check-repo-health":
            raise OSError("cannot execute")
        return None

    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        review_command_stub(calls, handler=handler),
    )

    tasks.target_review_repo_fast()

    assert calls == list(FAST_TARGET_CALLS)
    assert (
        "[unverified] (check-start-failed) repo-health: "
        "check could not be started: cannot execute" in capsys.readouterr().err
    )


def test_review_repo_fast_propagates_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        tasks.target_review_repo_fast()


def test_review_checks_preclassify_each_required_tool() -> None:
    fast_tools = {
        check.name: check.required_tools for check in tasks.FAST_REVIEW_CHECKS
    }
    full_tools = {
        check.name: check.required_tools for check in tasks.FULL_REVIEW_CHECKS
    }

    assert fast_tools == {
        "repo-health": ("git",),
        "uv-version": ("uv",),
        "public-lock": (),
        "publisher-requirements": (),
        "version-pins": (),
    }
    assert full_tools == {
        "qa-app": ("git", "uv"),
        "test-hooks": ("git", "uv", "lefthook"),
        "build-bicep": ("git", "az"),
        "lint-k8s": ("git", "docker"),
        "validate-helm-values": ("helm",),
        "lint-workflows": ("git", "docker"),
        "compile-aw": ("git", "gh"),
    }
    assert tasks.FULL_REVIEW_CHECKS[0].target_arguments == (
        "--skip-publisher-requirements",
    )


def test_full_review_never_repeats_a_fast_check() -> None:
    fast_names = {check.name for check in tasks.FAST_REVIEW_CHECKS}
    fast_targets = {check.target_name for check in tasks.FAST_REVIEW_CHECKS}
    full_names = {check.name for check in tasks.FULL_REVIEW_CHECKS}
    full_targets = {check.target_name for check in tasks.FULL_REVIEW_CHECKS}

    assert fast_names.isdisjoint(full_names)
    assert fast_targets.isdisjoint(full_targets)


def test_review_results_json_hands_structured_status_to_the_next_layer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(
        args: list[str] | tuple[str, ...],
        target: str,
    ) -> subprocess.CompletedProcess[bytes] | None:
        if target == "check-version-pins":
            return subprocess.CompletedProcess(
                args,
                1,
                stdout=b"",
                stderr=b"error: renovate.json must not enable automerge",
            )
        return None

    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        review_command_stub(calls, handler=handler),
    )
    results_path = tmp_path / "results.json"

    with pytest.raises(SystemExit):
        tasks.target_review_repo_fast(None, results_path)

    document = json.loads(results_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == tasks.REVIEW_RESULTS_SCHEMA_VERSION
    assert document["mode"] == "fast"
    assert document["status"] == "fail"
    assert document["coverage"]["fail"] == 1
    assert [check["name"] for check in document["checks"]] == list(FAST_CHECK_NAMES)
    version_pins = next(
        check for check in document["checks"] if check["name"] == "version-pins"
    )
    assert version_pins["status"] == "fail"
    assert version_pins["reason_code"] == tasks.REVIEW_REASON_CHECK_FAILED


def test_review_results_document_empty_input_is_unverified() -> None:
    document = tasks.review_results_document("fast", [])

    assert document["status"] == "unverified"
    assert document["coverage"] == {
        "total": 0,
        "pass": 0,
        "fail": 0,
        "unverified": 0,
        "excluded": 0,
    }


def test_review_result_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown review status"):
        tasks.ReviewResult("check", "unknown", "detail", "reason")


def test_review_result_rejects_empty_reason_code() -> None:
    with pytest.raises(ValueError, match="reason_code must not be empty"):
        tasks.ReviewResult("check", "pass", "detail", "")


def test_review_repo_cli_accepts_results_json_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    results_path = tmp_path / "results.json"
    calls: list[tuple[Path | None, Path | None]] = []
    monkeypatch.setattr(
        tasks,
        "target_review_repo_fast",
        lambda inventory_json=None, results_json=None: calls.append(
            (inventory_json, results_json)
        ),
    )

    assert tasks.main(["review-repo-fast", "--results-json", str(results_path)]) == 0
    assert calls == [(None, results_path)]


def test_kubernetes_schema_exclusions_have_one_configuration_source() -> None:
    with (REPO_ROOT / ".github" / "repo-health.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    assert tuple(sorted(config["kubernetes_schema_excluded_kinds"])) == (
        tasks.KUBECONFORM_SKIP_KINDS
    )
    assert ",".join(tasks.KUBECONFORM_SKIP_KINDS) == tasks.KUBECONFORM_SKIP


def test_review_qa_app_skips_publisher_check_already_run_by_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    for target_name in (
        "target_format_check",
        "target_lint_check",
        "target_typecheck",
        "target_test",
        "target_check_publisher_requirements",
    ):
        monkeypatch.setattr(
            tasks,
            target_name,
            lambda name=target_name: calls.append(name),
        )

    tasks.target_qa_app(check_publisher_requirements=False)

    assert calls == [
        "target_format_check",
        "target_lint_check",
        "target_typecheck",
        "target_test",
    ]


def test_repository_health_targets_use_current_python(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: calls.append(tuple(args)),
    )

    tasks.target_inventory_repo()
    tasks.target_inventory_repo("json")

    script = str(tasks.ROOT / "scripts" / "repo_health.py")
    assert calls == [
        (sys.executable, script, "inventory", "--format", "text"),
        (sys.executable, script, "inventory", "--format", "json"),
    ]
    output = capsys.readouterr().out
    assert output.count("Inventorying tracked repository health coordinates") == 1
    assert output.count("Repository inventory completed") == 1


def test_review_repo_full_passes_one_inventory_output_to_fast_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "inventory.json"
    calls: list[object] = []
    monkeypatch.setattr(
        tasks,
        "run_fast_review_checks",
        lambda inventory_json=None: calls.append(inventory_json) or [],
    )
    monkeypatch.setattr(
        tasks,
        "run_review_targets_isolated",
        lambda checks: calls.append(tuple(check.name for check in checks)) or [],
    )

    tasks.target_review_repo_full(output_path)

    assert calls == [
        output_path,
        (
            "qa-app",
            "test-hooks",
            "build-bicep",
            "lint-k8s",
            "validate-helm-values",
            "lint-workflows",
            "compile-aw",
        ),
    ]


def test_review_repo_cli_accepts_inventory_json_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "inventory.json"
    calls: list[Path | None] = []
    monkeypatch.setattr(
        tasks,
        "target_review_repo_full",
        lambda inventory_json=None, results_json=None: calls.append(inventory_json),
    )

    assert (
        tasks.main(
            [
                "review-repo-full",
                "--inventory-json",
                str(output_path),
            ]
        )
        == 0
    )
    assert calls == [output_path]


def test_check_repo_health_generates_and_renders_one_json_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "inventory.json"
    calls: list[tuple[str, Path]] = []

    def generate(destination: Path) -> int:
        calls.append(("generate", destination))
        destination.write_text('{"schema_version": "2.1"}\n', encoding="utf-8")
        return 0

    monkeypatch.setattr(tasks, "generate_repo_health_check_json", generate)
    monkeypatch.setattr(
        tasks,
        "render_repo_health_check",
        lambda report: calls.append(("render", report)),
    )

    tasks.target_check_repo_health(output_path)

    assert calls == [
        ("generate", output_path),
        ("render", output_path),
    ]


def test_review_repo_fast_skips_only_uv_version_when_uv_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")

    def version_probe(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env, timeout
        if tuple(args) == tasks.REVIEW_TOOL_VERSION_COMMANDS["uv"]:
            return subprocess.CompletedProcess(args, 1)
        if tuple(args) == tasks.REVIEW_TOOL_VERSION_COMMANDS["git"]:
            return subprocess.CompletedProcess(args, 0)
        calls.append(args[2])
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run_isolated_review_command", version_probe)
    monkeypatch.setattr(tasks, "probe_review_tool", real_probe_review_tool)
    tasks.target_review_repo_fast()

    assert calls == [
        "check-repo-health",
        "check-public-lock",
        "check-publisher-requirements",
        "check-version-pins",
    ]
    output = capsys.readouterr()
    assert "[pass] (check-passed) repo-health: check passed" in output.out
    assert "[pass] (check-passed) public-lock: check passed" in output.out
    assert "[pass] (check-passed) publisher-requirements: check passed" in output.out
    assert (
        "[unverified] (tool-preflight-failed) uv-version: "
        "required tool preflight failed: "
        "uv --version exited with code 1 during preflight"
    ) in output.err
    assert "Repository review completed with unverified checks" in output.out


@pytest.mark.parametrize(
    ("failure", "expected_detail"),
    [
        (
            subprocess.TimeoutExpired(("git", "--version"), 1),
            "git --version timed out during preflight",
        ),
        (
            OSError("cannot execute"),
            "git could not be started during preflight: cannot execute",
        ),
    ],
)
def test_review_tool_probe_failure_marks_check_unverified(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_detail: str,
) -> None:
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(tasks, "probe_review_tool", real_probe_review_tool)

    def fail_probe(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(tasks, "run_isolated_review_command", fail_probe)

    runnable, results = tasks.classify_review_tools(
        (tasks.ReviewCheck("check", "check", ("git",)),)
    )

    assert runnable == []
    assert [(result.status, result.detail) for result in results] == [
        ("unverified", f"required tool preflight failed: {expected_detail}")
    ]


def test_compile_aw_is_unverified_when_extension_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")

    def unavailable(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env
        calls.append((tuple(args), timeout))
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(tasks, "run_isolated_review_command", unavailable)

    runnable, results = tasks.classify_review_tools(
        (tasks.ReviewCheck("compile-aw", "compile-aw", ("git", "gh")),)
    )

    assert runnable == []
    assert [(result.name, result.status) for result in results] == [
        ("compile-aw", "unverified")
    ]
    assert calls == [
        (
            tasks.REVIEW_GH_AW_LIST_COMMAND,
            tasks.REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS,
        )
    ]


def test_compile_aw_preflight_timeout_is_unverified_and_does_not_block_next_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    system_which = tasks.shutil.which

    def available_tool(command: str) -> str | None:
        if command in {"powershell.exe", "pwsh.exe"}:
            return system_which(command)
        return f"mock-{command}"

    monkeypatch.setattr(tasks.shutil, "which", available_tool)
    monkeypatch.setattr(tasks, "REVIEW_GH_AW_PREFLIGHT_TIMEOUT_SECONDS", 0.5)
    child_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    original_resolve_command = tasks.resolve_command

    def resolve_preflight(args: list[str] | tuple[str, ...]) -> list[str]:
        if tuple(args) == tasks.REVIEW_GH_AW_LIST_COMMAND:
            return [sys.executable, "-c", child_code]
        return original_resolve_command(args)

    monkeypatch.setattr(tasks, "resolve_command", resolve_preflight)

    runnable, results = tasks.classify_review_tools(
        (
            tasks.ReviewCheck("compile-aw", "compile-aw", ("git", "gh")),
            tasks.ReviewCheck("next", "next", ("git",)),
        )
    )

    assert [check.name for check in runnable] == ["next"]
    assert [(result.name, result.status) for result in results] == [
        ("compile-aw", "unverified")
    ]
    tmp_path.rmdir()
    assert not tmp_path.exists()


def test_review_repo_full_runs_available_checks_after_fast_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        tasks,
        "run_fast_review_checks",
        lambda: [
            tasks.ReviewResult(
                "fast",
                "fail",
                "failed",
                tasks.REVIEW_REASON_REPOSITORY_FAILURE,
            )
        ],
    )
    monkeypatch.setattr(
        tasks,
        "run_review_targets_isolated",
        lambda checks: calls.append(tuple(check.name for check in checks)) or [],
    )

    with pytest.raises(SystemExit) as error:
        tasks.target_review_repo_full()

    assert error.value.code == 1
    assert calls == [
        (
            "qa-app",
            "test-hooks",
            "build-bicep",
            "lint-k8s",
            "validate-helm-values",
            "lint-workflows",
            "compile-aw",
        )
    ]


def test_isolated_review_copies_current_tracked_and_untracked_files_for_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    scripts_directory = repository / "scripts"
    scripts_directory.mkdir(parents=True)
    (scripts_directory / "tasks.py").write_text("# task runner\n", encoding="utf-8")
    (repository / "README.md").write_text(
        "# Modified uncommitted content\n", encoding="utf-8"
    )
    new_skill = (
        repository / ".github" / "skills" / "repository-freshness-checker" / "SKILL.md"
    )
    new_test = repository / "scripts" / "tests" / "test_review_repo_contract.py"
    new_skill.parent.mkdir(parents=True)
    new_test.parent.mkdir(parents=True)
    new_skill.write_text("# New skill\n", encoding="utf-8")
    new_test.write_text("# New test\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    git_outputs = {
        ("git", "ls-files", "-z"): ["scripts/tasks.py", "README.md"],
        (
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ): [
            ".github/skills/repository-freshness-checker/SKILL.md",
            "scripts/tests/test_review_repo_contract.py",
        ],
    }
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: git_outputs[tuple(args)],
    )
    monkeypatch.setattr(
        tasks,
        "command_output",
        lambda args, **_kwargs: (
            "https://github.com/example/repository.git"
            if tuple(args) == ("git", "remote", "get-url", "origin")
            else pytest.fail(f"unexpected command: {args}")
        ),
    )
    calls: list[tuple[tuple[str, ...], Path, dict[str, str] | None]] = []
    isolated_root: Path | None = None

    def record_run(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del check, timeout
        calls.append((tuple(args), cwd, env))
        return subprocess.CompletedProcess(args, 0)

    def record_isolated(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal isolated_root
        target_name = args[-1]
        if target_name == "prepare-review-python-env":
            assert timeout == tasks.REVIEW_PYTHON_ENVIRONMENT_TIMEOUT_SECONDS
            assert tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE not in env
        else:
            assert timeout == tasks.REVIEW_CHECK_TIMEOUT_SECONDS
            assert env[tasks.REVIEW_PREPARED_ENVIRONMENT_VARIABLE] == "1"
        isolated_root = cwd
        calls.append((tuple(args), cwd, env))
        assert (
            cwd / ".github" / "skills" / "repository-freshness-checker" / "SKILL.md"
        ).is_file()
        assert (cwd / "scripts" / "tests" / "test_review_repo_contract.py").is_file()
        assert (cwd / "README.md").read_text(encoding="utf-8") == (
            "# Modified uncommitted content\n"
        )
        assert not cwd.is_relative_to(repository.resolve())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run", record_run)
    monkeypatch.setattr(tasks, "run_isolated_review_command", record_isolated)

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("qa-app", "qa-app", ("git", "uv")),
            tasks.ReviewCheck("test-hooks", "test-hooks", ("git", "uv", "lefthook")),
        )
    )

    assert len(calls) == 7
    assert calls[0][0][:2] == ("git", "init")
    assert calls[1][0] == (
        "git",
        "remote",
        "add",
        "origin",
        "https://github.com/example/repository.git",
    )
    assert calls[2][0] == ("git", "add", "--force", "--all")
    assert "commit" in calls[3][0]
    assert calls[4][0][-1] == "prepare-review-python-env"
    assert calls[5][0][-1] == "qa-app"
    assert calls[6][0][-1] == "test-hooks"
    assert all(call[1] != repository for call in calls)
    assert calls[6][2] is not None
    assert "UV_PROJECT_ENVIRONMENT" in calls[6][2]
    assert Path(calls[6][2]["UV_PROJECT_ENVIRONMENT"]).is_relative_to(calls[6][1])
    assert [(result.name, result.status) for result in results] == [
        ("qa-app", "pass"),
        ("test-hooks", "pass"),
    ]
    assert isolated_root is not None
    assert not isolated_root.exists()
    assert (repository / "README.md").read_text(encoding="utf-8") == (
        "# Modified uncommitted content\n"
    )
    assert not (repository / "tmp").exists()


def test_isolated_review_rejects_os_temporary_directory_inside_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    real_mkdtemp = tasks.tempfile.mkdtemp
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks.tempfile,
        "mkdtemp",
        lambda *, prefix: real_mkdtemp(prefix=prefix, dir=repository),
    )
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda _destination: pytest.fail("snapshot copy must not run"),
    )
    monkeypatch.setattr(
        tasks,
        "run",
        lambda *_args, **_kwargs: pytest.fail("git setup must not run"),
    )
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda *_args, **_kwargs: pytest.fail("full checks must not run"),
    )

    results = tasks.run_review_targets_isolated(
        (tasks.ReviewCheck("test-hooks", "test-hooks", ("git",)),)
    )

    assert [(result.name, result.status) for result in results] == [
        ("snapshot", "unverified"),
        ("test-hooks", "unverified"),
    ]
    assert results[0].detail == (
        "operating-system temporary directory is inside the repository"
    )
    assert not any(repository.iterdir())


def test_isolated_review_times_out_one_check_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    scripts_directory = repository / "scripts"
    scripts_directory.mkdir(parents=True)
    (scripts_directory / "tasks.py").write_text("# task runner\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["scripts/tasks.py"] if tuple(args) == ("git", "ls-files", "-z") else []
        ),
    )
    child_calls: list[str] = []

    def record_run(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env, check, timeout
        return subprocess.CompletedProcess(args, 0)

    def record_isolated(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, env
        child_calls.append(args[-1])
        assert timeout == tasks.REVIEW_CHECK_TIMEOUT_SECONDS
        if args[-1] == "slow":
            raise subprocess.TimeoutExpired(args, tasks.REVIEW_CHECK_TIMEOUT_SECONDS)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(tasks, "run", record_run)
    monkeypatch.setattr(tasks, "run_isolated_review_command", record_isolated)

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("slow", "slow", ("git",)),
            tasks.ReviewCheck("next", "next", ("git",)),
        )
    )

    assert child_calls == ["slow", "next"]
    assert [(result.name, result.status) for result in results] == [
        ("slow", "unverified"),
        ("next", "pass"),
    ]
    assert not (repository / "tmp").exists()


def test_python_environment_preparation_failure_preserves_separate_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "scripts").mkdir(parents=True)
    (repository / "scripts" / "tasks.py").write_text(
        "# task runner\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "classify_review_tools",
        lambda checks: (list(checks), []),
    )
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda destination: (
            (destination / "scripts").mkdir(parents=True),
            (destination / "scripts" / "tasks.py").write_text(
                "# task runner\n",
                encoding="utf-8",
            ),
            [],
        )[-1],
    )
    monkeypatch.setattr(tasks, "command_output", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0),
    )
    child_calls: list[str] = []

    def fail_preparation(
        args: list[str] | tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        child_calls.append(args[-1])
        return subprocess.CompletedProcess(
            args,
            1,
            stdout=b"",
            stderr=b"Failed to fetch package index: connection timed out",
        )

    monkeypatch.setattr(tasks, "run_isolated_review_command", fail_preparation)

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("qa-app", "qa-app", ("git", "uv")),
            tasks.ReviewCheck("test-hooks", "test-hooks", ("git", "uv", "lefthook")),
        )
    )

    assert child_calls == ["prepare-review-python-env"]
    assert [(result.name, result.status) for result in results] == [
        ("qa-app", "unverified"),
        ("test-hooks", "unverified"),
    ]
    assert all(
        result.detail.startswith("Python environment preparation failed:")
        for result in results
    )


def test_compile_aw_is_unverified_without_origin_and_other_checks_continue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "scripts").mkdir(parents=True)
    (repository / "scripts" / "tasks.py").write_text(
        "# task runner\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "classify_review_tools",
        lambda checks: (list(checks), []),
    )
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda destination: (
            (destination / "scripts").mkdir(parents=True),
            (destination / "scripts" / "tasks.py").write_text(
                "# task runner\n",
                encoding="utf-8",
            ),
            [],
        )[-1],
    )
    monkeypatch.setattr(tasks, "command_output", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0),
    )
    child_calls: list[str] = []
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda args, **_kwargs: (
            child_calls.append(args[-1]) or subprocess.CompletedProcess(args, 0)
        ),
    )

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("compile-aw", "compile-aw", ("git", "gh")),
            tasks.ReviewCheck("next", "next", ("git",)),
        )
    )

    assert child_calls == ["next"]
    assert [(result.name, result.status) for result in results] == [
        ("compile-aw", "unverified"),
        ("next", "pass"),
    ]


def test_timed_out_process_tree_releases_isolation(tmp_path: Path) -> None:
    isolation = tmp_path / "isolation"
    isolation.mkdir()
    child_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        tasks.run_isolated_review_command(
            [sys.executable, "-c", child_code],
            cwd=isolation,
            env={},
            timeout=0.5,
        )

    isolation.rmdir()
    assert not isolation.exists()


def test_isolated_command_streams_output_and_keeps_only_bounded_tail(
    tmp_path: Path,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    output = b"prefix-" + (b"x" * (tasks.REVIEW_LOG_TAIL_BYTES + 1024))
    code = (
        "import sys; sys.stdout.buffer.write("
        f"b'prefix-' + b'x' * {tasks.REVIEW_LOG_TAIL_BYTES + 1024})"
    )

    completed = tasks.run_isolated_review_command(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={},
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == output[-tasks.REVIEW_LOG_TAIL_BYTES :]
    assert len(completed.stdout) == tasks.REVIEW_LOG_TAIL_BYTES
    assert capsysbinary.readouterr().out == output


@pytest.mark.parametrize(
    ("log", "reason"),
    [
        ("Cannot connect to the Docker daemon. Is it running?", "Docker daemon"),
        (
            "Error pulling image: failed to pull image ghcr.io/example/test",
            "image pull",
        ),
        ("dial tcp: lookup ghcr.io: no such host", "DNS resolution"),
        ("SSL certificate problem: unable to get local issuer certificate", "TLS"),
        ("Failed to fetch package index: connection timed out", "package index"),
        ("Connection refused while contacting registry.example", "network connection"),
        ("Unable to download Bicep CLI", "Azure CLI"),
    ],
)
def test_explicit_environment_failures_are_unverified(
    log: str,
    reason: str,
) -> None:
    completed = subprocess.CompletedProcess(
        ["check"],
        1,
        stdout=b"",
        stderr=log.encode(),
    )

    status, reason_code, detail = tasks.classify_review_failure(
        tasks.ReviewCheck("check", "check", ()),
        completed,
    )

    assert status == "unverified"
    assert reason_code == tasks.REVIEW_REASON_ENVIRONMENT_FAILURE
    assert reason in detail


def test_baseline_four_pytest_assertion_failures_remain_fail() -> None:
    completed = subprocess.CompletedProcess(
        ["check"],
        1,
        stdout=b"",
        stderr=(
            b"FAILED scripts/tests/test_lefthook.py::test_first - AssertionError\n"
            b"FAILED scripts/tests/test_lefthook.py::test_second - AssertionError\n"
            b"FAILED scripts/tests/test_lefthook.py::test_third - AssertionError\n"
            b"FAILED scripts/tests/test_uv_workflow.py::test_fourth - AssertionError\n"
            b"E       assert 4 == 0\n"
            b"4 failed, 106 passed\n"
        ),
    )

    status, reason_code, detail = tasks.classify_review_failure(
        tasks.ReviewCheck("test-hooks", "test-hooks", ()),
        completed,
    )

    assert status == "fail"
    assert reason_code == tasks.REVIEW_REASON_REPOSITORY_FAILURE
    assert "repository-related" in detail


def test_compile_aw_fails_when_the_compile_run_rewrites_a_managed_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repository"
    (repository / ".github/workflows").mkdir(parents=True)
    lock_file = repository / ".github/workflows/example.lock.yml"
    lock_file.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "target_check_gh_aw", lambda: None)
    monkeypatch.setattr(
        tasks,
        "run_gh_aw_compile",
        lambda: lock_file.write_text("regenerated\n", encoding="utf-8"),
    )

    with pytest.raises(SystemExit) as error:
        tasks.target_compile_aw()

    assert error.value.code == 1
    output = capsys.readouterr()
    assert "generated artifacts changed during this compile run" in output.err
    assert ".github/workflows/example.lock.yml" in output.err


def test_compile_aw_ignores_uncommitted_changes_the_compiler_does_not_make(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".github/workflows").mkdir(parents=True)
    # A managed path can carry an uncommitted edit the compiler never rewrites
    # (for example a hand-written comment in dependabot.yml). That is not a
    # stale generated artifact and must not be reported as one.
    dependabot = repository / ".github/dependabot.yml"
    dependabot.write_text("version: 2\n# hand-written comment\n", encoding="utf-8")
    (repository / ".github/workflows/example.lock.yml").write_text(
        "compiled\n", encoding="utf-8"
    )
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "target_check_gh_aw", lambda: None)
    monkeypatch.setattr(tasks, "run_gh_aw_compile", lambda: None)

    tasks.target_compile_aw()

    assert dependabot.read_text(encoding="utf-8").endswith("# hand-written comment\n")


def test_compile_aw_checks_all_compiler_managed_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".github/aw").mkdir(parents=True)
    (repository / ".github/workflows").mkdir(parents=True)
    managed_files = {
        ".github/aw/actions-lock.json": "{}\n",
        ".github/dependabot.yml": "version: 2\n",
        ".github/workflows/agentics-maintenance.yml": "name: maintenance\n",
        ".github/workflows/example.lock.yml": "compiled\n",
    }
    for relative, content in managed_files.items():
        (repository / relative).write_text(content, encoding="utf-8")
    (repository / ".github/workflows/ci.yml").write_text("name: ci\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", repository)

    digests = tasks.gh_aw_managed_file_digests()

    assert sorted(digests) == sorted(managed_files)


def test_review_index_parser_preserves_nul_delimited_paths_and_stages() -> None:
    oid = b"a" * 40
    output = (
        b"100644 " + oid + b" 1\tconflict\nname.txt\0"
        b"100755 " + oid + b" 2\tconflict\nname.txt\0"
    )

    assert tasks.parse_review_index_entries(output) == [
        ("conflict\nname.txt", "100644", 1, "a" * 40),
        ("conflict\nname.txt", "100755", 2, "a" * 40),
    ]


def test_review_fingerprint_records_deleted_and_untracked_files(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    tracked = repository / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    git = tasks.resolve_command(["git"])[0]
    subprocess.run([git, "init", "--quiet"], cwd=repository, check=True)
    subprocess.run([git, "add", "tracked.txt"], cwd=repository, check=True)
    tracked.unlink()
    untracked = repository / "untracked name.txt"
    untracked.write_text("untracked\n", encoding="utf-8")

    fingerprint = tasks.capture_review_fingerprint(repository)

    assert "timestamp" not in fingerprint
    assert fingerprint["tracked_index"][0]["path"] == "tracked.txt"
    assert fingerprint["tracked_index"][0]["worktree_kind"] == "deleted"
    assert fingerprint["tracked_index"][0]["content_sha256"] is None
    assert fingerprint["untracked"][0]["path"] == "untracked name.txt"
    assert (
        fingerprint["untracked"][0]["content_sha256"]
        == hashlib.sha256(untracked.read_bytes()).hexdigest()
    )


def test_review_fingerprint_hashes_symlink_target_without_following(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFLNK,
        st_size=10,
        st_mtime_ns=1,
        st_ino=2,
    )

    class FakeSymlink:
        def lstat(self) -> SimpleNamespace:
            return metadata

    symlink = FakeSymlink()
    monkeypatch.setattr(
        tasks,
        "fingerprint_worktree_path",
        lambda _root, _relative: symlink,
    )
    monkeypatch.setattr(tasks.os, "readlink", lambda _path: "target.txt")

    kind, digest = tasks.fingerprint_worktree_content(tmp_path, "link.txt")

    assert kind == "symlink"
    assert digest == hashlib.sha256(os.fsencode("target.txt")).hexdigest()


def test_review_fingerprint_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(tasks.ReviewSnapshotError):
        tasks.fingerprint_worktree_path(tmp_path, "../outside.txt")


def test_review_fingerprint_compare_reports_only_paths_and_hashes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_entry = {
        "path": "secret.txt",
        "worktree_kind": "file",
        "content_sha256": "a" * 64,
    }
    before_entry["entry_sha256"] = tasks.fingerprint_sha256(before_entry)
    after_entry = {
        "path": "secret.txt",
        "worktree_kind": "file",
        "content_sha256": "b" * 64,
    }
    after_entry["entry_sha256"] = tasks.fingerprint_sha256(after_entry)
    before_content = {"tracked_index": [before_entry], "untracked": []}
    after_content = {"tracked_index": [after_entry], "untracked": []}
    before_path.write_text(
        json.dumps(
            {
                "schema_version": tasks.REVIEW_FINGERPRINT_SCHEMA_VERSION,
                "repository_root": "C:\\repo",
                **before_content,
                "fingerprint_sha256": tasks.fingerprint_sha256(before_content),
            }
        ),
        encoding="utf-8",
    )
    after_path.write_text(
        json.dumps(
            {
                "schema_version": tasks.REVIEW_FINGERPRINT_SCHEMA_VERSION,
                "repository_root": "C:\\repo",
                **after_content,
                "fingerprint_sha256": tasks.fingerprint_sha256(after_content),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        tasks.compare_review_fingerprints(before_path, after_path)

    assert error.value.code == 1
    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "fail"
    assert result["changes"][0]["path"] == "secret.txt"
    assert set(result["changes"][0]) == {
        "path",
        "before_sha256",
        "after_sha256",
    }


def test_review_fingerprint_rejects_tampered_entry(
    tmp_path: Path,
) -> None:
    fingerprint_path = tmp_path / "fingerprint.json"
    entry = {
        "path": "file.txt",
        "worktree_kind": "file",
        "content_sha256": "a" * 64,
    }
    entry["entry_sha256"] = tasks.fingerprint_sha256(entry)
    content = {"tracked_index": [], "untracked": [entry]}
    fingerprint = {
        "schema_version": tasks.REVIEW_FINGERPRINT_SCHEMA_VERSION,
        "repository_root": "C:\\repo",
        **content,
        "fingerprint_sha256": tasks.fingerprint_sha256(content),
    }
    entry["content_sha256"] = "b" * 64
    fingerprint_path.write_text(json.dumps(fingerprint), encoding="utf-8")

    with pytest.raises(tasks.ReviewSnapshotError, match="entry hash does not match"):
        tasks.load_review_fingerprint(fingerprint_path)


def test_review_fingerprint_cli_dispatches_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "fingerprint.json"
    calls: list[Path] = []
    monkeypatch.setattr(
        tasks,
        "target_review_fingerprint_capture",
        lambda output: calls.append(output),
    )

    assert (
        tasks.main(
            [
                "review-fingerprint",
                "capture",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    assert calls == [output_path]


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific gh-aw process boundary")
def test_windows_gh_aw_compile_uses_powershell_process_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        tasks.shutil,
        "which",
        lambda command: (
            "C:\\tools\\powershell.exe" if command == "powershell.exe" else None
        ),
    )
    monkeypatch.setattr(
        tasks,
        "resolve_command",
        lambda args: ["C:\\tools\\gh.exe"] if list(args) == ["gh"] else list(args),
    )

    def record_command(
        args: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        observed["args"] = tuple(args)
        observed["cwd"] = cwd
        observed["timeout"] = timeout
        observed["gh_path"] = env["GH_AW_GH_PATH"]
        observed["root"] = env["GH_AW_ROOT"]
        observed["stdin_empty"] = Path(env["GH_AW_STDIN"]).read_bytes() == b""
        observed["temporary_parent"] = Path(env["GH_AW_STDIN"]).parent
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tasks, "run_isolated_review_command", record_command)

    tasks.run_gh_aw_compile()

    command = observed["args"]
    assert isinstance(command, tuple)
    assert command[:3] == (
        "C:\\tools\\powershell.exe",
        "-NoProfile",
        "-NonInteractive",
    )
    script = command[-1]
    assert isinstance(script, str)
    assert "Start-Process" in script
    assert observed["cwd"] == tasks.ROOT
    assert observed["timeout"] == tasks.REVIEW_GH_AW_COMPILE_TIMEOUT_SECONDS
    assert observed["gh_path"] == "C:\\tools\\gh.exe"
    assert observed["root"] == str(tasks.ROOT)
    assert observed["stdin_empty"] is True
    temporary_parent = observed["temporary_parent"]
    assert isinstance(temporary_parent, Path)
    assert not temporary_parent.exists()


def test_command_nul_output_preserves_git_path_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = b" leading.txt\0trailing.txt \0line\nbreak.txt\0"

    def completed_run(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(["git"], 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(tasks.subprocess, "run", completed_run)

    assert tasks.command_nul_output(["git", "ls-files", "-z"]) == [
        " leading.txt",
        "trailing.txt ",
        "line\nbreak.txt",
    ]


def test_snapshot_rejects_path_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    destination = tmp_path / "snapshot"
    repository.mkdir()
    destination.mkdir()
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["../escape.txt"] if tuple(args) == ("git", "ls-files", "-z") else []
        ),
    )

    with pytest.raises(tasks.ReviewSnapshotError, match="unsafe repository path"):
        tasks.copy_worktree_snapshot(destination)

    assert not (tmp_path / "escape.txt").exists()


def test_snapshot_skips_oversized_untracked_file_as_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    destination = tmp_path / "snapshot"
    repository.mkdir()
    destination.mkdir()
    (repository / "large.bin").write_bytes(b"oversized")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks, "REVIEW_MAX_UNTRACKED_FILE_BYTES", 4)
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["large.bin"]
            if tuple(args)
            == ("git", "ls-files", "--others", "--exclude-standard", "-z")
            else []
        ),
    )

    issues = tasks.copy_worktree_snapshot(destination)

    assert not (destination / "large.bin").exists()
    assert issues == [
        "untracked file exceeds the 4-byte snapshot limit and was skipped: large.bin"
    ]


def test_snapshot_skips_symlink_as_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    destination = tmp_path / "snapshot"
    repository.mkdir()
    destination.mkdir()
    target = repository / "target.txt"
    link = repository / "link.txt"
    target.write_text("target\n", encoding="utf-8")
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is not available")
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["link.txt"]
            if tuple(args)
            == ("git", "ls-files", "--others", "--exclude-standard", "-z")
            else []
        ),
    )

    issues = tasks.copy_worktree_snapshot(destination)

    assert not (destination / "link.txt").exists()
    assert issues == ["symlink cannot be safely isolated and was skipped: link.txt"]


@pytest.mark.parametrize(
    "snapshot_issue",
    [
        "symlink cannot be safely isolated and was skipped: link.txt",
        "untracked file exceeds the snapshot limit and was skipped: large.bin",
        "non-regular file cannot be safely isolated and was skipped: pipe",
    ],
)
def test_incomplete_snapshot_marks_all_full_checks_unverified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot_issue: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(tasks, "ROOT", repository)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: f"mock-{command}")
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda destination: [snapshot_issue],
    )
    monkeypatch.setattr(
        tasks,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "git setup must not run for an incomplete snapshot"
        ),
    )
    monkeypatch.setattr(
        tasks,
        "run_isolated_review_command",
        lambda *_args, **_kwargs: pytest.fail(
            "full checks must not run for an incomplete snapshot"
        ),
    )

    results = tasks.run_review_targets_isolated(
        (
            tasks.ReviewCheck("first", "first", ("git",)),
            tasks.ReviewCheck("second", "second", ("git",)),
        )
    )

    assert [(result.name, result.status) for result in results] == [
        ("snapshot", "unverified"),
        ("first", "unverified"),
        ("second", "unverified"),
    ]
    assert not (repository / "tmp").exists()


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh, empty directory monkeypatched in as ``tasks.ROOT``, for
    review-workspace tests that only need a stand-in repository root rather
    than a real git checkout."""
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    monkeypatch.setattr(tasks, "ROOT", repository_path)
    return repository_path


@pytest.fixture
def confined_mkdtemp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``tempfile.mkdtemp`` create its directory under ``tmp_path``
    instead of the real OS temp dir, so tests can scope their cleanup
    assertions (``tmp_path.glob(...)``) to files they actually created."""
    real_mkdtemp = tasks.tempfile.mkdtemp
    monkeypatch.setattr(
        tasks.tempfile,
        "mkdtemp",
        lambda prefix: real_mkdtemp(prefix=prefix, dir=tmp_path),
    )


def _write_review_workspace_manifest(
    workspace_path: Path, *, repository_root: Path, token: object
) -> None:
    """Write a manifest for a hand-built workspace fixture, as a sibling of
    ``workspace_path`` -- matching where ``create_review_workspace`` writes
    it (never inside the workspace; see ``_review_workspace_manifest_path``)
    -- so ``cleanup_review_workspace`` finds it at the same path it would
    for a real workspace."""
    workspace_path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": tasks.REVIEW_WORKSPACE_SCHEMA_VERSION,
        "token": token,
        "repository_root": str(repository_root),
        "workspace_path": str(workspace_path),
    }
    (workspace_path.parent / tasks.REVIEW_WORKSPACE_MANIFEST_FILENAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_review_workspace_create_writes_a_correct_manifest_and_leaves_repository_intact(
    repository: Path, confined_mkdtemp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_review_workspace delegates the actual tracked/untracked copy
    behavior to copy_worktree_snapshot (already exhaustively covered by
    test_isolated_review_copies_current_tracked_and_untracked_files_for_tests
    and proven to be the exact same function via
    test_review_workspace_create_reuses_shared_snapshot_and_git_bootstrap_helpers),
    so this only has to prove the parts unique to create_review_workspace: a
    single copied file lands correctly, git bootstrap runs, the manifest is
    correct, and the original repository is left untouched."""
    scripts_directory = repository / "scripts"
    scripts_directory.mkdir(parents=True)
    (scripts_directory / "tasks.py").write_text("# task runner\n", encoding="utf-8")
    monkeypatch.setattr(
        tasks,
        "command_nul_output",
        lambda args, **_kwargs: (
            ["scripts/tasks.py"] if tuple(args) == ("git", "ls-files", "-z") else []
        ),
    )
    monkeypatch.setattr(tasks, "command_output", lambda *_a, **_k: "")
    run_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: (
            run_calls.append(tuple(args)) or subprocess.CompletedProcess(args, 0)
        ),
    )

    workspace = tasks.create_review_workspace()
    workspace_path = Path(workspace["workspace_path"])
    try:
        assert workspace_path.is_absolute()
        assert not workspace_path.is_relative_to(repository.resolve())
        assert (workspace_path / "scripts" / "tasks.py").read_text(
            encoding="utf-8"
        ) == "# task runner\n"
        assert workspace["snapshot_issues"] == []
        assert run_calls[0][:2] == ("git", "init")
        assert "commit" in run_calls[-1]

        manifest_path = Path(workspace["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["schema"] == tasks.REVIEW_WORKSPACE_SCHEMA_VERSION
        assert manifest["token"] == workspace["token"]
        assert manifest["repository_root"] == str(repository.resolve())
        assert manifest["workspace_path"] == str(workspace_path)
    finally:
        tasks.cleanup_review_workspace(workspace_path, workspace["token"])

    assert not workspace_path.exists()
    assert (scripts_directory / "tasks.py").read_text(encoding="utf-8") == (
        "# task runner\n"
    )


def test_review_workspace_create_produces_a_real_isolated_workspace() -> None:
    """Real, unpatched create_review_workspace against this repository:
    the manifest must sit beside, not inside, the workspace (else it's an
    untracked true_gap that fails check-repo-health from inside the
    workspace), and ROOT must resolve to the copied workspace rather than
    back to this repository, so nested isolation is safe by construction."""
    workspace = tasks.create_review_workspace()
    workspace_path = Path(workspace["workspace_path"])
    try:
        assert not (workspace_path / tasks.REVIEW_WORKSPACE_MANIFEST_FILENAME).exists()

        completed = subprocess.run(
            [
                sys.executable,
                str(workspace_path / "scripts" / "tasks.py"),
                "check-repo-health",
            ],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, (
            "check-repo-health failed inside the isolated review workspace:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
        assert "0 true gaps" in completed.stdout, (
            "expected zero true gaps (the manifest must not be an untracked, "
            f"unrecognized file inside the workspace); got:\n{completed.stdout}"
        )

        copied_tasks_path = workspace_path / "scripts" / "tasks.py"
        spec = importlib.util.spec_from_file_location(
            "review_repository_tasks_isolated_workspace_copy", copied_tasks_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            del sys.modules[spec.name]

        assert workspace_path == module.ROOT
        assert REPO_ROOT.resolve() != module.ROOT
    finally:
        tasks.cleanup_review_workspace(workspace_path, workspace["token"])


def test_review_workspace_create_reuses_shared_snapshot_and_git_bootstrap_helpers(
    repository: Path, confined_mkdtemp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_review_workspace must call the same copy_worktree_snapshot and
    initialize_isolated_git_repository helpers review-repo-full already uses
    internally, rather than duplicating copy/path-traversal/symlink or git
    bootstrap logic."""
    monkeypatch.setattr(tasks, "command_nul_output", lambda *_a, **_k: [])
    snapshot_calls: list[Path] = []
    real_snapshot = tasks.copy_worktree_snapshot

    def spy_snapshot(destination: Path) -> list[str]:
        snapshot_calls.append(destination)
        return real_snapshot(destination)

    monkeypatch.setattr(tasks, "copy_worktree_snapshot", spy_snapshot)
    git_init_calls: list[Path] = []

    def spy_git_init(isolated_root: Path) -> str:
        git_init_calls.append(isolated_root)
        return ""

    monkeypatch.setattr(tasks, "initialize_isolated_git_repository", spy_git_init)

    workspace = tasks.create_review_workspace()
    workspace_path = Path(workspace["workspace_path"])
    try:
        assert snapshot_calls == [workspace_path]
        assert git_init_calls == [workspace_path]
    finally:
        tasks.cleanup_review_workspace(workspace_path, workspace["token"])


def test_review_workspace_create_rejects_workspace_inside_repository(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_mkdtemp = tasks.tempfile.mkdtemp
    monkeypatch.setattr(
        tasks.tempfile,
        "mkdtemp",
        lambda prefix: real_mkdtemp(prefix=prefix, dir=repository),
    )
    monkeypatch.setattr(
        tasks,
        "copy_worktree_snapshot",
        lambda _destination: pytest.fail("snapshot copy must not run"),
    )
    monkeypatch.setattr(
        tasks,
        "initialize_isolated_git_repository",
        lambda _isolated_root: pytest.fail("git setup must not run"),
    )

    with pytest.raises(
        tasks.ReviewSnapshotError,
        match="operating-system temporary directory is inside the repository",
    ):
        tasks.create_review_workspace()

    assert not any(repository.iterdir())


def _snapshot_raises(destination: Path) -> list[str]:
    (destination / "partial.txt").write_text("partial\n", encoding="utf-8")
    raise tasks.ReviewSnapshotError("boom")


def _snapshot_reports_issues(destination: Path) -> list[str]:
    (destination / "partial.txt").write_text("partial\n", encoding="utf-8")
    return ["skipped a broken symlink: dangling"]


@pytest.mark.parametrize(
    ("snapshot_fn", "expected_match"),
    [
        (_snapshot_raises, "boom"),
        (_snapshot_reports_issues, "skipped a broken symlink: dangling"),
    ],
    ids=["snapshot-raises", "snapshot-reports-non-empty-issues"],
)
def test_review_workspace_create_fails_and_cleans_up_when_snapshot_is_incomplete(
    repository: Path,
    confined_mkdtemp: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_fn: object,
    expected_match: str,
) -> None:
    """copy_worktree_snapshot can fail either by raising or by returning a
    non-empty ``issues`` list without raising; create_review_workspace must
    treat both the same way -- a partially built workspace is not a partial
    success, so creation fails with ReviewSnapshotError and removes the
    temporary parent either way, and git bootstrap must not run."""
    monkeypatch.setattr(tasks, "copy_worktree_snapshot", snapshot_fn)
    monkeypatch.setattr(
        tasks,
        "initialize_isolated_git_repository",
        lambda _isolated_root: pytest.fail(
            "git setup must not run after an incomplete snapshot"
        ),
    )

    with pytest.raises(tasks.ReviewSnapshotError, match=expected_match):
        tasks.create_review_workspace()

    assert not any(tmp_path.glob("review-repo-workspace-*"))


_BOOTSTRAP_TIMEOUT = subprocess.TimeoutExpired(cmd=["git", "commit"], timeout=30)
_BOOTSTRAP_UNEXPECTED = ValueError("unexpected git bootstrap failure")


def _fails_manifest_path(workspace_root: Path) -> Path:
    return workspace_root.parent / "missing-subdirectory" / "manifest.json"


@pytest.mark.parametrize(
    ("bootstrap_error", "manifest_path_override", "expected_exception", "match"),
    [
        (
            _BOOTSTRAP_TIMEOUT,
            None,
            tasks.ReviewSnapshotError,
            "timed out after 30 seconds",
        ),
        (KeyboardInterrupt(), None, KeyboardInterrupt, None),
        (
            _BOOTSTRAP_UNEXPECTED,
            None,
            tasks.ReviewSnapshotError,
            "unexpected git bootstrap failure",
        ),
        (
            None,
            _fails_manifest_path,
            tasks.ReviewSnapshotError,
            "failed to prepare the isolated review workspace",
        ),
    ],
    ids=[
        "subprocess-timeout",
        "keyboard-interrupt",
        "unexpected-exception",
        "manifest-write-failure",
    ],
)
def test_review_workspace_create_cleans_up_on_any_preparation_failure(
    repository: Path,
    confined_mkdtemp: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_error: BaseException | None,
    manifest_path_override: object,
    expected_exception: type[BaseException],
    match: str | None,
) -> None:
    """Every preparation failure (subprocess timeout, interrupt, unexpected
    exception, or manifest-write failure) must remove the temporary parent
    before propagating. KeyboardInterrupt propagates unchanged; every other
    failure is translated into a ReviewSnapshotError chained via
    __cause__, so callers only need to handle one failure type."""
    monkeypatch.setattr(tasks, "copy_worktree_snapshot", lambda _destination: [])
    if bootstrap_error is not None:

        def _raiser(
            _isolated_root: Path, _error: BaseException = bootstrap_error
        ) -> str:
            raise _error

        monkeypatch.setattr(tasks, "initialize_isolated_git_repository", _raiser)
    else:
        monkeypatch.setattr(tasks, "initialize_isolated_git_repository", lambda _r: "")
        monkeypatch.setattr(
            tasks, "_review_workspace_manifest_path", manifest_path_override
        )

    with pytest.raises(expected_exception, match=match) as excinfo:
        tasks.create_review_workspace()

    if bootstrap_error is not None:
        if expected_exception is not KeyboardInterrupt:
            assert excinfo.value.__cause__ is bootstrap_error
    else:
        assert isinstance(excinfo.value.__cause__, OSError)
    assert not any(tmp_path.glob("review-repo-workspace-*"))


def test_review_workspace_cleanup_removes_workspace_and_is_not_idempotent(
    repository: Path, tmp_path: Path
) -> None:
    workspace_path = tmp_path / "review-workspace-parent" / "workspace"
    token = "a" * 32
    _write_review_workspace_manifest(
        workspace_path, repository_root=repository.resolve(), token=token
    )
    (workspace_path / "extra.txt").write_text("copied file\n", encoding="utf-8")

    removed = tasks.cleanup_review_workspace(workspace_path, token)

    assert removed == str(workspace_path.resolve())
    assert not workspace_path.exists()
    assert not workspace_path.parent.exists()

    # Repeating cleanup on an already-removed workspace must fail explicitly
    # rather than silently succeed a second time.
    with pytest.raises(tasks.ReviewSnapshotError, match="does not exist"):
        tasks.cleanup_review_workspace(workspace_path, token)


@pytest.mark.parametrize(
    ("build_candidate", "expected_match"),
    [
        (lambda repository: repository, "repository root or one of its ancestors"),
        (
            lambda repository: repository.parent,
            "repository root or one of its ancestors",
        ),
        (
            lambda repository: repository / "subdir",
            "path inside the repository worktree",
        ),
        (
            lambda repository: repository / "subdir" / ".." / "subdir",
            "path inside the repository worktree",
        ),
    ],
    ids=[
        "repository-root",
        "ancestor-of-repository-root",
        "inside-repository",
        "traversal-that-resolves-inside-repository",
    ],
)
def test_review_workspace_cleanup_rejects_paths_not_disjoint_from_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_candidate: Callable[[Path], Path],
    expected_match: str,
) -> None:
    """A workspace_path that equals, contains (is an ancestor of), or is
    contained by (including via a ``..`` traversal that resolve(strict=True)
    normalizes before any check runs) the repository root must always be
    rejected before cleanup ever looks for a manifest, and the repository
    must be left untouched."""
    nested = tmp_path / "nested"
    repository = nested / "repository"
    (repository / "subdir").mkdir(parents=True)
    monkeypatch.setattr(tasks, "ROOT", repository)

    with pytest.raises(tasks.ReviewSnapshotError, match=expected_match):
        tasks.cleanup_review_workspace(build_candidate(repository), "irrelevant-token")

    assert (repository / "subdir").exists()


def test_review_workspace_cleanup_rejects_arbitrary_path_without_manifest(
    repository: Path, tmp_path: Path
) -> None:
    arbitrary = tmp_path / "not-a-review-workspace"
    arbitrary.mkdir()
    (arbitrary / "innocuous.txt").write_text("data\n", encoding="utf-8")

    with pytest.raises(
        tasks.ReviewSnapshotError, match="manifest is missing or unreadable"
    ):
        tasks.cleanup_review_workspace(arbitrary, "irrelevant-token")

    assert arbitrary.exists()
    assert (arbitrary / "innocuous.txt").exists()


@pytest.mark.parametrize(
    ("manifest_token", "supplied_token", "expected_match"),
    [
        ("f" * 32, "e" * 32, "token does not match"),  # correctly shaped, wrong value
        ("f" * 32, "caf\u00e9" + "a" * 28, "token does not match"),  # non-ASCII
        (123456, "f" * 32, "token does not match"),  # tampered: not a string
        (None, "f" * 32, "token does not match"),  # tampered: missing field
    ],
    ids=[
        "value-mismatch",
        "non-ascii-supplied-token",
        "non-string-manifest-token",
        "missing-manifest-token",
    ],
)
def test_review_workspace_cleanup_rejects_token_mismatch_without_raising(
    repository: Path,
    tmp_path: Path,
    manifest_token: object,
    supplied_token: str,
    expected_match: str,
) -> None:
    """The manifest token is a plaintext equality check, not authentication:
    it only has to fail closed -- with a clean ReviewSnapshotError, never a
    raw TypeError/AttributeError -- whether the mismatch is an ordinary
    wrong value, a non-ASCII supplied token, or a manifest token corrupted
    into a non-string (or missing) JSON value."""
    workspace_path = tmp_path / "workspace"
    _write_review_workspace_manifest(
        workspace_path, repository_root=repository.resolve(), token=manifest_token
    )

    with pytest.raises(tasks.ReviewSnapshotError, match=expected_match):
        tasks.cleanup_review_workspace(workspace_path, supplied_token)

    assert workspace_path.exists()


def _valid_manifest(repository_root: Path, workspace_path: Path) -> dict[str, object]:
    return {
        "schema": tasks.REVIEW_WORKSPACE_SCHEMA_VERSION,
        "token": "f" * 32,
        "repository_root": str(repository_root),
        "workspace_path": str(workspace_path),
    }


@pytest.mark.parametrize(
    ("manifest_content", "expected_match"),
    [
        ("not json at all", "not valid JSON"),
        ('"just a string"', "not a JSON object"),
        ("[1, 2, 3]", "not a JSON object"),
        ("42", "not a JSON object"),
        ("true", "not a JSON object"),
        ("null", "not a JSON object"),
        (
            lambda repository_root, workspace_path: {
                **_valid_manifest(repository_root, workspace_path),
                "schema": "unrecognized-schema/0",
            },
            "schema is not recognized",
        ),
        (
            lambda repository_root, workspace_path: {
                **_valid_manifest(repository_root, workspace_path),
                "repository_root": str(repository_root.parent),
            },
            "not created for this repository",
        ),
        (
            lambda repository_root, workspace_path: {
                **_valid_manifest(repository_root, workspace_path),
                "workspace_path": str(workspace_path.parent),
            },
            "does not match the requested path",
        ),
    ],
    ids=[
        "invalid-json",
        "json-string",
        "json-array",
        "json-number",
        "json-boolean",
        "json-null",
        "unrecognized-schema",
        "wrong-repository",
        "wrong-workspace-path",
    ],
)
def test_review_workspace_cleanup_rejects_malformed_manifest_content(
    repository: Path,
    tmp_path: Path,
    manifest_content: str | Callable[[Path, Path], dict[str, object]],
    expected_match: str,
) -> None:
    """A manifest that is not valid JSON, not a JSON object, from an
    unrecognized schema version, written for a different repository, or
    bound to a different workspace path must all be rejected with a clean
    ReviewSnapshotError instead of proceeding to delete anything."""
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir(parents=True)
    manifest_path = workspace_path.parent / tasks.REVIEW_WORKSPACE_MANIFEST_FILENAME
    if isinstance(manifest_content, str):
        manifest_path.write_text(manifest_content, encoding="utf-8")
    else:
        manifest_path.write_text(
            json.dumps(manifest_content(repository.resolve(), workspace_path)),
            encoding="utf-8",
        )

    with pytest.raises(tasks.ReviewSnapshotError, match=expected_match):
        tasks.cleanup_review_workspace(workspace_path, "f" * 32)

    assert workspace_path.exists()


def test_review_workspace_cleanup_rejects_workspace_path_replaced_by_a_symlink(
    repository: Path, tmp_path: Path
) -> None:
    """If the workspace_path directory is replaced by a symlink to an
    unrelated victim directory after creation (a classic TOCTOU
    substitution attempt), cleanup resolves that symlink and then looks for
    the manifest beside *its* resolved target, not beside the original
    workspace_path. Without a manifest proving that resolved location was
    ours, cleanup must refuse to touch it, and the victim directory must
    survive untouched."""
    victim = tmp_path / "victim-parent" / "victim"
    victim.mkdir(parents=True)
    (victim / "precious.txt").write_text("do not delete me\n", encoding="utf-8")
    workspace_path = tmp_path / "workspace"
    workspace_path.symlink_to(victim, target_is_directory=True)

    with pytest.raises(
        tasks.ReviewSnapshotError, match="manifest is missing or unreadable"
    ):
        tasks.cleanup_review_workspace(workspace_path, "irrelevant-token")

    assert victim.exists()
    assert (victim / "precious.txt").read_text(encoding="utf-8") == (
        "do not delete me\n"
    )


def test_review_workspace_cleanup_does_not_follow_a_symlink_planted_inside_workspace(
    repository: Path, tmp_path: Path
) -> None:
    """A symlink planted inside an otherwise legitimately created workspace,
    pointing at an external victim directory, must not cause that victim's
    contents to be deleted when the real workspace is cleaned up:
    shutil.rmtree unlinks symlinks it encounters rather than recursing
    through them."""
    victim = tmp_path / "victim-parent" / "victim"
    victim.mkdir(parents=True)
    (victim / "precious.txt").write_text("do not delete me\n", encoding="utf-8")
    workspace_path = tmp_path / "review-workspace-parent" / "workspace"
    token = "e" * 32
    _write_review_workspace_manifest(
        workspace_path, repository_root=repository.resolve(), token=token
    )
    (workspace_path / "escape-link").symlink_to(victim, target_is_directory=True)

    removed = tasks.cleanup_review_workspace(workspace_path, token)

    assert removed == str(workspace_path.resolve())
    assert not workspace_path.exists()
    assert victim.exists()
    assert (victim / "precious.txt").read_text(encoding="utf-8") == (
        "do not delete me\n"
    )


def test_review_workspace_rmtree_onexc_clears_read_only_bit_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The onexc handler must clear the read-only attribute Git can leave on
    packed object files (blocking deletion on Windows) and retry the failed
    operation, widening the mode (``current_mode | S_IWRITE``) rather than
    replacing it outright, so unrelated bits (e.g. S_IROTH) survive.
    ``os.unlink`` is monkeypatched to a stub gated on write access, since
    POSIX unlink is governed by the containing directory's permissions, not
    the file's own mode -- a purely filesystem-driven version of this test
    would not reliably fail before the fix on non-Windows platforms."""
    target = tmp_path / "read-only-file.txt"
    target.write_text("payload\n", encoding="utf-8")
    initial_mode = stat.S_IREAD | stat.S_IROTH
    target.chmod(initial_mode)
    calls: list[str] = []
    modes_seen: list[int] = []
    real_unlink = os.unlink

    def fake_unlink(path: str) -> None:
        calls.append(path)
        modes_seen.append(stat.S_IMODE(os.stat(path).st_mode))
        if not os.access(path, os.W_OK):
            raise PermissionError(f"simulated permission error for {path}")
        real_unlink(path)

    monkeypatch.setattr(os, "unlink", fake_unlink)

    tasks._review_workspace_rmtree_onexc(os.unlink, str(target), PermissionError())

    assert calls == [str(target)]
    assert not target.exists()
    # The retry must observe read/write for the owner and the original
    # other-read bit still set -- proof the fix widens the mode rather than
    # replacing it with just S_IWRITE (which would strip S_IROTH).
    assert modes_seen == [stat.S_IMODE(initial_mode | stat.S_IWRITE)]


def test_review_workspace_rmtree_onexc_propagates_when_retry_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If clearing the read-only bit does not resolve the failure (for
    example a permission problem unrelated to the read-only attribute), the
    handler must let the retry's exception propagate rather than swallow
    it, so shutil.rmtree fails loudly instead of silently leaving files
    behind."""
    target = tmp_path / "still-locked-file.txt"
    target.write_text("payload\n", encoding="utf-8")

    def always_fails(path: str) -> None:
        raise PermissionError(f"still cannot remove {path}")

    monkeypatch.setattr(os, "unlink", always_fails)

    with pytest.raises(PermissionError, match="still cannot remove"):
        tasks._review_workspace_rmtree_onexc(os.unlink, str(target), PermissionError())


@pytest.mark.parametrize("non_removal_callback", [os.open, os.close, os.scandir])
def test_review_workspace_rmtree_onexc_preserves_original_exception_for_non_removal_callback(
    non_removal_callback: object,
) -> None:
    """shutil.rmtree can invoke onexc with function=os.open/os.close/
    os.scandir while walking with fd-based APIs -- none accept a bare path,
    so calling ``function(path)`` for them would raise a spurious TypeError
    that hides the real failure. The handler must recognize this is not a
    retryable removal call, never invoke the callback, and re-raise the
    original exception object unchanged."""
    original_error = PermissionError("original failure reading the directory")

    with pytest.raises(PermissionError) as excinfo:
        tasks._review_workspace_rmtree_onexc(
            non_removal_callback, "irrelevant-path", original_error
        )

    assert excinfo.value is original_error


def test_review_workspace_removal_passes_onexc_handler_to_shutil_rmtree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_remove_review_workspace_tree (used by create_review_workspace's
    failure cleanup, cleanup_review_workspace's normal removal, and
    run_review_targets_isolated's inner isolation teardown) must route
    through shutil.rmtree's onexc= handler so the read-only-file retry logic
    actually applies to every workspace removal, not just to a hand-picked
    call site."""
    captured: dict[str, object] = {}
    real_rmtree = tasks.shutil.rmtree

    def spy_rmtree(path: Path, **kwargs: object) -> None:
        captured.update(kwargs)
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(tasks.shutil, "rmtree", spy_rmtree)
    target = tmp_path / "parent-to-remove"
    target.mkdir()
    (target / "file.txt").write_text("data\n", encoding="utf-8")

    tasks._remove_review_workspace_tree(target)

    assert captured.get("onexc") is tasks._review_workspace_rmtree_onexc
    assert not target.exists()


def test_review_workspace_cleanup_succeeds_after_a_simulated_failed_full_check(
    repository: Path, confined_mkdtemp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The task-runner contract can only prove that cleanup does not depend
    on check outcome: it must remove a proven workspace regardless of what a
    (simulated) failed full check left behind inside it."""
    monkeypatch.setattr(tasks, "command_nul_output", lambda *_a, **_k: [])
    monkeypatch.setattr(tasks, "command_output", lambda *_a, **_k: "")
    monkeypatch.setattr(
        tasks,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0),
    )

    workspace = tasks.create_review_workspace()
    workspace_path = Path(workspace["workspace_path"])
    (workspace_path / "qa-failure-artifact.log").write_text(
        "simulated failed check output\n", encoding="utf-8"
    )

    removed = tasks.cleanup_review_workspace(workspace_path, workspace["token"])

    assert removed == str(workspace_path)
    assert not workspace_path.exists()


def test_review_workspace_cli_dispatches_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_create() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(tasks, "target_review_workspace_create", fake_create)

    assert tasks.main(["review-workspace", "create"]) == 0
    assert calls == 1


def test_review_workspace_cli_dispatches_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_path = tmp_path / "workspace"
    calls: list[tuple[Path, str]] = []

    def fake_cleanup(path: Path, token: str) -> None:
        calls.append((path, token))

    monkeypatch.setattr(tasks, "target_review_workspace_cleanup", fake_cleanup)

    assert (
        tasks.main(
            [
                "review-workspace",
                "cleanup",
                "--workspace-path",
                str(workspace_path),
                "--token",
                "deadbeef",
            ]
        )
        == 0
    )
    assert calls == [(workspace_path, "deadbeef")]


def frontmatter_and_body(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw_frontmatter, body = text.split("---", 2)
    frontmatter = {}
    for line in raw_frontmatter.strip().splitlines():
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body


def test_review_repo_agent_contract() -> None:
    frontmatter, body = frontmatter_and_body(
        REPO_ROOT / ".github" / "agents" / "review-repo.agent.md"
    )

    assert frontmatter["name"] == "review-repo"
    assert "標準はfast（taskのみ）" in frontmatter["description"]
    assert "review-repo-fast" in body
    assert "review-repo-full" in body
    assert "scripts/tasks.py" in body
    assert "task target" in body
    assert "## 実行インターフェース" in body
    assert "## 検査の包含関係" in body
    assert (
        'review-repo-fast --inventory-json "<temp>/inventory.json" '
        '--results-json "<temp>/review-fast.json"' in body
    )
    assert (
        'review-repo-full --inventory-json "<temp>/inventory.json" '
        '--results-json "<temp>/review-full.json"' in body
    )
    assert "review-fingerprint capture --output" in body
    assert "review-fingerprint compare --before" in body
    assert "inventory-repo --format json" in body
    assert (
        '`review-workspace create` | `uv run --no-project "${PWD}/scripts/tasks.py" '
        "review-workspace create`" in body
    )
    assert (
        '`review-repo-full` | `uv run --no-project "<workspace>/scripts/tasks.py" '
        'review-repo-full --inventory-json "<temp>/inventory.json" '
        '--results-json "<temp>/review-full.json"`' in body
    )
    assert (
        '`review-workspace cleanup` | `uv run --no-project "${PWD}/scripts/tasks.py" '
        'review-workspace cleanup --workspace-path "<workspace>" --token "<token>"`'
        in body
    )
    assert "検査の成否にかかわらず実行する" in body
    assert "（finally相当）" in body
    assert "`review-repo-fast`の全検査" in body
    assert "Bicep parameter JSON" in body
    # Fast is the offline layer. It must not claim any external lookup, and the
    # scheduled mechanisms must stay named as the owners of update detection.
    fast_row = body.split("| `review-repo-fast` task target |", 1)[1].split(
        "| review-repo agentのfastモード |", 1
    )[0]
    assert "check-version-pins" in fast_row
    for external in ("latest release", "RE2", "公開活動"):
        assert external not in fast_row
    assert "隔離copyでしか実行できない検査だけを追加する" in body
    assert "fastが判定済みのversion契約も再実行しない" in body
    assert "`review-repo-fast`はオフラインで完結し、外部APIもDockerも使わない" in body
    assert (
        "最新版候補の検出はscheduledな仕組みへ委譲済みであり、fastでは実行しない"
        in body
    )
    assert "全Kubernetes YAML" in body
    assert "Chaos Mesh chart" in body
    assert "`kubernetes-schema-exclusion`座標で`excluded`" in body
    assert "## fullモードの文書とAI運用資産の評価基準" in body
    assert "文書とAI運用資産の意味評価および専門skillは実行しない" in body
    assert "文書とAI運用資産の意味評価を実行する" in body
    for document_type in (
        "`.github/copilot-instructions.md`",
        "| agent |",
        "| skill |",
        "| ADR |",
        "| Feature Document |",
        "| `docs/workarounds.md` |",
        "| READMEと運用文書 |",
        "| workflow sourceと生成物 |",
    ):
        assert document_type in body
    assert "inventoryへの出現だけで`pass`にしない" in body
    assert "`disable-model-invocation: true`のdispatcherはnameを必須とせず" in body
    execution_steps = body.split("## 実行手順", 1)[1].split(
        "## 既存経路との責務境界",
        1,
    )[0]
    assert "`inventory-repo`や`check-repo-health`を重複実行しない" in execution_steps
    assert "repo_health.py" not in execution_steps
    assert "fullの場合だけ" in execution_steps
    assert "full専用検査を個別の`unverified`として列挙せず" in execution_steps
    assert "決定論的検査の結果を`status`と`reason_code`とともに列挙" in execution_steps
    assert "手順2が出力した検査結果JSON" in execution_steps
    assert "`repository-freshness-checker`" in execution_steps
    assert "`bicep-api-version-updater`のcheck-onlyモード" in execution_steps
    assert "手順2と同じinventory JSON" in execution_steps
    assert "`documentation-external-link`座標だけを全件処理" in execution_steps
    assert "scheduled workflowが担当するversion関連の意味評価は実行しない" in (
        execution_steps
    )
    assert "意味評価はfull" not in body
    assert "Bicep resource APIの結果が返らない場合" in execution_steps
    assert "その領域を`unverified`とする" in execution_steps
    assert "pass" in body
    assert "fail" in body
    assert "unverified" in body
    assert "excluded" in body
    assert "coverage" in body
    for category in (
        "covered_by_other_check",
        "intentionally_excluded",
        "true_gap",
    ):
        assert category in body
    assert "今回の走査範囲では" in body
    assert "追跡ファイルを編集しない" in body
    assert "git status --short" not in body
    assert "tracked/index" in body
    assert "未追跡ファイル一覧" in body
    assert "SHA-256" in body
    assert "内容を表示しない" in body
    assert "Azure subscription" in body
    assert "AKS cluster" in body
    assert "Fleet" in body
    assert "aks-updates-analyzer" in body
    assert "bicep-api-version-updater" in body
    # Bicep CLI freshness moved to Renovate, so no dedicated workflow remains.
    assert "bicep-version-check.yml" not in body
    assert not (
        REPO_ROOT / ".github" / "workflows" / "bicep-version-check.yml"
    ).exists()
    for product_command in ("az feature show", "az provider show", "gh api", "curl"):
        assert product_command not in body


def test_repository_freshness_skill_contract() -> None:
    skill_path = (
        REPO_ROOT / ".github" / "skills" / "repository-freshness-checker" / "SKILL.md"
    )
    frontmatter, body = frontmatter_and_body(skill_path)

    assert frontmatter["name"] == "repository-freshness-checker"
    assert "review-repo full" in frontmatter["description"]
    assert "check-only" in body
    assert "repo health inventory JSON" in body
    assert (
        "review-repo-full --inventory-json <absolute-path> "
        "--results-json <absolute-path>" in body
    )
    assert "inventory-repo --format json" in body
    assert "別のinventory生成コマンドを実行せず" in body
    assert "同じ検出・検証をこのスキルがやり直さない" in body
    for subject in (
        "gh-aw",
        "Lefthook",
        "actionlint",
        "kubeconform",
        "azd",
        "Chaos Mesh Helm chart",
        "Docker base image",
        "Azure Functions extension bundle",
    ):
        assert subject in body
    for boundary in (
        "check-version-pins",
        "check-renovate-config",
        "freshness-checks",
    ):
        assert boundary in body
    assert "bicep-version-check.yml" not in body
    for status in ("pass", "fail", "unverified", "excluded"):
        assert status in body
    assert "Microsoft Learn MCP" in body
    assert "mslearn" in body
    assert "`documentation-external-link`" in body
    assert "`404`と`410`は`fail`" in body
    assert "ファイルの編集、自動更新" in body
    assert "Azure subscription" in body


def test_bicep_api_version_skill_exposes_non_editing_check_only_mode() -> None:
    _, body = frontmatter_and_body(
        REPO_ROOT / ".github" / "skills" / "bicep-api-version-updater" / "SKILL.md"
    )
    check_only = body.split("## check-onlyモード", 1)[1].split("## updateモード", 1)[0]

    assert "Azure認証を行わない" in check_only
    assert "`bicep-api-version-check` workflow" in check_only
    assert "subscriptionを照会しない" in check_only
    assert "Microsoft Learnの公開APIリファレンス" in check_only
    assert "Azure/azure-rest-api-specs" in check_only
    assert "どちらからも公開情報を取得できない対象は`unverified`" in check_only
    assert "az provider show" not in check_only
    assert "`edit`" not in check_only
    assert "ファイルを更新" not in check_only
    assert "## updateモード" in body
    assert "ユーザーの明示承認" in body
    assert "az provider show" in body.split("## updateモード", 1)[1]


def test_each_skill_directory_contains_skill_document() -> None:
    skills_directory = REPO_ROOT / ".github" / "skills"
    missing = sorted(
        path.name
        for path in skills_directory.iterdir()
        if path.is_dir() and not (path / "SKILL.md").is_file()
    )

    assert missing == []


ONLINE_TARGET_NAMES = (
    "freshness-checks",
    "check-renovate-config",
    "check-renovate-activity",
    "update-lefthook-pin",
)


def test_fast_review_runs_no_online_target() -> None:
    """Fast is offline by contract, so it may not call an external evaluator."""
    fast_targets = {check.target_name for check in tasks.FAST_REVIEW_CHECKS}

    assert fast_targets.isdisjoint(ONLINE_TARGET_NAMES)
    assert fast_targets == {
        "check-repo-health",
        "check-uv-version",
        "check-public-lock",
        "check-publisher-requirements",
        "check-version-pins",
    }
    assert all(
        "docker" not in check.required_tools for check in tasks.FAST_REVIEW_CHECKS
    )


def test_check_version_pins_touches_no_network_or_container_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline invariant check must reach its verdict from files alone."""

    def forbidden(*_args: object, **_kwargs: object) -> object:
        pytest.fail("check-version-pins must not perform an external lookup")

    monkeypatch.setattr(tasks.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(tasks, "open_github_url", forbidden)
    monkeypatch.setattr(tasks.subprocess, "run", forbidden)
    monkeypatch.setattr(tasks.shutil, "which", forbidden)

    tasks.target_check_version_pins()


def _version_pin_repository(tmp_path: Path) -> Path:
    """Copy every coordinate check-version-pins reads into a scratch tree."""
    repository = tmp_path / "repository"
    for relative in (
        ".github/renovate.json",
        ".github/workflows/ci.yml",
        "azure.yaml",
        "src/external-sli-publisher/host.json",
        "scripts/tasks.py",
        "pyproject.toml",
    ):
        source = REPO_ROOT / relative
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    return repository


def _run_version_pins(
    monkeypatch: pytest.MonkeyPatch, repository: Path
) -> int | str | None:
    monkeypatch.setattr(tasks, "ROOT", repository)
    with pytest.raises(SystemExit) as error:
        tasks.target_check_version_pins()
    return error.value.code


def test_check_version_pins_rejects_a_returning_dependabot_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _version_pin_repository(tmp_path)
    (repository / ".github" / "dependabot.yml").write_text(
        "version: 2\nupdates: []\n", encoding="utf-8"
    )

    assert _run_version_pins(monkeypatch, repository) == 1
    assert "Dependabot version updates must stay disabled" in capsys.readouterr().err


def test_check_version_pins_rejects_renovate_automerge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _version_pin_repository(tmp_path)
    config_path = repository / ".github" / "renovate.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["automerge"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")

    assert _run_version_pins(monkeypatch, repository) == 1
    assert "must not enable automerge" in capsys.readouterr().err


def test_check_version_pins_rejects_an_ambiguous_lefthook_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _version_pin_repository(tmp_path)
    workflow_path = repository / ".github" / "workflows" / "ci.yml"
    text = workflow_path.read_text(encoding="utf-8", newline="")
    workflow_path.write_text(
        text.replace("LEFTHOOK_SHA256=", "LEFTHOOK_SHA25=", 1),
        encoding="utf-8",
        newline="",
    )

    assert _run_version_pins(monkeypatch, repository) == 1
    assert "LEFTHOOK_SHA256=" in capsys.readouterr().err


def test_check_version_pins_rejects_an_exact_pin_where_a_range_belongs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """azd and the Functions bundle are ranges, never latest-comparable pins."""
    repository = _version_pin_repository(tmp_path)
    azure_yaml = repository / "azure.yaml"
    text = azure_yaml.read_text(encoding="utf-8", newline="")
    azure_yaml.write_text(
        tasks.re.sub(r"azd: *['\"]?>= *", "azd: ", text),
        encoding="utf-8",
        newline="",
    )

    assert _run_version_pins(monkeypatch, repository) == 1
    assert "requiredVersions.azd range" in capsys.readouterr().err


def test_check_version_pins_rejects_a_malformed_functions_bundle_range(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _version_pin_repository(tmp_path)
    host_json = repository / "src" / "external-sli-publisher" / "host.json"
    document = json.loads(host_json.read_text(encoding="utf-8"))
    document["extensionBundle"]["version"] = "4.0.0"
    host_json.write_text(json.dumps(document), encoding="utf-8")

    assert _run_version_pins(monkeypatch, repository) == 1
    assert "support range" in capsys.readouterr().err


def test_version_ranges_are_never_compared_against_a_latest_release() -> None:
    """No evaluator may treat azd or the Functions bundle as an exact pin."""
    assert tasks.azd_minimum_version_range().startswith(">= ")
    assert tasks.functions_bundle_support_range().startswith("[")
    assert "azd" not in tasks.FRESHNESS_CHECK_SUBJECTS
    assert "Azure Functions extension bundle" not in tasks.FRESHNESS_CHECK_SUBJECTS
    assert "scheduled freshness workflow" in (
        tasks.azd_minimum_version_range.__doc__ or ""
    )
    renovate_targets = {
        item.target_path for item in tasks.RENOVATE_MANAGER_EXPECTATIONS
    }
    assert "azure.yaml" in renovate_targets
    assert "src/external-sli-publisher/host.json" not in renovate_targets


def test_scheduled_checker_reports_exactly_the_non_renovate_subjects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The scheduled JSON carries gh-aw, Lefthook, and Renovate app activity."""
    monkeypatch.setattr(
        tasks,
        "evaluate_gh_aw_pin",
        lambda: tasks.FreshnessFinding(
            tasks.FRESHNESS_SUBJECT_GH_AW, "c", "pass", "current", None, None, (), "d"
        ),
    )
    monkeypatch.setattr(
        tasks,
        "evaluate_lefthook_pin",
        lambda: tasks.FreshnessFinding(
            tasks.FRESHNESS_SUBJECT_LEFTHOOK,
            "c",
            "unverified",
            tasks.FRESHNESS_REASON_UPDATE_AVAILABLE,
            "2.1.10",
            "2.1.12",
            (),
            "d",
        ),
    )
    monkeypatch.setattr(
        tasks,
        "evaluate_renovate_activity",
        lambda: tasks.FreshnessFinding(
            tasks.FRESHNESS_SUBJECT_RENOVATE_ACTIVITY,
            "c",
            "pass",
            tasks.FRESHNESS_REASON_RENOVATE_ACTIVITY_OBSERVED,
            None,
            None,
            (),
            "d",
        ),
    )
    output = tmp_path / "freshness.json"

    tasks.target_freshness_checks(output)

    document = json.loads(output.read_text(encoding="utf-8"))
    assert [finding["subject"] for finding in document["findings"]] == [
        "gh-aw",
        "Lefthook",
        "Renovate app activity",
    ]
    assert document["status"] == "unverified"
