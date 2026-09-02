from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
FRESHNESS_SUBJECTS = (
    "gh-aw",
    "Lefthook",
    "actionlint",
    "kubeconform",
    "azd",
    "Chaos Mesh Helm chart",
    "Docker base imageのEOLとdigest固定状況",
    "Azure Functions extension bundleのsupport範囲",
)


def renovate_config() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((REPO_ROOT / ".github" / "renovate.json").read_text("utf-8")),
    )


def test_dependabot_version_updates_are_disabled() -> None:
    """Renovate owns scheduled version updates, so no Dependabot config exists.

    GitHub's Dependabot alerts and security updates are repository settings and
    are unaffected by removing this file.
    """
    assert not (REPO_ROOT / ".github" / "dependabot.yml").exists()

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "tasks.py"),
            "check-version-pins",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Dependabot version updates: disabled" in completed.stdout


def test_renovate_covers_the_scheduled_update_targets() -> None:
    config = renovate_config()

    assert config["enabledManagers"] == [
        "pep621",
        "github-actions",
        "dockerfile",
        "custom.regex",
    ]
    assert config["automerge"] is False
    assert config["dependencyDashboard"] is True
    assert config["dependencyDashboardApproval"] is False
    assert config["ignorePaths"] == [".github/workflows/*.lock.yml", ".github/aw/**"]
    pep621_rule = next(
        rule
        for rule in config["packageRules"]
        if rule["description"] == "python-workspace-candidate-detection-only"
    )
    assert pep621_rule["dependencyDashboardApproval"] is True
    assert pep621_rule["skipArtifactsUpdate"] is True
    descriptions = {manager["description"] for manager in config["customManagers"]}
    assert descriptions == {
        "chaos-mesh-chart-version",
        "actionlint-docker-image",
        "kubeconform-docker-image",
        "renovate-validator-image",
        "bicep-cli-version",
        "uv-required-version",
    }


def test_scheduled_and_review_detection_do_not_overlap() -> None:
    """Only what Renovate cannot reach is left to the scheduled checker.

    The freshness workflow must not re-detect a Renovate-owned coordinate, and
    the offline review layer must not detect update candidates at all.
    """
    config = renovate_config()
    renovate_owned = {
        manager["depNameTemplate"] for manager in config["customManagers"]
    }
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "repository-freshness-check.md"
    ).read_text(encoding="utf-8")

    assert renovate_owned.isdisjoint({"evilmartians/lefthook", "github/gh-aw"})
    assert "Renovateが構造上扱えない3対象だけ" in workflow or (
        "Renovateが検出できない3対象だけ" in workflow
    )
    assert "同じ最新版検出を繰り返さず" in workflow


def test_ci_runs_repository_health_check() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'scripts/tasks.py" check-repo-health' in ci


def test_ci_reuses_workflow_lint_target() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'scripts/tasks.py" lint-workflows' in ci
    assert "docker run --rm -v" not in ci


def test_ci_reuses_kubernetes_and_helm_validation_targets() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'scripts/tasks.py" lint-k8s' in ci
    assert 'scripts/tasks.py" validate-helm-values' in ci
    assert "azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310" in ci
    assert "k8s/apps/chaos-app/*.yaml" not in ci


