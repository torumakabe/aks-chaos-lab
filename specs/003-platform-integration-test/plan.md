# Implementation Plan: プラットフォーム統合テストパイプライン

**Branch**: `003-platform-integration-test` | **Date**: 2024-12-10 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-platform-integration-test/spec.md`

## Summary

GitHub Actionsワークフローを作成し、手動トリガー（workflow_dispatch）により一時的なAzure環境をプロビジョニングし、アプリケーションをデプロイし、統合テストを実行後にクリーンアップする。既存の`azure.yaml`とBicepテンプレートを使用し、OIDC認証でAzureに接続する。

## Technical Context

**Language/Version**: YAML (GitHub Actions) + Bash (テストスクリプト)
**Primary Dependencies**: Azure CLI, Azure Developer CLI (azd), Bicep CLI, curl
**Storage**: N/A（一時的なAzureリソース）
**Testing**: HTTPベースの統合テスト（curl + pytest）
**Target Platform**: GitHub Actions (ubuntu-latest)
**Project Type**: CI/CDパイプライン
**Performance Goals**: 全体実行60分以内、プロビジョニング+デプロイ35分以内
**Constraints**: 同時実行1、タイムアウト60分、クリーンアップ必須
**Scale/Scope**: 単一ワークフロー、複数ジョブ構成

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. コード品質基準（Python） | N/A | 本機能はYAML/Bashが主、Pythonコード変更なし |
| II. インフラストラクチャ品質基準（Bicep） | ✅ Pass | Bicep検証ステップをパイプラインに含める |
| III. ドキュメント管理 | ✅ Pass | `/docs/deployment.md`を更新予定 |
| IV. Spec Kit ワークフロー | ✅ Pass | 本ワークフローに従って実装 |
| V. テスト方針 | ✅ Pass | 統合テストスクリプトを`src/tests/integration/`に配置 |

## Project Structure

### Documentation (this feature)

\`\`\`text
specs/003-platform-integration-test/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output
\`\`\`

### Source Code (repository root)

\`\`\`text
.github/
└── workflows/
    └── integration-test.yml   # 新規作成: 統合テストパイプライン

src/
└── tests/
    └── integration/
        └── test_platform.py   # 新規作成: プラットフォーム統合テスト

docs/
└── deployment.md              # 更新: 統合テストパイプラインの説明を追加
\`\`\`

**Structure Decision**: 既存のプロジェクト構造に従い、ワークフローは`.github/workflows/`に、統合テストは`src/tests/integration/`に配置する。

## Workflow Design

### Jobs Overview

\`\`\`mermaid
graph TD
    A[workflow_dispatch] --> B[validate]
    B --> C[provision-and-deploy]
    C --> D[test]
    D --> E[cleanup]
    
    B -->|failure| E
    C -->|failure| E
    D -->|always| E
\`\`\`

> **Note**: provision と deploy は元々別ジョブだったが、azd env の環境変数共有を簡素化するため統合した。

### Job Details

| Job | Description | Timeout | Dependencies |
|-----|-------------|---------|--------------|
| validate | Bicepテンプレートの検証 | 15分 | - |
| provision-and-deploy | azd provision + deploy | 35分 | validate |
| test | 統合テスト実行 | 10分 | provision-and-deploy |
| cleanup | リソースグループ削除 | 15分 | always (成功・失敗問わず) |

> **Note**: provisionとdeployはazd envの環境変数共有の簡素化のため、単一ジョブに統合した。

### Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| branch | string | main | テスト対象ブランチ |
| test_scope | choice | full | full / infra-only / app-only |
| aks_sku | choice | Base | Base / Automatic |

## Logging & Debugging Strategy

**目的**: SC-005「5分以内に失敗原因特定」を実現するためのログ構造化方針

### 実装方針

1. **ロググループ化**: 各ステップで \`::group::\` / \`::endgroup::\` を使用してログを折りたたみ可能に
2. **ステップサマリー**: 失敗時に \`\$GITHUB_STEP_SUMMARY\` へエラー概要を出力
3. **azdデバッグモード**: 失敗時のリトライで \`--debug\` フラグを有効化
4. **クリーンアップログ**: リソース削除状況を明示的に出力

### ログ出力例

\`\`\`yaml
- name: Provision infrastructure
  run: |
    echo "::group::azd provision"
    azd provision --no-prompt || {
      echo "## ❌ Provision Failed" >> \$GITHUB_STEP_SUMMARY
      echo "Run ID: \${{ github.run_id }}" >> \$GITHUB_STEP_SUMMARY
      echo "Check the logs above for details." >> \$GITHUB_STEP_SUMMARY
      exit 1
    }
    echo "::endgroup::"
\`\`\`

## Complexity Tracking

> 本機能はConstitution違反なし

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | - | - |
