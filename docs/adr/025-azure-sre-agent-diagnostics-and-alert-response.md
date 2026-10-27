# ADR-025: Azure SRE Agent の診断とレビュー型アラート対応を opt-in で導入する

## Status

Accepted

- Date: 2026-10-19
- Updated: 2026-10-22

## Context

AKS Chaos Lab は Application Insights、Log Analytics、Managed Prometheus にテレメトリを保存する。Azure SRE Agent を導入する際は、対象データと Azure リソースへの権限を限定し、継続費用を明示的に選択できる必要がある。

Azure SRE Agent の公式資料は Azure Monitor と AKS の診断機能を説明している。一方、組み込み `QueryPrometheusMetrics` が Managed Prometheus workspace の endpoint をどの条件で探索するかは、このプロジェクトの構成について確認できていない。現在の検証結果だけでは、SRE Agent 全体または Managed Prometheus 全体のサポート可否を結論できない。

公式資料では、Azure Monitor metrics、Application Insights、Log Analytics、Resource Graph、AKS diagnostics は Agent の組み込み機能として示されている。Application Insights と Log Analytics の connector は、特定のデータソースを Agent の継続的なコンテキストに含めるために使う。

このため、本構成では Azure Monitor metrics 用の独立した `MonitorClient` connector resource を定義しない。公式 IaC template にある `AzureMonitor` connector は subscription scope の alert を読む設定であり、組み込み metrics query や Managed Prometheus endpoint 探索とは別の機能である（[connector docs](https://learn.microsoft.com/azure/sre-agent/connectors), [official IaC sample](https://github.com/microsoft/sre-agent/blob/ff317310cca5894fcd42ccc32f5452c3f408cc3c/sreagent-templates/bicep/agent-extensions.bicep)）。

Azure Monitor の incident platform は `AzureMonitor` connector と異なる機能である。Action Group を使わずに発火した alert を Agent へ取り込み、Agent は alert を acknowledge して調査を開始する。Microsoft Learn は、Agent が Azure を操作する user-assigned managed identity に Monitoring Contributor を subscription scope で割り当て、alert の acknowledge と close を行えるようにする構成を案内している（[Agent permissions](https://learn.microsoft.com/azure/sre-agent/permissions)）。Reader と Monitoring Reader の許可操作に、alert の状態を変更する `Microsoft.AlertsManagement/alerts/changestate/action` は含まれない。Monitoring Contributor は `Microsoft.AlertsManagement/alerts/*` を含む。この組み込みロールには alert rule や workspace などを変更できる操作も含まれるため、通常の読み取り診断とは分けて opt-in にする。

Reader 相当の権限を持つ Agent が権限を超える操作を実行しようとすると、その操作は On-Behalf-Of の承認待ち（`PendingAuthorization`）になる。承認できるのは SRE Agent Administrator の work または school アカウントだけである。

## Decision

- Azure SRE Agent を `base` Bicep layer に opt-in で追加する。既定では作成しない。`AZURE_SRE_AGENT_ENABLED=true` の場合、通常の `azd up` 全体デプロイで Agent も作成する。
- Agent 専用の User Assigned Managed Identity と Application Insights を作成し、既存の chaos-app Application Insights と Log Analytics を connector で接続する。
- Agent の操作設定を `Low` / `Review` とする。Agent identities に base Resource Group 範囲の Reader、Monitoring Reader、Log Analytics Reader を付与する。
- Azure Monitor metrics と AKS diagnostics は組み込み機能として扱い、独立した `MonitorClient` connector resource は作成しない。`AzureMonitor` connector が扱う subscription scope の alert は初期導入に含めない。
- `AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED=true` は Bicep で Azure Monitor を incident platform に設定し、Agent の user-assigned managed identity に subscription scope の Monitoring Contributor を付与する。system-assigned identity には付与しない。Monitoring Contributor の広い書き込み権限を避けるため、既定値は `false` とする。
- base layer の post-provision hook は `https://azuresre.dev` audience の token を使い、Azure SRE Agent Data Plane API で response plan を作成または更新する。ARM incident filter endpoint ではなく、[公式 template](https://github.com/microsoft/sre-agent/blob/ff317310cca5894fcd42ccc32f5452c3f408cc3c/sreagent-templates/bicep/apply-extras.sh) が使う `/api/v2/extendedAgent/incidentFilters/{name}` を使い、組み込み `meta_agent` に割り当てる。
- AI による incident response は、直近のサービス品質の悪化を検知する15分窓の短窓アラートに限定する。既定の発火条件は失敗 probe 5件以上とする。Hook は `SLI short-window alert` を `Title contains` に指定した1つの plan を設定し、Sev3 と `Review` を選ぶ。対象は Availability / Latency の2 alert とする。Baseline / 長窓アラートは長い評価窓の SLO 達成状況や error budget 消費を表すため、自動調査対象から外す。Kubernetes、publisher、Fleet 承認待ち、`aks-nodeos-autoupgrade` の alert も対象外とする。旧 Fast / Slow plan と、以前の広い title 条件を持つ plan が残っていれば削除する。title 条件は resource ID を区別しないため、同じ title を含む別環境の alert も一致する可能性がある。
- Action Group は引き続き任意とする。Azure Monitor incident platform は Action Group を経由せずに alert を取り込む。
- 組み込み `QueryPrometheusMetrics` による Managed Prometheus endpoint 探索を、初期構成、受け入れ条件、運用手順から除外する。サポート可否が確認できていない機能を、利用可能とも非対応とも断定しない。
- Agent を既存 VNet の専用 subnet に統合し、GitHub App の署名鍵用に public access 無効の Key Vault を用意する。Agent は Crypto User で署名を依頼し、秘密鍵の取得権限を持たない。ラボ環境を同名で作り直せるよう purge protection は設定せず、環境を完全削除するときは `azd down --force --purge` を使う。GitHub App の準備と Key import は通常配備の前提にせず、GitHub 認証と Code Access は配備完了後の独立した操作で接続する。異なる認証主体の準備待ちで環境構築を停止させないためである。
- README は既存ファイルを配備後にナレッジへ登録し、ナレッジ専用の構成説明は別に保守しない。配備と再適用で同じ元ファイルを使う。更新時点のコピーであり、リポジトリの自動同期とは区別する。
- 自動修復、定期実行、外部通知、Issue / PR 用 GitHub connector は初期導入に含めない。Alert response plan は前述の Azure Monitor 用 Review plan だけを対象とする。
- `enableSreAgent=false` で incremental deployment を行っても、既存 Agent を削除しない。Agent の廃止は別の明示的な削除操作とする。

## Consequences

- 利用者は `AZURE_SRE_AGENT_ENABLED=true` と `AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED=true` を設定し、`azd provision base` または `azd up` を実行して Agent と alert 連携を構成する。利用登録、リージョン、モデルの可用性、料金を事前に確認する。
- Bicep が Azure Monitor incident platform と Monitoring Contributor を設定し、post-provision hook が1つの response plan を構成する。plan は短窓アラートの title と Sev3 で対象を選び、Review mode で調査結果や操作案を提示する。同じ title を含む別環境の alert が一致する可能性がある。Baseline / 長窓アラート、Kubernetes、publisher、Fleet 承認待ち、node OS upgrade の alert 自体は Azure Monitor に残し、Agent の response plan では処理しない。
- Agent は Resource Group 内の Azure リソースと接続済みの Application Insights / Log Analytics を診断対象とする。
- Reader 系3ロールが許可しない操作（Function App の publish profile や app settings の一覧、AKS の kubeconfig 取得など）を Agent が試みると、調査は On-Behalf-Of の承認待ちで止まる。承認待ちを避けるための権限追加は、対象の操作ごとに別途判断する。この ADR では追加しない。
- Managed Prometheus endpoint 探索はプロジェクトの受け入れ条件ではない。今回の範囲外という判断から、製品機能全体の非対応は導かない。
- Agent が存在する間は Azure SRE Agent の利用料金が発生する。設定を無効にするだけでは Agent と専用リソースは削除されない。
