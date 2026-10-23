# 環境構築と運用手順

このドキュメントは、AKS Chaos Lab を構築・検証・削除するための手順をまとめます。設計判断の背景は [ADR 一覧](adr/INDEX.md)、既知のワークアラウンドと解消条件は [workarounds.md](workarounds.md) を参照してください。

コマンド例はリポジトリのルートで実行します。`<env>` は対象の azd 環境名を表し、`<...>` で示した値は利用する環境の値に置き換えてください。`-e` を省略した azd コマンドは、`azd init` または `azd env select "<env>"` で選択した環境を使います。

## 前提ツール

- Windows、macOS、または Linux
- [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
- Azure CLI + Bicep extension
- `kubectl`
- Python 3.14+ + [`uv`](https://github.com/astral-sh/uv)
- リポジトリ全体の QA を実行する場合は Docker と GitHub CLI + `gh-aw` extension

## 必要なロール

`azd up` を実行する identity (ユーザー / Service Principal) には、サブスクリプション スコープに加え、Azure Monitor SLI を有効化する場合はテナント レベルの Service Group スコープにも RBAC 権限が必要です。

### 1. サブスクリプション スコープ

- **Owner**、または **Contributor** + **User Access Administrator**
- リソース作成と、UAMI / AKS / Chaos Studio / Azure Monitor Workspace / DCR への RBAC 付与に必要な `Microsoft.Authorization/roleAssignments/write` を満たすこと

### 2. Service Group スコープ (Azure Monitor SLI を使う場合)

Azure Monitor SLI は Service Group に紐づくテナントレベル リソースです。Service Group / SLI はサブスクリプションを超えるため、サブスクリプション Owner だけでは作成できません。

- 既定構成 (`enableAzureMonitorSli=true`): tenant root の Service Group 配下に環境別 Service Group を作成するため、tenant root Service Group 上で Service Group の作成 / 子リソース管理ができる権限が必要
- 別の親 Service Group を使う場合: `AZURE_MONITOR_SLI_PARENT_SERVICE_GROUP_ID` / `azureMonitorSliParentServiceGroupId` で指定し、その親 Service Group 上で Service Group の作成 / 子リソース管理ができること
- 既存の環境別 Service Group を再利用する場合: `AZURE_MONITOR_SLI_SERVICE_GROUP_RESOURCE_ID` / `azureMonitorSliServiceGroupResourceId` で指定し、その Service Group で SLI 作成 (`Microsoft.Monitor/slis/write`) ができること
- 環境別 Service Group で作成権限を付与する構成例は、デプロイ実行 identity に対する **Service Group Administrator** の直接付与

Service Group RBAC の制約は [docs/workarounds.md §A-5](workarounds.md#a-5-環境別-service-group-への-service-group-administrator-直付与が必要) で棚卸ししています。

### 3. Azure SRE Agent を使う場合

Azure SRE Agent を有効にする環境では、デプロイ実行 identity に Agent、専用 Managed Identity、role assignments を作成する権限が必要です。`Microsoft.App` provider を登録し、subscription の Azure SRE Agent 利用登録と対象リージョン、モデルの利用可否を事前に確認してください。Azure Monitor incident integration も有効にする場合、subscription scope の Monitoring Contributor role assignment を追加する権限が必要です。このロールには alert rule や Log Analytics workspace などの書き込み操作が含まれます。

### 4. SLI 用 User Assigned Managed Identity に自動付与するロール

`infra/modules/azmonitor/sli-rbac.bicep` と `infra/modules/azmonitor/sli-managed-dcr-rbac.bicep` が、SLI 用 UAMI に以下を自動付与します。デプロイ主体にサブスクリプション スコープ権限があれば、追加操作は不要です。

- Azure Monitor Workspace: Monitoring Reader / Monitoring Data Reader / Monitoring Metrics Publisher
- Prometheus pipeline DCR: Monitoring Reader / Monitoring Metrics Publisher
- AMW managed resource group `MA_<amw-name>_<region>_managed` 内の同名 DCR: Monitoring Reader / Monitoring Metrics Publisher

managed DCR で `Monitoring Metrics Publisher` が不足すると、SLI の storage location validation が失敗します。`Monitoring Reader` は SLI destination metric の読み出し要件に合わせて付与します。

### 4. AKS の Microsoft Entra 統合

AKS は `aadProfile.managed=true` + `enableAzureRbac=true` + `disableLocalAccounts=true` のため、`kubectl`（`azd up` 中の Kubernetes マニフェスト適用を含む）は Microsoft Entra ID 経由の Azure RBAC で認可されます。`azd up` を実行する identity には、サブスクリプション スコープで **Azure Kubernetes Service RBAC Cluster Admin** を事前に付与してください。

## プレビュー機能とリソースプロバイダー登録

`azd up` 前に、サブスクリプション単位で以下のプレビュー機能とリソースプロバイダーを登録します。登録には Owner / Contributor 相当の権限が必要です。

```bash
# AKS のアドオン VPA
az feature register --namespace Microsoft.ContainerService --name AKS-AddonAutoscalingPreview

# OTLP 経由の Application Insights / Azure Monitor managed Prometheus 連携 (ADR-006)
az feature register --namespace Microsoft.ContainerService --name AzureMonitorAppMonitoringPreview

# 反映後に provider を再登録
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.KubernetesConfiguration
az provider register --namespace Microsoft.Insights
```

`az feature show --namespace <ns> --name <name>` で `state: Registered` になってから `azd up` を実行してください。feature flag 名と登録手順が現在の構成および関連 ADR と一致しているかは、[review-repo エージェント](../.github/agents/review-repo.agent.md) がリポジトリ内の記述を確認します。対象 subscription の登録状態は確認しないため、デプロイ実行者が `az feature show` で確認してください。

## 環境構築

本リポジトリは **AKS Base** モード前提で動作します。`infra/main.parameters.json` の `aksSkuName` と環境変数 `AKS_SKU_NAME` は `Base` のみ受け付けます。理由は [ADR-010](adr/010-aks-automatic-unsupported-due-to-deployment-safeguards.md) を参照してください。

Azure Kubernetes Fleet Manager が更新管理を担います。

- Fleet フリート / メンバー / 更新戦略 / 自動アップグレード プロファイルを `infra/modules/fleet.bicep` で自動作成
- 更新戦略は `beforeGates` に Approval ゲートを含み、手動承認まで Update Run を開始しない
- Control plane 用と NodeImage 用の autoUpgradeProfile が同じ承認ゲートを共有
- Azure Monitor Scheduled Query Rule `fleet-approval-pending` が、Approval Gate が Pending の間アクション グループに通知

base layer は AKS、Fleet module、Inspektor Gadget 拡張の順に作成します。Fleet module が失敗した場合、拡張の導入は開始しません。拡張追加時の依存の扱いと、この直列化の対象範囲は[ワークアラウンド D-13](workarounds.md#d-13-fleet-登録後に-aks-拡張を導入する)を参照してください。

Approval Gate の承認例:

```bash
az extension add --name fleet
az fleet gate list \
  --resource-group "<resource-group>" \
  --fleet-name "<fleet-name>" \
  --state Pending
az fleet gate approve \
  --resource-group "<resource-group>" \
  --fleet-name "<fleet-name>" \
  --gate-name "<gate-name>"
```

リソース名は `appName` と `environment` に応じて読み替えてください。

### `azd up`

```bash
azd config set alpha.aks.kustomize on
azd config set alpha.aks.helm on
azd init
azd up
```

`azd up` は `preup` hook で公開取得の許可設定を確認してから、`azure.yaml` の `workflows.up` に従って実行します。未確認の場合や user-level uv 設定と矛盾する場合は、最初の `provision base` より前に停止し、理由と専用手順を表示します。[公開取得の許可設定と Docker build の手順](#docker-build-のpackage-index)を参照してください。

1. `azd provision base` (`infra/main.bicep`) — VNet / AKS / Inspektor Gadget 拡張 / Redis / Application Insights / Managed Prometheus / external SLI publisher infra / Service Group / SLI 用 Managed Identity / RBAC を作成
2. `azd deploy api-instrumentation` — chaos-app 固有の Application Insights OTLP `Instrumentation` を先に適用し、AKS App Monitoring webhook が参照できる状態にする
3. `azd exec -- uv run --no-project scripts/tasks.py deploy-node-provisioning` — NAP 有効時だけ CRD の準備を待ち、既存の node-provisioning service を適用する。無効時はスキップする
4. `azd deploy api` — chaos-app をデプロイし、`postdeploy` hook で Pod に `OTEL_EXPORTER_OTLP_*` が注入されたことを確認
5. `azd deploy observability` — Envoy Gateway などをデプロイ
6. `azd deploy chaos-mesh` — Chaos Mesh を Helm install
7. `azd deploy external-sli-publisher` — Flex Consumption の Azure Functions publisher をデプロイ
8. `azd provision sli` (`infra/sli/main.bicep`) — layer `preprovision` hook で external SLI input metrics の出現を待ってから Azure Monitor SLI definitions と SLI metric alerts を作成

`base` と `sli` を分離する判断は [ADR-012](adr/012-functions-direct-external-sli-probe.md)、デプロイ後の SLI 評価開始の確認は [可観測性ガイド](observability.md#アプリ信頼性-signal)を参照してください。`azd up` は SLI destination metric の出現までは待ちません。

### Bicep の適用前差分確認

Bicep の差分確認には、[What-if 分析スキル](../.github/skills/bicep-what-if-analysis/SKILL.md)を使います。このスクリプトは `azd provision --preview` の出力を解析するのではなく、選択中の azd 環境と `azure.yaml` の layer に対応する Bicep、parameters file から `az deployment sub what-if` を実行します。対象環境を `azd env select "<environment>"` で選択してから実行してください。

```bash
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base

# external SLI metrics が出現した後に SLI layer を確認する場合
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer sli
```

スクリプトは Azure に what-if を照会し、成功時に `.github/skills/bicep-what-if-analysis/scripts/patterns/pattern_stats.json` を解析履歴として更新します。デプロイや preprovision / postprovision hook は実行しません。what-if も `azd provision --preview` と同様に、出力を人が確認して適用を判断する手順です。分析器は差分を細かく表示し、既知パターンに基づく分類と根拠を示しますが、適用可否は自動判定しません。

適用前に、次の順で判断してください。

1. 対象環境、subscription、layer が予定どおりで、what-if が最後まで完了したことを確認します。対象違いや解析失敗があれば適用しません。
2. Create、Modify、Delete の各リソースについて、変更対象とプロパティを予定していた変更と照合します。Delete、RBAC / identity、ネットワーク、セキュリティ設定、共有リソースの変更は個別に確認します。text 出力で有効差分の省略が警告された場合は、`--format json` で全差分を確認します。
3. `⚠️` と `❓`、および `pending` の差分は、Bicep の意図、既存リソースの状態、必要に応じて ARM schema と照合します。`noise_confirmed` や `📘` も、それだけで適用可とは判断せず、表示された根拠がそのリソースとプロパティに当てはまることを確認します。Bicep 照合の `notDefined` は、変数やモジュール経由の設定がない証明にはなりません。
4. 差分ごとに「予定した変更」「根拠を確認した既知の差分」「未説明」のいずれかに整理します。未説明の差分、予期しない削除、必要な差分の欠落が残る場合は適用を止め、原因を確認してから再度 what-if を実行します。すべての差分を説明できた後に、確認者が適用可否を決めます。

分類は差分の安全性を保証しません。特に Bicep 外で hook が行う操作は what-if の対象外です。適用する場合も、対象 layer の hook と実行される操作を別途確認してください。

検証中、新規環境の初回配備で Service Group（`Microsoft.Management/serviceGroups`）の作成が `ResourceNotFound` で失敗した事例がありました。その後の検証では再現していません。同じエラーで停止した場合は、同じ配備コマンドを再実行してください。発生時は Service Group が作成済みで、再試行により配備が完了したため、同様に成功が期待できます。

上記のサービス配備（2〜7）は、共通 task `deploy-services` が順番に実行します。通常の `up` と組織 index 向けの段階配備は、この task と各 service の hook を共有します。個別のサービスだけを更新するときは、従来どおり `azd deploy <service>` を使えます。

### Azure SRE Agent (任意)

SRE Agent は `base` layer の opt-in 機能です。`AZURE_SRE_AGENT_ENABLED` の既定値は `false` です。`true` にすると、`azd up` の全体デプロイで基盤とともに Agent を作成します。**この設定だけではインシデント連携は有効になりません。** Azure Monitor alert の取り込みには、後述の `AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED=true` も必要です。利用前に `Microsoft.App` provider、Azure SRE Agent の subscription 利用登録、対象リージョンとモデルの利用可否を確認してください。

```bash
azd env set AZURE_SRE_AGENT_ENABLED true
azd env set AZURE_SRE_AGENT_MONTHLY_AAU_LIMIT 10000
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
```

分析結果で Agent、専用 User Assigned Managed Identity、専用 Application Insights、Resource Group 範囲の role assignments を確認してください。`base` layer には他の基盤リソースも含まれるため、既存リソースの変更を含むすべての差分が意図した内容であることを確認してから、全体をデプロイします。公開取得が許可された環境では次を実行します。組織承認済み package index を使う場合は、同じ環境設定を使って [専用の配備手順](#docker-build-のpackage-index)を実行してください。

```bash
azd up
```

Agent は既存 VNet の専用 subnet `snet-sre-agent` に接続するため、リージョンは `AZURE_LOCATION` と同じにします。`AZURE_SRE_AGENT_LOCATION` を明示する場合も同じ値が必要で、異なる値は base の preprovision hook が拒否します。専用 subnet の prefix は `AZURE_SRE_AGENT_SUBNET_PREFIX`（既定 `10.10.5.0/27`、/27 以上）で指定します。モデル設定は `AZURE_SRE_AGENT_MODEL_PROVIDER` と `AZURE_SRE_AGENT_MODEL_NAME` で変更できます。

Agent 有効時は private Key Vault、Private Endpoint、Private DNS、Agent の Key Vault Crypto User、監査ログも Bicep で作成します。public access は無効、trusted services bypass は None です。base の postprovision hook は README のナレッジ登録とインデックス完了の確認を行い、incident integration が有効な場合だけ response plan も設定します。GitHub App、秘密鍵、GitHub 認証や Code Access の設定は待ちません。これらが未設定でも配備は完了します。

GitHub 連携は任意であり、環境作成や Azure SRE Agent の利用に必須ではありません。GitHub の後日接続、鍵登録、README だけの更新は [Azure SRE Agent の運用](sre-agent.md)を参照してください。

Agent は `Low` / `Review` で作成し、既存の chaos-app Application Insights と Log Analytics に connector で接続します。Azure Monitor metrics、Resource Graph、AKS 診断は Agent の組み込み機能で、Agent identity に付与した Azure RBAC 権限を使います。詳細は [Azure SRE Agent の connector と組み込み機能](https://learn.microsoft.com/azure/sre-agent/connectors)を参照してください。組み込み `QueryPrometheusMetrics` による Managed Prometheus endpoint 探索は本プロジェクトの初期導入範囲と受け入れ条件に含めません。今回の構成でのサポート可否を確認できていないためです。詳細は [Azure SRE Agent の可観測性](observability.md#azure-sre-agent) と [ADR-025](adr/025-azure-sre-agent-diagnostics-and-alert-response.md) を参照してください。

Azure Monitor の alert を Agent に取り込む場合は、別途次の設定を有効にします。

```bash
azd env set AZURE_SRE_AGENT_ENABLED true
azd env set AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED true
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
```

分析結果で Agent の `incidentManagementConfiguration` と subscription scope の Monitoring Contributor role assignment を確認してから、`azd provision base` または `azd up` を実行します。Bicep が Azure Monitor を incident platform として設定し、Agent の user-assigned managed identity に Monitoring Contributor を割り当てます。配備後も `az role assignment list --all --assignee <user-assigned identity の principal ID>` で割り当てを確認してください。権限は alert rule や Log Analytics workspace の変更にも及ぶため、subscription 全体への付与が適切か確認してください。

`base` layer の post-provision hook は、Azure SRE Agent の Data Plane API を使って、response plan `aks-chaos-lab-sli-short-window-review` を作成または更新します。`Title contains` は `SLI short-window alert` とし、Sev3 と `Review` mode に限定します。

対象は Availability / Latency の短窓アラート計2件です。既定では15分内に5件以上の失敗 probe がある場合に発火します。判定式は [SLI metric alerts](observability.md#sli-metric-alerts) を参照してください。

Baseline / 長窓アラートは長い評価窓の SLO 達成状況や error budget 消費を表すため、Agent の自動調査対象から外します。Kubernetes、publisher、Fleet 承認待ち、`aks-nodeos-autoupgrade` の alert も対象外です。これらの Azure Monitor alert と通知設定は維持します。

Hook は旧 Fast / Slow plan と、以前の広い SLI / Kubernetes / publisher / Fleet plan を削除します。削除対象の名前は [設定スクリプト](../scripts/configure_sre_agent_incidents.py) の `LEGACY_PLAN_NAMES` で管理します。削除した plan が開始済みの調査は完了まで継続します。title 条件は resource ID を区別しないため、同じ文字列を含む別環境の alert も一致する可能性があります。

アラート名を短窓 / 長窓へ変更すると、次回の `azd provision sli` は新しい名前のリソースを作成します。incremental deployment は旧名のアラートを削除しないため、再配備後に旧 `SLI fast burn rate alert for ...` と `SLI slow burn rate alert for ...` を確認し、残っているものを削除してください。

Hook は quickstart plan が有効なら無効化し、ほかに有効な Autonomous plan が残っている場合は処理を失敗させます。Plan は組み込み `meta_agent` を指定し、専用 incident handler は設定しません。response plan の作成には `https://azuresre.dev` audience の access token を使います。Action Group はこの連携に使いません。API とポータルでの設定方法は [Azure Monitor alert integration](https://learn.microsoft.com/azure/sre-agent/automate-incidents) と [response plan setup](https://learn.microsoft.com/azure/sre-agent/response-plan) を参照してください。

#### 既存環境へインシデント連携付きで追加する

対象環境を各コマンドの `-e` で指定します。subscription scope の Monitoring Contributor の付与を確認したうえで、Agent 本体と incident integration の両方を有効にします。

配備前に既存 SLI metric alert の location が AMW と一致することも確認します。異なる場合は、既存ルールの定義を保存し、同名で再配備する baseline ルールだけを削除してから `sli` を配備します。location は更新できないためです。詳細は [SLI metric alerts](observability.md#sli-metric-alerts) を参照してください。

```bash
azd env select "<environment>"
azd env set AZURE_SRE_AGENT_ENABLED true -e "<environment>"
azd env set AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED true -e "<environment>"
azd env set AZURE_SRE_AGENT_MONTHLY_AAU_LIMIT 10000 -e "<environment>"
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
azd provision base -e "<environment>"
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer sli
azd provision sli -e "<environment>"
```

各 layer の what-if 分析結果を確認してから対応する配備コマンドを実行します。`base` は Agent と Azure Monitor 接続、response plan を設定し、`sli` はその plan が照合する短窓アラートを配備します。既存環境のアラートが旧 Fast / Slow 名のままでは、`SLI short-window alert` の title 条件に一致しません。新しい短窓と長窓アラートの作成を確認後、前述の旧アラートを削除します。既存アプリの変更や再ビルドは不要です。

完了時は Agent の作成成功だけでなく、Azure Monitor 接続、専用 identity の Monitoring Contributor、有効な `Review` response plan、title 条件に一致する Availability / Latency の短窓アラート2件、README のインデックス完了、既存 API の応答を確認します。GitHub 接続は別の任意設定です。

Agent を `false` に戻して incremental deployment を行っても、作成済み Agent は削除されません。Agent と専用リソースを廃止する場合は個別に削除してください。Agent が存在する間は Azure SRE Agent の利用料金が発生します。

`api-instrumentation` は app-specific な `Instrumentation/chaos-app-otel` だけを `k8s/apps/chaos-app/instrumentation/` から適用します。クラスタ共通の `k8s/observability` には置きません。`Instrumentation` を `Deployment/chaos-app` より先に作成しないと、AKS App Monitoring の admission webhook が Pod template に `OTEL_EXPORTER_OTLP_*` を注入できず、API の Application Insights traces / metrics / logs と Redis dependency が欠落します。

External SLI publisher の Function host storage と deployment storage は managed identity 接続です。Storage account key / connection string に依存しないため、`allowSharedKeyAccess=false` の環境でも `azd deploy external-sli-publisher` を使います。publisher storage は `publicNetworkAccess=Disabled` とし、Function App を `snet-func` に VNet integration して blob / queue / table の Private Endpoint 経由で接続します。公式 azd sample と同じく、デプロイ実行 principal には Storage Blob Data Owner を付与します。

azd 1.33.0 は ZIP を Function App の SCM エンドポイント `/api/publish` へ送信します（[実装](https://github.com/Azure/azure-dev/blob/29133b640536436db9b56f8db4b1781cb136e5ba/cli/azd/pkg/azsdk/funcapp_host_client.go)）。デプロイ実行元に必要なのは SCM への接続で、storage の Private Endpoint へ直接アップロードする構成ではありません。storage への接続は Functions 側で行います。デプロイ失敗時に publisher storage の public access を一時的に開ける運用は行いません。

このプロジェクトは `azure.yaml` の `requiredVersions` で azd 1.35.1 以上を要求します。古い azd ではプロジェクトを実行せず、azd を更新してください。

環境設定の復元などで `azd env refresh` を使うと、過去のデプロイ出力から値が取り込まれます。リージョンや VM サイズを変更する場合は、refresh 後に設定してください。

### Local DNS

`AZURE_AKS_LOCAL_DNS_MODE` の既定は `Required`（有効）です。System pool と NAP に `azd up` で適用します。未設定の既存環境も有効化対象です。無効化する場合は、事前に `azd env set AZURE_AKS_LOCAL_DNS_MODE Disabled -e <environment>` を実行してください。再有効化は `Required` を指定します。

対応する Kubernetes、OS、VM サイズは [Microsoft Learn](https://learn.microsoft.com/azure/aks/localdns-custom)、採用判断は [ADR-019](adr/019-adopt-aks-local-dns.md)を参照してください。

既存環境で初めて有効化する際は、ノード更新前に `kubectl apply -f k8s/apps/chaos-app/ciliumnetworkpolicy-egress-allowlist.yaml` で DNS 許可を反映してください。有効化と無効化ではノードの再イメージ化による停止を想定し、他の Chaos 実験を停止して実施します。PDB があっても無停止は保証されません。

`<environment>` は対象の azd 環境名に置き換えます。what-if 分析を実行する前に対象環境を選択します。意図を説明できない差分がある場合は、続く `azd up` を実行しません。

```bash
azd env select "<environment>"
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
azd up -e <environment>
```

適用後は新規 Pod の名前解決と[ノード別メトリクス](observability.md#local-dns)を確認してください。API が返す Local DNS の状態が `state: Enabled` でも既存ノードに反映されない場合だけ、[D-14](workarounds.md#d-14-local-dns-の-api-更新が既存ノードへ反映されない場合)の追加対応を使います。DNS 障害注入の範囲は [Chaos 実験ガイド](chaos-experiments.md#local-dns-の比較検証)を参照してください。

### Node Auto Provisioning

Node Auto Provisioning（NAP）は既定で有効です。`AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING` が未設定の環境も有効化対象です。設計判断と採用条件は[ADR-018](adr/018-adopt-aks-node-auto-provisioning-for-arm64-capacity.md)、既定有効化の判断は[ADR-020](adr/020-enable-node-auto-provisioning-by-default.md)を参照してください。

NAP有効時はSystem AgentPoolがArm64 2台固定となり、Cluster Autoscalerは無効になります。未設定の既存環境でも次回のbase layer適用からこの構成が要求されるため、適用前に差分を確認してください。NAPを使わない新規環境や、NAP無効の既存環境を維持する場合は、適用前に `azd env set AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING false -e "<env>"` を実行してください。明示的な `false` は引き続き無効として扱います。

```bash
azd env select "<env>"
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
```

既存環境の分析結果にAKS以外の意図しない変更、またはSystem AgentPoolの削除や置換が含まれる場合は、base layerを適用しません。Azure PolicyがsubnetへBicep管理外のNSGを関連付ける環境では、NSG関連付けの削除が差分に表示されます。対象NSGがポリシー管理であり、subnetの名前、address prefix、delegationに変更がないことを確認した場合は、期待されたdriftとして扱います。

差分確認後、標準の `azd up` で AKS の NAP 設定と AKSNodeClass／NodePool を適用します。

```bash
azd up -e "<env>"
```

System AgentPoolが2台Readyで、既存workloadが健全であることを確認します。通常の `azd up` は instrumentation の後に NAP の条件付き task を実行します。単独で適用する場合も、同じ task で CRD の作成と Established を待ってから、User workload 用の AKSNodeClass と NodePool を適用します。

```bash
azd exec -e "<env>" -- uv run --no-project "${PWD}/scripts/tasks.py" deploy-node-provisioning
```

環境を読み込めない場合や flag が空文字または不正な値の場合は停止します。読み込んだ `AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING` が未設定または true なら NAP の適用を行い、false なら Kubernetes に接続せずスキップします。false の環境で再有効化する場合は、`azd env set AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING true -e "<env>"` を実行してから差分を確認してください。false に設定するだけでは既存の NodePool を削除せず、稼働中の NAP を無効化するには次の手順が必要です。

NAP の適用には azd を使います。azd が `k8s/node-provisioning/.env` を生成し、Local DNS の設定値を Kustomize へ渡すため、このファイルを手動で管理する必要はありません。

無効化するときは、次の順序で操作します。System AgentPoolは全工程で2台を維持します。

1. NAP capacityを必要とするUser workloadを停止する。
2. User NodeClaimが0台になったことを確認する。
3. `kubectl delete nodepool.karpenter.sh chaos-arm64`、続いて `kubectl delete aksnodeclass.karpenter.azure.com chaos-arm64` で対象の NodePool と AKSNodeClass を削除する。
4. `az aks update --node-provisioning-mode Manual`でNAPを無効化する。
5. `az aks nodepool update --enable-cluster-autoscaler --min-count 1 --max-count 3`でSystem AgentPoolのCluster Autoscalerを復元する。
6. System AgentPool、既存workload、外部health endpointを確認する。
7. `azd env set AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING false -e "<env>"` で無効を明示する。未設定にすると次回の適用で再び有効化されるため、設定を削除しない。
8. 対象環境を選択して What-if 分析スキルを `--layer base` で実行し、NAPに関する差分が解消したことを確認する。ポリシー管理のNSG関連付けは期待されたdriftとして残る場合がある。

## ローカル開発

リポジトリはuv workspace構成です。hostのuvはルート`pyproject.toml`の`required-version`の下限以上であれば使えます。GitHub Actionsとlock更新workflowはsetup-uvでその下限を選択します。Dockerのuv versionは`azd package api`との互換性のためDockerfileに明記し、`check-uv-version`がルートの下限との一致を検査します。ルートで一度同期すれば、`src/api`と`src/external-sli-publisher`の両方の依存と開発ツール（ruff、ty、pytest、locust）が揃います。

### Python 開発環境の手動セットアップ

この手順は Windows、macOS、Linux のターミナルで実行でき、Copilot App と Copilot CLI は不要です。Python の依存同期だけなら azd、Azure CLI、kubectl、Docker、Lefthook も不要です。Git hooks とリポジトリ全体の検査には、後述の追加ツールが必要です。

1. Git、ルート `pyproject.toml` の `required-version` の下限以上の uv、Python 3.14 系を用意する。組織承認済み index の同期は Python 3.14 を指定するため、Python 3.15 以降だけで準備済みとはしない。
2. Git で clone したリポジトリ、または作成した worktree のルートに移動する。
3. package index の利用方針を確認する。user-level uv 設定がない場合は public PyPI が選択される。組織承認済み index が必要な場合は、[index 設定と認証情報](#組織承認済み-package-index-を使う環境)を準備してから同期する。
4. 次のコマンドで uv の version 整合を確認し、成功したら開発依存を同期する。

macOS / Linux（bash または zsh）:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" check-uv-version &&
uv run --no-project "${PWD}/scripts/tasks.py" sync-dev
```

Windows PowerShell（ブロック全体を一度に実行）:

```powershell
& {
    uv run --no-project "${PWD}/scripts/tasks.py" check-uv-version
    if ($LASTEXITCODE -ne 0) { throw "uv version check failed" }
    uv run --no-project "${PWD}/scripts/tasks.py" sync-dev
    if ($LASTEXITCODE -ne 0) { throw "Development dependency synchronization failed" }
}
```

同期の終了コードが 0 で、選択された index に応じて `ok: Public PyPI development dependencies synced` または `ok: Approved-index development dependencies synced` が表示されれば成功です。`.venv` の存在だけでは成功と判断しません。同期後は、次のコマンドで開発ツールを確認できます（PowerShell でも同じコマンドを使えます）。

```bash
uv run --no-sync ruff --version
uv run --no-sync ty --version
uv run --no-sync pytest --version
```

仮想環境の activation は不要です。既定では各 worktree の `.venv` を使います。`UV_PROJECT_ENVIRONMENT` を設定する場合は、別 worktree の環境と共有しないよう対象を確認してください。

失敗した場合は、表示されたエラーに従って uv と Python、index 設定、認証、接続、package の取得可否と hash を確認し、失敗した処理の終了後に同じルートで次を再実行してください。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" sync-dev
```

組織承認済み index の同期は仮想環境を再作成するため、同期中は同じ環境のアプリや task を実行しないでください。失敗時に別 index へ自動で切り替えません。lock が不正な場合は、[public lockfile の更新](#public-lockfile-の更新)手順を使います。

### Copilot App のセッション作成時のセットアップ

[`.github/github-app.yml`](../.github/github-app.yml) は、セッション作成時に手動手順と同じ `sync-dev` を登録しています。App が認識する PATH に uv と Python を用意し、手動手順と同じ index 設定と必要な認証情報を App の実行環境でも利用できるようにしてください。設定をレビューして受け入れると、新しいローカルセッションの checkout を cwd として非同期に実行します。Git hooks の登録、QA、Azure 認証は実行しません。

設定の受け入れ前や既存セッションの再開時には、この登録による同期は行われません。依存定義や index 設定を変更した場合も、手動の `sync-dev` を使います。セットアップが失敗した場合は `Setup Failed` の通知を確認し、上の手動手順で原因を修正して再実行してください。branch セッションでは既存 checkout を同期するため、その環境のアプリや task と並行実行しないでください。

この設定はローカルセッション向けです。Windows App の shell とコマンド展開、Linux App、remote 実行は未検証です。Cloud の環境準備はこの設定の対象外です。

### コントリビュータ向け Git hooks

Lefthook を用意した開発者は、次のコマンドで Git hooks を登録します。Python の依存同期とは別の操作です。

```bash
lefthook install
```

`lefthook install` は、コントリビュータ向けにこのリポジトリの Git hooks を登録します。pre-commit は毎コミット、Git index 内の `uv.lock` とルート `pyproject.toml` を既存の public lock validator で検査します。作業ツリーとは独立してコミット対象を読み、非public source、必須ファイルの欠落、未解決の競合、解析不能の場合はコミットを中止します。public lock の検査では、依存の同期、ネットワーク接続、ファイルの修復や再ステージは行いません。実行には uv とインストール済みの Python 3.14 以降が必要です。

ステージされた `infra/` 配下の Bicep ファイルがある場合は、`infra/main.bicep` と `infra/sli/main.bicep` をビルドします。hook の未導入や `--no-verify` による省略に備え、CI の public lock 検査も維持します。Lefthook のインストール方法は [公式手順](https://lefthook.dev/install/) を参照してください。

### ローカルアプリの起動

開発環境の準備後、次のコマンドでアプリを起動します。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" run
```

### 組織承認済み package index を使う環境

依存同期は `sync` / `sync-dev` を明示して実行します。user-level uv 設定がなければ public PyPI を選び、設定に HTTPS の index が一つだけあり `default = true` の場合は、その index を選びます。public PyPI への接続可否は選択条件にせず、失敗時に別 index へ自動で切り替えません。設定の解析失敗、明示した `UV_CONFIG_FILE` の欠落、複数 index、不正 URL、取得元を変える環境変数は同期前にエラーになります。

組織承認済み index を使う場合は、user-level の `uv.toml` に一つだけ index を設定します。URL と認証情報をリポジトリへ保存しないでください。

```toml
[[index]]
name = "approved-index"
url = "https://<organization-index>/simple"
default = true
```

```bash
uv run --no-project "${PWD}/scripts/tasks.py" sync-dev
uv run --no-project "${PWD}/scripts/tasks.py" qa-app
```

`sync` は全 workspace package の runtime 依存だけを、`sync-dev` は全 dependency group を同期します。public 同期は `uv.lock` に記録した release cutoff を `--exclude-newer` で渡す `uv sync --locked --no-config` を使います。private 同期は public lock を検査し、frozen export から一時 requirements を生成して `uv pip sync --require-hashes` を使います。private index は public lock の hash と一致する artifact を提供する必要があります。private 同期にだけ必要な cache、仮想環境の再作成、一時 requirements、workspace `.pth` は同期処理に閉じています。

private 同期の認証情報は、選択された index 名に対応する `UV_INDEX_<NAME>_USERNAME` / `UV_INDEX_<NAME>_PASSWORD` 環境変数から同期 process にだけ渡します。`<NAME>` は英数字以外を `_` に置き換えて大文字にします。同期失敗時は URL や認証情報を含まない固定案内を出します。public 同期の失敗時も private へ切り替えず、組織承認済み index の設定を案内して失敗します。

依存定義（root/member の `pyproject.toml` または `uv.lock`）を変更した場合、新しい worktree を使う場合、runtime-only 環境から開発 task に切り替える場合、または registry 設定を変更した場合は、該当する `sync` / `sync-dev` を先に実行してください。同期と task を同じ worktree で並行実行しないでください。同期後の task は `uv run --no-sync` を使うため、暗黙の依存取得や lock 更新は行いません。post-edit hook も同期せず、準備済み `.venv` の Ruff のみを実行します。

不正または private index の URL を含む `uv.lock` は変更せずに同期を停止します。限定的な自動修復はありません。lock を更新する場合は、[public lockfile の更新](#public-lockfile-の更新)手順で public PyPI から作成した lock を取り込みます。

task runner を介さず workspace 内で `uv run` を直接使う場合も、暗黙同期を止めるため `--no-sync` を指定します。

```bash
uv run --no-sync <command>
```

### Docker build のpackage index

`azd up` の実行者は、public PyPI、公開コンテナレジストリ、Helm repository からの取得が利用方針で許可されていることを確認してください。コーディングエージェントは利用者による確認を得てから、対象環境に次を設定します。疎通成功や user-level 設定の不在を許可とはみなしません。

```bash
azd env set AZD_ALLOW_PUBLIC_SOURCES true -e "<env>"
```

`preup` hook は `AZD_ALLOW_PUBLIC_SOURCES` が未設定、不正、または `false` の場合に非ゼロで終了します。`true` でも、user-level uv に public PyPI 以外の index、取得元に関する追加設定、不正な設定、または引き継げない uv 環境変数があれば停止します。設定ファイルの探索は既存の専用 task と共通で、`UV_CONFIG_FILE`、`XDG_CONFIG_HOME`、OS ごとの既定位置に従います。公開取得の許可を取り消す場合は、この値を `false` に変更してください。

hook は標準ライブラリだけを使い、workspace の同期、ネットワーク接続、設定変更は行いません。起動にも `--offline --no-python-downloads` を指定するため、uv と Python 3.14 以降を事前に用意してください。このチェックは宣言された利用方針との矛盾を検出するもので、接続可否の確認や通信制御ではありません。個別の `azd package`、`azd deploy`、Docker build には `preup` は適用されません。

組織承認済み index が必要な環境では、許可設定や uv 設定を変更してチェックを通すのではなく、以下の専用手順を使います。hook は API build や適用方法の切り替えを自動実行しません。公開コンテナレジストリや Helm repository の利用も禁止されている場合は、別途その取得元を準備する必要があります。

通常のDocker buildはpublic PyPIを使用します。

```bash
docker build -f src/api/Dockerfile -t aks-chaos-lab:local .
```

組織承認済み package index が必要な環境では、専用 task で API image を build し、その成果物を `azd deploy --from-package` へ渡します。task は user-level `uv.toml` を検証し、BuildKit secret として build へ渡します。公開コンテナレジストリと ACR には接続できることが前提です。Python package index の制限への対応であり、コンテナレジストリをミラーする処理は含みません。

build が成功すると、ローカル image ID に対応する `aks-chaos-lab-approved:sha256-<image-id>` を出力します。deploy にはその参照を `--image` で必ず指定します。固定の `aks-chaos-lab:local` や最後に成功した build を暗黙に選びません。

既存環境の API 更新では、次のコマンド例のように build 後に deploy を実行します。`<image-reference>` は build task が出力したイメージ参照全体に置き換えてください。対象環境の Instrumentation は構築済みであることが前提です。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" package-api-approved-index
azd exec -e "<env>" -- uv run --no-project "${PWD}/scripts/tasks.py" deploy-api-approved-index \
  --image "<image-reference>"
```

deploy はローカルの ID と Arm64 architecture を確認してから既存 azd に渡し、適用後に ACR の manifest/config と Deployment、稼働 Pod の対応を照合します。Pod 不在、未 Ready、不一致や判別不能な応答は成功にしません。同じ内容の成果物の再適用は許容しますが、将来の外部操作による tag の上書きまで防ぐ仕組みではありません。

`--from-package` が省略するのは build です。API の deploy hook による Instrumentation の存在確認と Pod への OTel 設定注入の確認も自動実行されるため、確認スクリプトを別途実行する必要はありません。API の Kustomize に含まれる CiliumNetworkPolicy、ConfigMap なども再適用します。API 配下の宣言とデプロイ先のリソースの差分を確認し、意図しない変更があれば適用を停止してください。CiliumNetworkPolicy は CoreDNS と Local DNS への DNS 名制限付き通信を許可します。異なる DNS 構成を使う場合は、適用前に許可先の整合を確認してください。

初回構築では、ログイン、環境作成、権限と feature flag の準備後に、以下の環境設定と配備を順に実行します。すべての `<env>` に同じ環境名を指定してください。`azd up` との違いは API image の build と成果物の渡し方だけで、機能の有効化設定と provision hook は共通です。

**SRE Agent でインシデント調査まで利用する場合は、配備前に次の両フラグを設定します。** subscription scope の Monitoring Contributor を Agent の専用 identity に付与するため、[Azure SRE Agent の権限と設定](#azure-sre-agent-任意)を確認してください。診断のみの場合は incident integration のフラグを `false` にし、Agent を使わない場合はこの設定を省略します（両フラグの既定値は `false`）。

```bash
azd env set AZURE_SRE_AGENT_ENABLED true -e "<env>"
azd env set AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED true -e "<env>"
azd env set AZURE_SRE_AGENT_MONTHLY_AAU_LIMIT 10000 -e "<env>"
```

続いて、次の全工程を実行します。`<image-reference>` には build task が出力したイメージ参照全体を指定します。SRE Agent を有効にした場合、`base` で Agent と、有効化したインシデント連携を設定し、`sli` で対象アラートを配備します。`base` の成功だけで全体配備を完了としないでください。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" package-api-approved-index
azd provision base -e "<env>"
azd exec -e "<env>" -- uv run --no-project "${PWD}/scripts/tasks.py" deploy-services \
  --image "<image-reference>"
azd provision sli -e "<env>"
```

通常の `azd up`、`azd package api`、`azd deploy api` は public PyPI で API を build し、事前ビルドを引き継ぎません。制限環境では上記手順を使い、`azure.yaml` の一時書換えや public PyPI への fallback は行いません。API 更新時に他サービスを再適用する必要はありません。認証が必要な index は専用 Docker build の対象外です。`uv.lock` は public PyPI の URL を維持します。

Functions publisher は両方の手順で同じ prepackage hook を使います。hook は frozen な public lock から publisher の runtime 依存を export し、選択済み index から hash を検査して Linux x64 / Python 3.14 向け wheel を `.python_packages/lib/site-packages` に配置します。配備では `remoteBuild: false` とし、Functions 側で public PyPI から再取得しません。組織 index はこれらの wheel を提供する必要があり、ソース build や別 index への fallback は行いません。public index を選ぶ場合は `AZD_ALLOW_PUBLIC_SOURCES=true` が必要です。

publisher 単独更新も `azd deploy external-sli-publisher -e "<env>"` で同じ hook が動きます。`--from-package` で独自 ZIP を渡すと package hook が省略されるため、その ZIP に Linux 向け依存を含める責任は実行者にあります。通常は指定しません。host の開発用 `.venv` をパッケージへコピーしません。

base の SRE Agent hook も public / 組織 index で同じ処理を使います。Agent 有効時は README を登録し、incident integration 有効時は Azure Monitor 接続を確認して Review response plan を設定します。これらのスクリプトを手動で追加実行する必要はありません。GitHub の準備待ちを配備に含めず、後日設定後も通常の再配備は GitHub 認証設定を上書きしません。既存アプリを再配備せず Agent だけを追加する場合は、[既存環境への追加手順](#既存環境へインシデント連携付きで追加する)を使います。

### AKS の接続先と認証

azd と Azure CLI は別々にログインします。hook と専用 task は対象 AKS の情報を Azure CLI で照会するため、その identity にも管理プレーンの読み取り権限が必要です。Kubernetes の認証と RBAC は別に成立させてください。`azd exec -e "<env>"` は環境値を渡すだけで、Kubernetes の context を準備するコマンドではありません。

`KUBECONFIG` 未指定時は、hook と独自の稼働確認が処理専用の一時 kubeconfig を作り、対象 subscription、resource group、AKS を明示して user credentials を取得し、`azd` 認証へ変換します。実際の deploy は引き続き azd が標準の接続準備を行います。一時ファイルはその処理内だけで使い、終了時に削除します。先行 deploy や手元の既定 context に依存しません。azd 自体は共有の `~/.kube/config` を更新するため、既定設定の不変性は保証しません。

専用 kubeconfig を使う場合は、準備済みの単一ファイルを絶対パスで `KUBECONFIG` に指定し、同じ値を azd と hook に渡します。hook は ARM の対象 AKS と API server の対応を検証し、credentials の再取得、認証方式の変換、context の書換えは行いません。不在、不正、対象不一致は停止理由です。相対パス、複数ファイルのマージ、proxy 経由の独自 endpoint は対象外です。必要な `kubelogin` の準備と、そのファイルに設定された認証方式でのログインは利用者が行ってください。

readiness と OTel helper の `--timeout-seconds` は、環境解決から接続準備、待機、最後の照会までを含む全体上限です。rollout の個別上限は全体の残り時間を延長しません。期限切れや認証失敗では、その理由を確認し、別 context への切替えで成功を代用しないでください。

### public lockfile の更新

dependencyを変更する場合、public PyPIへ接続できるGitHub Actionsでlockfileを生成します。workflowはrepositoryへのwrite permissionを持たず、`uv.lock` をartifactとして返します。手順は次の3段階です。

1. `pyproject.toml`（ルートまたは `src/` 配下）の変更をcommitしてpushし、pull requestを開きます。`refresh-uv-lock.yml`はこれらのファイルと`uv.lock`、workflow定義自身を変更したpull requestで自動実行されます。pull requestを開かない場合は`gh workflow run refresh-uv-lock.yml --ref <branch>`で起動します。
2. workflowの成功を確認してから、現在のcommitに対応するlockを取り込みます。

    ```bash
    uv run --no-project "${PWD}/scripts/tasks.py" adopt-public-lock
    ```

3. 表示された差分を確認してcommitします。

`adopt-public-lock`は現在の`HEAD`と同じcommitで成功した`refresh-uv-lock.yml`のrunだけを選び、`uv-lock-public`のartifactを取得して`uv.lock`を置き換えます。commitとpushは行いません。次の場合は`uv.lock`を変更せずに停止します。

- 対応するrunが無い、またはartifactを取得できない（retentionは7日です。`gh workflow run refresh-uv-lock.yml --ref <branch>`で再生成してください）
- `uv.lock`かworkspaceの`pyproject.toml`に未commitの変更、staged変更、競合がある
- 取得したlockがpublic PyPI以外のsourceを含む
- 取得中に`HEAD`か`uv.lock`が変わった

runを明示的に選ぶ場合や、このtaskを使えない場合は、現在の`HEAD`と同じcommitで成功したrunを選び、artifactを`uv.lock`へ上書きしてから、taskと同じ検査を自分で実行します。

```bash
gh run list --workflow refresh-uv-lock.yml --branch <branch> --commit "$(git rev-parse HEAD)" --status success
gh run download <run-id> --name uv-lock-public --dir tmp/refresh-uv-lock
cp tmp/refresh-uv-lock/uv.lock uv.lock
uv run --no-project "${PWD}/scripts/tasks.py" check-public-lock
uv run --no-project "${PWD}/scripts/tasks.py" check-uv-lock
```

いずれかの検査が失敗した場合は`git checkout -- uv.lock`で戻し、workflowを再実行してください。`uv.lock`の手編集で検査を通さないでください。

Renovateのpull requestのように`pyproject.toml`だけが更新された場合も同じ手順です。CIの`uv Lock Check`（`check-uv-lock`）が失敗するのは、lockがまだ取り込まれていないことを示します。`uv.lock`を取り込んだ後は、開発 task の前に `sync-dev` を実行してください。組織承認済み package index は public lock と同一 hash の artifact を提供する必要があります。

`refresh-uv-lock.yml`は、公開から一定期間を経ていないreleaseを選ばないcutoffを指定して解決します。cutoffは`lock-cutoff` taskが算出し、生成された`uv.lock`の`[options]`に記録されます。`check-uv-lock`はその記録値を読み、期間を満たしていることを確認したうえで、同じ値でネットワークを使わずに`uv lock --check`を実行します。cutoffが記録されていない`uv.lock`は検査を通りません。この場合はworkflowで再生成して取り込んでください。cutoffは直接依存だけでなく推移的依存にも適用されます。

ローカルで`uv lock`を実行して生成したlockはcutoffを記録しないため、どの環境でもCIの`check-uv-lock`を通りません。private index を使って lock を生成すると取得元も変わり、pre-commit の `check-public-lock.py` が失敗します。誤って変更した場合は差分を確認し、必要なら public lockfile 更新手順で生成した `uv.lock` を取り込みます。validator と同期処理は lock を修復しません。

Renovateはworkspaceの依存について更新候補の検出だけを行い、lockは更新しません。workspace member、`resolution-strategy = "lowest"`、public PyPIを参照する`uv.lock`、external SLI publisherのrequirements同期を一度の更新で維持できることを保証できないためです。lockの更新経路は`refresh-uv-lock.yml`のままとし、取得した`uv.lock`は`check-uv-version`、`check-public-lock`、`check-publisher-requirements`、既存QAで検証します。責務の全体像は[依存パッケージとツールの更新管理](dependency-management.md)を参照してください。

## テストと品質確認

アプリケーション:

クリーン環境や新しいworktreeでは、workspace task の前に `uv run --no-project "${PWD}/scripts/tasks.py" sync-dev` を実行してください。public と private のどちらも同じ明示同期 task を使います。絶対 path と `--no-project` は、task runner の起動前に uv が別の project を探索または同期することを防ぎます。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" test
uv run --no-project "${PWD}/scripts/tasks.py" test-cov
uv run --no-project "${PWD}/scripts/tasks.py" lint
uv run --no-project "${PWD}/scripts/tasks.py" typecheck
uv run --no-project "${PWD}/scripts/tasks.py" qa-app
```

Bicep（base と sli の両 layer）:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" build-bicep
```

Git hooks:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" test-hooks
```

依存とツールのversion契約:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" check-version-pins
uv run --no-project "${PWD}/scripts/tasks.py" check-renovate-config
```

`check-version-pins`はオフラインで完結し、`review-repo-fast`とCIが実行します。`check-renovate-config`はpin済みRenovate imageを必要とし、CIの専用jobで実行します。更新候補の検出責務と結果の解釈は[依存パッケージとツールの更新管理](dependency-management.md)を参照してください。

リポジトリ全体:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" qa
```

`uv run --no-project "${PWD}/scripts/tasks.py" qa`はworkflows、Bicep、Kubernetes manifests、アプリ、リポジトリ用Python scriptsのQAをまとめて実行します。必要な外部ツールの確認は`uv run --no-project "${PWD}/scripts/tasks.py" install-tools`と`check-*`ターゲットで実行できます。

### full レビューの Python 環境

`review-repo-full` は専用または利用者が明示的に用意した worktree で実行します。レビュー task に独自の環境準備、同期状態の引き渡し、process 間 lock はありません。レビューを始める前に `sync-dev` を実行し、各 task は準備済み `.venv` を `--no-sync` で使います。

### GitHub Actions の統合テスト

`Platform Integration Test`（`.github/workflows/integration-test.yml`）は手動起動です。`full` は一時環境で既存の `azd up` を実行し、HTTP endpoint を検査します。`infra-only` は base layer の構築だけを行い、アプリと sli layer は適用しません。AKS は現在の IaC と同じ Base に固定し、`Automatic` と `app-only` は選択肢に含めません。

GitHub Environment `integration-test` に認証用 secrets `AZURE_CLIENT_ID`、`AZURE_TENANT_ID`、`AZURE_SUBSCRIPTION_ID` を設定します。identity には本書の構築と削除に必要な権限を付与します。`full` で公開取得が許可されている場合は、同 Environment の変数 `AZD_ALLOW_PUBLIC_SOURCES` を `true` に設定してください。workflow は未設定の値を補完しません。runner は `ubuntu-latest` を維持し、uv と Python を各処理の前に準備します。

cleanup job は構築の成否にかかわらず、実行IDに対応する一時 azd 環境を作成し、`azd down --force --purge` を実行します。`.azure` の引き継ぎは行わず、環境名、location、subscription、Resource Group を再設定します。hook は Service Group など azd の管理対象外のリソースを整理し、Resource Group の削除と Key Vault の purge は azd に任せます。job の上限は90分で、削除失敗は job の失敗として報告します。

## 負荷テスト

Locust ベースの負荷を生成できます。`BASE_URL` 未指定時は `AZURE_INGRESS_FQDN` を優先し、未設定の場合は Gateway から自動検出します。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" load-smoke
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline
uv run --no-project "${PWD}/scripts/tasks.py" load-stress
uv run --no-project "${PWD}/scripts/tasks.py" load-spike
```

手動で対象 URL や負荷パラメーターを指定する場合は、利用中のシェルで環境変数を設定してから同じタスクを実行します。

```powershell
$env:BASE_URL = "http://<host-or-ip>"
$env:USERS = "100"
$env:SPAWN_RATE = "10"
$env:DURATION = "300"
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline
```

```bash
export BASE_URL=http://<host-or-ip>
export USERS=100
export SPAWN_RATE=10
export DURATION=300
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline
```

Chaos実験の観察時は、別ターミナルで`uv run --no-project "${PWD}/scripts/tasks.py" load-baseline`を継続しながら[docs/chaos-experiments.md](chaos-experiments.md)の実験を開始すると挙動を追いやすくなります。

## 環境削除

Azure Monitor SLI を有効化した環境では、Service Group scope の `Microsoft.Monitor/slis` と環境別 Service Group が resource group の外に存在します。削除時は cleanup hook を有効にして project-level `azd down` を実行してください。

```bash
azd down --force --purge
```

`predown` hook は対象環境の `AZURE_SUBSCRIPTION_ID` を必須とし、取得できなければ削除前に停止します。Azure CLI の既定 subscription は使わず、取得した値を各操作へ明示します。Service Group は tenant scope のため、環境名による所有確認も維持します。指定した RG が対象環境の命名規則に一致しない場合は、削除前の cleanup を停止します。

Service Group ID が環境変数にない場合は、対象環境の base デプロイの操作記録から取得します。操作記録を手動で削除した環境では、`AZURE_MONITOR_SLI_SERVICE_GROUP_ID` または `AZURE_MONITOR_SLI_SERVICE_GROUP_NAME` を指定してください。SG 本体の取得が `ResourceNotFound` を返した場合だけ SG の削除を省略し、SLI 一覧の403を削除済みとは扱いません。

SG の DELETE が202を返した場合は、応答の `Location` で非同期削除の完了を確認します。`Retry-After` に従い、指定がなければ5秒間隔で、削除要求と完了待ちを合わせて最大10分待機します。処理中の SG 本体の GET は削除完了の判定に使いません。

Service Group 側の探索や削除に失敗した場合は、エラーを記録して subscription 内の後始末を続け、最後に非ゼロで終了します。この場合、再実行で SG を特定できるよう base デプロイ記録は残します。RG、AKS、DCRA の存在確認は、正常な応答で対象の不在を確認できた場合だけ削除済みと扱います。存在確認や DCRA 削除後の確認に失敗した場合は、`predown` hook が非ゼロで終了するため `azd down` は続行されません。Service Group API の制約と改善確認項目は [workarounds.md §A-8](workarounds.md#a-8-service-group-api-の探索と不在応答を補う) を参照してください。

`predown` hook は Service Group scope の SLI / Service Group と AKS の OTLP Application Insights DCR association、SLI layer の deployment record を整理します。base resource group は hook で削除せず、`azd down` に削除させます。`postdown` hook は残った base layer の deployment record を削除します。これにより `azd down --purge` が Key Vault を含むリソースを削除し、soft-deleted resource を purge する機能を利用できます。AKS 上の OTLP DCRA を先に削除することで、App Insights managed resource group (`ai_<appi-name>_<guid>_managed`) は base RG の削除に連動して消えます。詳細な順序と理由は [ADR-009](adr/009-azure-monitor-sli-and-prometheus-slo.md)、[docs/workarounds.md §A-4](workarounds.md#a-4-predown-hook-で-service-group-scope-sli-と環境別-service-group-を削除)、[§B-2](workarounds.md#b-2-predown-hook-で-aks-上の-otlpappinsightsextension-dcra-を先に削除) を参照してください。

cleanup hook の構文・実行経路だけを非破壊で確認する場合は、削除系処理を dry-run にして hook を単体実行できます。

```powershell
$env:AZURE_MONITOR_SLI_CLEANUP_DRY_RUN = "true"
azd hooks run predown --platform windows
Remove-Item Env:AZURE_MONITOR_SLI_CLEANUP_DRY_RUN
```

```bash
export AZURE_MONITOR_SLI_CLEANUP_DRY_RUN=true
azd hooks run predown --platform posix
unset AZURE_MONITOR_SLI_CLEANUP_DRY_RUN
```

注意点:

- cleanup hook は system-protected deny assignment を解除しません
- `azd down <layer>` のような layer 指定 down はサポート対象外です
- `AZURE_MONITOR_SLI_CLEANUP_DRY_RUN=true` を付けた hook 単体実行では削除せず、対象検出とログ出力だけを行います
- SRE Agent を有効にした環境の Key Vault（`kvsregithub<resourceToken>`）は purge protection を設定しません。環境を完全削除して同じ名前で再作成する場合は `azd down --force --purge` を使い、azd に Key Vault の purge を処理させます。purge protection 付きで作成された旧 vault は purge できず、`scheduledPurgeDate` の自動消去まで名前が残るため、その間は別の環境名を使ってください
