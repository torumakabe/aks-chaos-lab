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
network:
  allowed:
    - defaults
    - github
    - "api.github.com"
    - "learn.microsoft.com"
tools:
  bash: ["python3", "git"]
safe-outputs:
  report-failure-as-issue: false
  report-failed-jobs: false
  threat-detection:
    enabled: ${{ needs.agent.result == 'success' }}
  missing-tool:
    create-issue: false
  missing-data:
    create-issue: false
  report-incomplete:
    create-issue: false
  create-issue:
    title-prefix: "[Bicep API Versions] "
    labels: [repository-health, automation, bicep]
    close-older-issues: true
    max: 1
timeout-minutes: 25
---

# Bicep resource API versionの週次確認

次のスキルのcheck-only手順、判断規則、完了条件に従ってください。updateモードへ進んではなりません。

{{#runtime-import .github/skills/bicep-api-version-updater/SKILL.md}}

## 実行範囲

1. checkoutされたリポジトリ全体を対象としてスキルのcheck-onlyを実行してください。Gitで対象のBicepファイルを列挙し、ファイルを直接読んでください。事前生成された対象JSONはありません。
2. 公開情報の取得には、許可されたPython標準ライブラリと`learn.microsoft.com`、GitHub APIを使い、各HTTP requestを30秒以内に制限してください。Microsoft Learn API referenceのURLは`https://learn.microsoft.com/en-us/azure/templates/{provider}/{resourceType}`形式です。`Azure/azure-rest-api-specs`のAKSとFleetのREST仕様は`specification/containerservice/resource-manager/Microsoft.ContainerService/{aks|fleet}/{stable|preview}`を使用します。
3. 終了前に、対象ファイルと宣言ごとの処理状況を確認してください。時間制限で未処理の対象は`not-processed`、読取や宣言解釈の失敗は`target-unresolved`、公開情報の取得不能は`evidence-unavailable`、仕様判断の未完了は`assessment-incomplete`として区別してください。未読ファイルは宣言数不明として別に記録し、未完了のまま確認完了と報告してはなりません。
4. Azure認証を行わず、`az`、subscription、resource provider、AKS cluster、Fleetを照会しないでください。
5. ファイルの編集、commit、push、pull requestの作成を行わないでください。

## Issue

日本語でIssueを1件だけ作成してください。更新候補がない場合も実行結果を記録します。

- 確認日時、対象commitと作業変更、対象範囲、確認完了または未完了
- 対象ファイル数、resource宣言数、重複排除後のresource typeとversionの組数、状態別の宣言数、未読ファイルと未確定の対象範囲
- 各宣言のファイル、宣言名、行番号と判定。未検証の対象は上記の理由を区別する
- resource typeごとの現在version、公開されている最新GA版、プレビューからGAへ移行できるか、状態、根拠URL
- breaking changesと、更新前に確認する属性または互換性
- 実行環境の制約

公開情報を取得できなかった対象を問題なしと推定しないでください。更新はIssueで通知するだけとし、このworkflowでは適用しません。
