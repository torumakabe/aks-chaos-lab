from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

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


def dependabot_update_blocks() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    config = yaml.safe_load(
        (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    return config, {update["package-ecosystem"]: update for update in config["updates"]}


def test_dependabot_contract() -> None:
    config, blocks = dependabot_update_blocks()

    assert config["version"] == 2
    assert set(blocks) == {"github-actions", "docker"}

    for ecosystem, limit in (("github-actions", 5), ("docker", 3)):
        block = blocks[ecosystem]
        assert block["schedule"]["interval"] == "weekly"
        assert block["open-pull-requests-limit"] == limit
        group = next(iter(block["groups"].values()))
        assert group["patterns"] == ["*"]
        assert group["update-types"] == ["minor", "patch"]


def test_dependabot_excludes_generated_and_uv_managed_inputs() -> None:
    config_text = (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    _, blocks = dependabot_update_blocks()

    actions = blocks["github-actions"]
    assert actions["directory"] == "/"
    assert actions["exclude-paths"] == [".github/workflows/*.lock.yml"]
    action_ignores = {item["dependency-name"] for item in actions["ignore"]}
    assert "github/gh-aw-actions/*" in action_ignores
    assert "github/gh-aw-actions" in action_ignores
    assert (
        "gh-aw only manages the exact github/gh-aw-actions ignore entry" in config_text
    )
    assert "Managed by gh aw compile" in config_text

    docker = blocks["docker"]
    assert docker["directory"] == "/src/api"
    docker_ignores = {item["dependency-name"] for item in docker["ignore"]}
    assert docker_ignores == {"astral-sh/uv"}


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
    assert "scripts/repo_health.py" not in source

    for subject in FRESHNESS_SUBJECTS:
        assert subject in source
    assert f"{len(FRESHNESS_SUBJECTS)}つ" in source
    assert "Docker image tag更新はDependabot" in source
    for boundary in (
        "bicep-version-check.yml",
        "aks-updates-analyzer",
        "GitHub Actionsのversion更新",
        "Dependabot",
    ):
        assert boundary in source

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
        REPO_ROOT / "README.md",
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
    assert "schedule:" in lock
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

    assert "review-repo-fast" in readme
    assert "review-repo-full" in readme
    assert "fastモードは" in readme
    assert "意味評価や専門skillは実行しません" in readme
    assert "fullモードは" in readme
    assert "文書とAI運用資産の意味評価を実行します" in readme
    assert ".github/dependabot.yml" in readme
    assert "bicep-version-check.yml" in readme
    assert "bicep-api-version-check.md" in readme
    assert "aks-updates-analyzer.md" in readme
    assert "repository-freshness-check.md" in readme
    assert "唯一の上位実行入口" in readme
    assert "構造化inventory" in readme

    assert "標準のfastはtaskによる非編集検査だけを実行する" in instructions
    assert "全task、公開鮮度とBicep APIの確認" in instructions
    assert "文書とAI運用資産の意味評価を実行する" in instructions

    assert "Dependabotのuv ecosystemは現時点では有効にしません" in deployment
    assert 'resolution-strategy = "lowest"' in deployment
    assert "workspace member" in deployment
    assert "public PyPI" in deployment
    assert "check-public-lock" in deployment
    assert "check-publisher-requirements" in deployment
    assert "refresh-uv-lock.yml" in deployment
    assert "GitHub Actions ecosystem" in deployment
    assert "完全一致のignore entry" in deployment
