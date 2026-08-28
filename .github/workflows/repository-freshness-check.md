---
on:
  schedule: weekly on wednesday around 9:00 utc+9
  workflow_dispatch:
description: "Weekly repository tool freshness check that creates one issue without changing repository files."
labels: [repository-health, automation]
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
  bash: ["uv", "python3", "git", "gh"]
safe-outputs:
  create-issue:
    title-prefix: "[Repository Freshness] "
    labels: [repository-health, automation]
    close-older-issues: true
    max: 1
  noop: false
timeout-minutes: 15
---

# リポジトリ鮮度の週次確認

次のスキルを判断規則として読み込み、そのcheck-only契約と出力schemaに従ってください。

{{#runtime-import .github/skills/repository-freshness-checker/SKILL.md}}

## 実行範囲

1. `uv run --no-project "${GITHUB_WORKSPACE}/scripts/tasks.py" inventory-repo --format json`で、追跡済みファイルからinventory JSONを生成してください。
2. 今回の対象は、gh-aw、Lefthook、actionlint、kubeconform、azd、Chaos Mesh Helm chart、Docker base imageのEOLとdigest固定状況、Azure Functions extension bundleのsupport範囲の8つだけです。
3. Bicep CLIは`bicep-version-check.yml`、AKS更新情報は`aks-updates-analyzer`が担当します。GitHub Actionsのversion更新とDocker image tag更新はDependabotが担当します。Docker base imageのEOLとdigest固定状況はこのworkflowで確認します。Dependabotが扱う更新候補をIssueへ重複して含めないでください。
4. Azure認証を行わず、subscription、AKS cluster、Fleetなどの実環境を照会しないでください。
5. ファイルの編集、commit、push、pull requestの作成を行わないでください。

各対象について現在値、公開済みの安定版、状態、根拠URL、更新前の確認事項を整理してください。公開情報を取得できない対象は`unverified`とし、問題なしと推定しないでください。

## Issue

日本語でIssueを1件だけ作成してください。Issueには次の情報を含めます。

- 確認日時と対象commit
- 8対象それぞれの現在値、公開値、`pass`、`fail`、`unverified`、`excluded`のいずれかの状態
- inventory座標数、確認済み座標数、未検証座標数、除外座標数
- 公式情報のURLと、更新前に確認する互換性または移行条件
- 実行環境の制約

更新が不要な場合も、今回の確認結果を記録するIssueを作成してください。