def test_freshness_workflow_contract() -> None:
    source = (
        REPO_ROOT / ".github" / "workflows" / "repository-freshness-check.md"
    ).read_text(encoding="utf-8")

    assert "schedule: weekly" in source
    assert "workflow_dispatch:" in source
    assert "permissions:\n  contents: read\n  copilot-requests: write" in source
    assert "safe-outputs:\n  create-issue:" in source
    assert "  noop: false" in source
    assert "close-older-issues: true" in source
    assert "max: 1" in source
    assert "repository-freshness-checker/SKILL.md" in source
    assert 'scripts/tasks.py" inventory-repo --format json' in source
    assert 'scripts/tasks.py" freshness-checks' in source
    assert "reason_code" in source
    assert "update-available" in source
    assert "evidence-unavailable" in source
    assert "scripts/repo_health.py" not in source

    for subject in FRESHNESS_SUBJECTS:
        assert subject in source
    assert f"{len(FRESHNESS_SUBJECTS)}つ" in source
    for boundary in (
        "Renovate（`.github/renovate.json`）",
        "latestと比較して更新する対象ではありません",
    ):
        assert boundary in source
    # Bicep CLI updates moved to Renovate; unrelated scheduled workflows remain.
    assert "bicep-version-check.yml" not in source

    for forbidden in (
        "azure/login",
        "az login",
        "create-pull-request:",
        "push-to-pr-branch:",
        "contents: write",
    ):
        assert forbidden not in source


def test_aks_updates_workflow_disables_implicit_noop_issues() -> None:
    source = (
        REPO_ROOT / ".github" / "workflows" / "aks-updates-analyzer.md"
    ).read_text(encoding="utf-8")

    assert "safe-outputs:\n  create-issue:" in source
    assert "  noop: false" in source
    assert "expires:" not in source


def test_implicit_gh_aw_maintenance_workflow_is_not_committed() -> None:
    maintenance = REPO_ROOT / ".github" / "workflows" / "agentics-maintenance.yml"
    actions_lock = json.loads(
        (REPO_ROOT / ".github" / "aw" / "actions-lock.json").read_text(encoding="utf-8")
    )

    assert not maintenance.exists()
    assert "github/gh-aw-actions/setup-cli@v0.79.6" not in actions_lock["entries"]


def test_freshness_targets_exist_in_repository_inventory() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "repo_health.py"),
            "inventory",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    inventory = json.loads(completed.stdout)["inventory"]
    tool_versions = {
        item["location"].rpartition(":")[2]
        for item in inventory
        if item["category"] == "tool-version"
    }
    helm_charts = {
        item["value"] for item in inventory if item["category"] == "helm-chart"
    }
    gh_aw_versions = {
        item["value"]
        for item in inventory
        if item["location"].endswith(("gh-aw-setup", "compiler-version"))
    }
    docker_images = {
        item["value"] for item in inventory if item["category"] == "docker-base-image"
    }
    extension_bundles = {
        item["value"]
        for item in inventory
        if item["category"] == "function-extension-bundle"
    }
    external_links = {
        item["value"]
        for item in inventory
        if item["category"] == "documentation-external-link"
    }

    assert {"lefthook", "actionlint", "kubeconform", "azd"} <= tool_versions
    assert "chaos-mesh/chaos-mesh" in helm_charts
    assert gh_aw_versions
    assert any(image.startswith("python:3.14-slim@sha256:") for image in docker_images)
    assert any(
        image.startswith("ghcr.io/astral-sh/uv:0.12.2@sha256:")
        for image in docker_images
    )
    assert "[4.*, 5.0.0)" in extension_bundles
    assert "https://learn.microsoft.com/azure/chaos-studio/" in external_links


def test_freshness_scope_is_identical_across_declarations() -> None:
    paths = (
        REPO_ROOT / ".github/workflows/repository-freshness-check.md",
        REPO_ROOT / ".github/skills/repository-freshness-checker/SKILL.md",
        REPO_ROOT / "docs/dependency-management.md",
    )

    for path in paths:
        content = path.read_text(encoding="utf-8")
        for subject in FRESHNESS_SUBJECTS:
            assert subject in content, f"{subject!r} is missing from {path}"


def test_freshness_workflow_is_compiled() -> None:
    lock = (
        REPO_ROOT / ".github" / "workflows" / "repository-freshness-check.lock.yml"
    ).read_text(encoding="utf-8")

    assert "automatically generated by gh-aw" in lock
    assert "schedule:" in lock
    assert "workflow_dispatch:" in lock
    assert "issues: write" in lock
    assert "pull-requests: write" not in lock
    assert "id-token: write" not in lock


def test_bicep_api_version_workflow_contract() -> None:
    source = (
        REPO_ROOT / ".github" / "workflows" / "bicep-api-version-check.md"
    ).read_text(encoding="utf-8")

    assert "schedule: weekly" in source
    assert "workflow_dispatch:" in source
    assert "bicep-api-version-updater/SKILL.md" in source
    assert 'scripts/tasks.py" inventory-repo --format json' in source
    assert "`bicep-resource-api`座標だけ" in source
    assert "learn.microsoft.com" in source
    assert "Azure/azure-rest-api-specs" in source
    assert "Microsoft.ContainerService/{aks|fleet}/{stable|preview}" in source
    assert "どちらからも公開情報を取得できない座標" in source
    assert "各HTTP requestを30秒以内" in source
    assert "  noop: false" in source
    assert "close-older-issues: true" in source
    for forbidden in (
        "azure/login",
        "az login",
        "az provider show",
        "create-pull-request:",
        "push-to-pr-branch:",
        "contents: write",
    ):
        assert forbidden not in source


def test_bicep_api_version_workflow_is_compiled() -> None:
    lock = (
        REPO_ROOT / ".github" / "workflows" / "bicep-api-version-check.lock.yml"
    ).read_text(encoding="utf-8")

    assert "automatically generated by gh-aw" in lock
    assert "workflow_dispatch:" in lock
    assert "issues: write" in lock
    assert "pull-requests: write" not in lock
    assert "id-token: write" not in lock


def test_documentation_exposes_maintenance_entry_points() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    instructions = (REPO_ROOT / ".github" / "copilot-instructions.md").read_text(
        encoding="utf-8"
    )
    deployment = (REPO_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    dependencies = (REPO_ROOT / "docs" / "dependency-management.md").read_text(
        encoding="utf-8"
    )

    assert "review-repo-fast" in readme
    assert "review-repo-full" in readme
    assert "fastモードは" in readme
    assert "意味評価や専門skillは実行しません" in readme
    assert "fullモードは" in readme
    assert "文書とAI運用資産の意味評価を実行します" in readme
    assert "docs/dependency-management.md" in readme
    assert "唯一の上位実行入口" in readme
    assert "構造化inventory" in readme
    assert "オフラインで完結する検査だけを実行します" in readme
    # README stays a short entry point: the responsibility split lives in the
    # dependency-management document.
    assert (
        len(readme.split("## リポジトリ保守")[1].split("##")[0].strip().splitlines())
        <= 3
    )

    assert ".github/renovate.json" in dependencies
    assert "repository-freshness-check.md" in dependencies
    assert "refresh-uv-lock.yml" in dependencies
    assert "`.github/dependabot.yml`は存在せず" in dependencies
    assert "## fastとfullの境界" in dependencies
    assert "`review-repo-fast`はオフラインで完結する" in dependencies
    assert "version候補、EOL、support範囲、互換性はscheduled workflowが担当" in (
        dependencies
    )
    assert "fullは再評価しない" in dependencies
    assert "--results-json" in dependencies

    assert "標準のfastはtaskによる非編集検査だけを実行する" in instructions
    assert "公開MarkdownリンクとBicep APIのcheck-only確認" in instructions
    assert "version候補、EOL、support範囲、互換性はscheduled workflowが担当" in (
        instructions
    )
    assert "文書とAI運用資産の意味評価を実行する" in instructions

    assert "Renovateはworkspaceの依存について更新候補の検出だけ" in deployment
    assert 'resolution-strategy = "lowest"' in deployment
    assert "workspace member" in deployment
    assert "public PyPI" in deployment
    assert "check-public-lock" in deployment
    assert "check-publisher-requirements" in deployment
    assert "check-version-pins" in deployment
    assert "check-renovate-config" in deployment
    assert "refresh-uv-lock.yml" in deployment
