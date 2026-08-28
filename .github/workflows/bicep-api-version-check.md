---
on:
  schedule: weekly on friday around 9:00 utc+9
  workflow_dispatch:
description: "Weekly Bicep resource API version check using public Microsoft Learn and Azure REST API specifications without Azure authentication or repository changes."
labels: [repository-health, automation, bicep]
permissions:
  contents: read
  copilot-requests: write
engine:
  id: copilot
  model: claude-opus-4.8
network:
  allowed:
    - defaults
    - github
    - "learn.microsoft.com"
tools:
  bash: ["uv", "python3", "git"]
safe-outputs:
  create-issue:
    title-prefix: "[Bicep API Versions] "
    labels: [repository-health, automation, bicep]
    close-older-issues: true
    max: 1
  noop: false
timeout-minutes: 15
---

# Bicep resource API versionの週次確認

次のスキルのcheck-only契約、判断規則、出力schemaに従ってください。updateモードへ進んではなりません。

{{#runtime-import .github/skills/bicep-api-version-updater/SKILL.md}}

## 実行範囲

1. `uv run --no-project "${GITHUB_WORKSPACE}/scripts/tasks.py" inventory-repo --format json`を一度だけ実行し、標準出力を`"${RUNNER_TEMP}/bicep-api-inventory.json"`へ保存してください。
2. inventoryの`bicep-resource-api`座標だけを処理してください。同じresource typeと現在versionの組は一度だけ公開情報を取得し、結果を対応する全座標へ適用してください。
3. 最初に`https://learn.microsoft.com/en-us/azure/templates/{provider}/{resourceType}`形式のMicrosoft Learn API referenceと、Microsoft Learn内のbreaking changes情報を確認してください。現在versionがMicrosoft Learnにまだ掲載されていない場合は、Azure公式`Azure/azure-rest-api-specs` repositoryの対応するstableまたはpreview仕様を確認してください。AKSとFleetでは`specification/containerservice/resource-manager/Microsoft.ContainerService/{aks|fleet}/{stable|preview}`を使用します。取得にはPython標準ライブラリを使い、各HTTP requestを30秒以内に制限してください。
4. Azure認証を行わず、`az`、subscription、resource provider、AKS cluster、Fleetを照会しないでください。
5. ファイルの編集、commit、push、pull requestの作成を行わないでください。
6. workflow全体の残り時間で確認できない座標、Microsoft Learnと`Azure/azure-rest-api-specs`のどちらからも公開情報を取得できない座標、安定版を一意に判定できない座標は`unverified`としてください。

## Issue

日本語でIssueを1件だけ作成してください。更新候補がない場合も実行結果を記録します。

- 確認日時、対象commit、inventory schema version
- inventory座標数、重複排除後のresource typeとversionの組数、確認済み座標数、未検証座標数、除外座標数
- resource typeごとの現在version、公開されている最新GA版、プレビューからGAへ移行できるか、状態、根拠URL
- breaking changesと、更新前に確認する属性または互換性
- 実行環境の制約

公開情報を取得できなかった対象を問題なしと推定しないでください。更新はIssueで通知するだけとし、このworkflowでは適用しません。
