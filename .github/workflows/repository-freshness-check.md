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
2. `uv run --no-project "${GITHUB_WORKSPACE}/scripts/tasks.py" freshness-checks`で、scheduled checkerの結果を機械可読JSONとして取得してください。このcheckerは、Renovateが検出できない3対象だけを扱います。gh-awのpinと公式latest releaseの比較、Lefthookのpin versionと公式latest releaseの比較およびpin versionの公式checksum照合、Renovate appの公開活動の観測です。このコマンドはfindingが`fail`や`unverified`でも終了コード0でJSONを出力するため、結果の解釈だけを行ってください。各findingの`status`、`reason_code`、`current`、`published`、`evidence`、全体の`status`と`coverage`をそのまま採用し、標準出力の自然言語解釈だけに依存しないでください。
3. 今回の対象は、gh-aw、Lefthook、actionlint、kubeconform、azd、Chaos Mesh Helm chart、Docker base imageのEOLとdigest固定状況、Azure Functions extension bundleのsupport範囲の8つと、Renovate appの稼働確認です。
4. actionlint、kubeconform、Chaos Mesh Helm chart、GitHub Actions、Docker image tag、Python依存、uv、Bicep CLIの更新候補はRenovate（`.github/renovate.json`）が検出します。このworkflowは同じ最新版検出を繰り返さず、Renovateの担当範囲についてはEOLとsupport範囲と互換性の意味評価だけを行ってください。Renovate appが稼働しているかどうかは`Renovate app activity` findingで判断してください。このfindingは、Dependency Dashboard issueの`updated_at`と、Renovate app authoredのPull Request（open/closedを含む）の`created_at`/`updated_at`のうち最新のものを、14日の公開活動の観測窓と比べた結果です。Renovateは定期的なpingを公開しないため、この窓はheartbeatの間隔ではありません。`renovate-not-observed`、`renovate-activity-unobserved`、`evidence-unavailable`のいずれかのときはRenovate担当対象を`pass`とせず`unverified`として、公開活動の未観測を理由に記録してください。`renovate-activity-unobserved`はapp停止の証拠ではないため、停止と断定せず、最近の公開活動から現在の稼働を確認できないと記述してください。Dashboard issueはRenovate app自身が作るものであり、このworkflowでは作成しないでください。
5. azdのminimum version rangeとAzure Functions extension bundleのsupport範囲は、latestと比較して更新する対象ではありません。固定の公式情報源で、azdのrangeが現行schemaの`requiredVersions`仕様に従っているか、Functions bundleのrangeがサポート対象かを意味評価してください。Docker base imageのEOLもこのworkflowで確認します。
6. Azure認証を行わず、subscription、AKS cluster、Fleetなどの実環境を照会しないでください。
7. ファイルの編集、commit、push、pull requestの作成を行わないでください。

各対象について現在値、公開済みの安定版、状態、根拠URL、更新前の確認事項を整理してください。`freshness-checks`が`unverified`（`reason_code: update-available`）とした対象は更新候補が確定していますが互換性レビュー待ちです。`reason_code: evidence-unavailable`は公開情報を取得できなかった対象であり、問題なしと推定しないでください。

## Issue

日本語でIssueを1件だけ作成してください。Issueには次の情報を含めます。

- 確認日時と対象commit
- 8対象それぞれの現在値、公開値、`pass`、`fail`、`unverified`、`excluded`のいずれかの状態
- Renovate appの稼働状態（Dependency Dashboardの有無と最終更新日時、`pass`または`unverified`の理由）
- inventory座標数、確認済み座標数、未検証座標数、除外座標数
- 公式情報のURLと、更新前に確認する互換性または移行条件
- 実行環境の制約

更新が不要な場合も、今回の確認結果を記録するIssueを作成してください。
