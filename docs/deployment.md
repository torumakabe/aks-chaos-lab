# デプロイメント - AKS Chaos Lab

本書はAKSへのデプロイ手順と構成方針を示す。

## 前提条件
- **動作環境**: Linux (WSL) または macOS
- [Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/) **推奨**
- Azure CLI + Bicep extension
- **アドオンのVPAによるコスト最適化機能**: `aks-preview` 拡張機能 + プレビュー機能フラグ `AKS-AddonAutoscalingPreview` の登録が必要
  ```bash
  # aks-preview拡張機能のインストール
  az extension add --name aks-preview
  az extension update --name aks-preview
  
  # プレビュー機能の登録
  az feature register --namespace "Microsoft.ContainerService" --name "AKS-AddonAutoscalingPreview"
  az feature show --namespace "Microsoft.ContainerService" --name "AKS-AddonAutoscalingPreview"
  az provider register --namespace Microsoft.ContainerService
  ```
- kubectl
- Python 3.13+ + [uv](https://github.com/astral-sh/uv)
- サブスクリプション権限: Contributor 以上
- リージョン例: japaneast
- azd Helm/Kustomize は現時点で alpha 機能のため有効化が必要（本手順で使用）

### azd alpha 機能の有効化
Helm/Kustomize の alpha 機能を有効化します。

```bash
azd config set alpha.aks.kustomize on
azd config set alpha.aks.helm on
```

## 構成要素
- **AKS**: Azure CNI Overlay + Cilium, Workload Identity
  - **SKU**: BaseとAutomaticの両方をサポート（パラメーターで選択可能）
    - **Base**: 従来のAKS
    - **Automatic**: より自動化された運用を提供する新しいAKSモード
  - **自動アップグレード（Baseモード）**:
    - Kubernetesバージョン: Fleet Manager経由で手動承認制御
    - Node OSイメージ: AKSネイティブ自動アップグレード（`nodeOSUpgradeChannel=NodeImage`、毎週水曜日メンテナンスウィンドウ）
  - **ノード自動スケーリング**: Base - Cluster Autoscaler、Automatic - Node Auto Provisioning
  - **Cost Analysis**: AKS コスト分析アドオンを有効化
  - **Availability Zones**: 1 / 2 / 3（リージョン対応時）
  - **セキュリティ**: ローカルアカウントを無効化し、Azure AD/Entra IDのみの認証を強制
    - Base モード: `disableLocalAccounts: true` を明示的に設定
    - Automatic モード: 既定でローカルアカウントが無効化されているため追加設定不要
    - クラスターへのアクセスは `az aks get-credentials` で取得する Azure AD トークンベースの認証が必要
    - アイデンティティガバナンス、条件付きアクセスポリシー、監査性が向上
- **Advanced Container Networking**: L7ネットワークポリシー + 可観測性
- **Container Insights**: AMA + DCR による統合監視（`azureMonitorProfile.containerInsights` + DCR/DCRA）。Portal 互換のため一時的に ContainerLog(V1) も併用しています。
- **Prometheus (Managed)**: Azure Monitor managed Prometheus を有効化（`azureMonitorProfile.metrics.enabled=true`）。Azure Monitor Workspace(AMW) は既定で作成（`enablePrometheusWorkspace=true`、無効化可）。
  - 収集パイプライン: DCE/DCR/DCRA を Bicep で構成（`enablePrometheusPipeline=true`）。
  - レコーディングルール: Linux/UX を `prometheusRuleGroups` で作成（`enablePrometheusRecordingRules`）。
  - WARメトリクス収集: 公式手順に従い AMA 設定ConfigMapで Podアノテーションスクレイプを有効化
    - `k8s/observability/ama-metrics-settings-configmap.yaml`（`podannotationnamespaceregex` を設定、`podannotations=true`）
    - 静的ターゲットの追加ジョブは使用しない（公式手順に忠実に、アノテーションベースのみを採用）
- **ACR**: Azure Container Registry (Premium SKU, Private Endpoint + Public Access Enabled)
- **Azure Managed Redis**: Private Endpoint + Entra ID認証
- **Application Insights + Log Analytics (Workspace-based)**
- **Web Application Routing**: AKSアドオン有効化、カスタムNginxIngressController（静的IP設定、Prometheusメトリクス対応）
- **Chaos Studio**: 実験リソース + Chaos Mesh (AKS内)

## デプロイ手順

本リポジトリは**AKS Base**モードと**AKS Automatic**モードの両方をサポートしています。パラメーターファイル（`infra/main.parameters.json`）で`aksSkuName`を変更することで選択可能です：
- **Base**: 従来のAKS（デフォルト）
- **Automatic**: より自動化された運用を提供する新しいAKSモード

Base モードを選択した場合、Bicep は更新管理のために次を自動的にプロビジョニングします：

**Kubernetesバージョンの更新（Fleet Manager経由）**:
- Azure Kubernetes Fleet Manager フリート（`fleet-${appName}-${environment}`）
- AKS クラスタをフリート メンバーとして登録（グループ名 `base-cluster`）
- `beforeGates` に Approval ゲートを持つ更新戦略（`base-manual-approval`）
- Stable チャンネルの自動アップグレード プロファイル（制御プレーン更新を承認後に実行、`nodeImageSelection.type=Consistent`）
- Azure Monitor Scheduled Query Rule `fleet-approval-pending`（ARG から Approval Gate の Pending を検出し、アクション グループへ通知）

**Node OSイメージの更新（AKSネイティブ機能）**:
- AKS自動アップグレードプロファイル（`nodeOSUpgradeChannel=NodeImage`）
- メンテナンスウィンドウ設定（`aksManagedNodeOSUpgradeSchedule`、毎週水曜日 00:00 JST、4時間）
- Azure Monitor Scheduled Query Rule `aks-nodeos-autoupgrade`（ARG からNode OSアップグレードイベントを検出し、アクション グループへ通知）

承認ワークフローは手動で実施する必要があります：
```bash
# Fleet CLI 拡張機能をインストール
az extension add --name fleet

# Pending 状態のゲートを確認（例）
az fleet gate list \
  --resource-group rg-aks-chaos-lab-dev \
  --fleet-name fleet-aks-chaos-lab-dev \
  --state Pending \
  --query "[0].name"

# ゲートを承認（取得した gate-name を指定）
az fleet gate approve \
  --resource-group rg-aks-chaos-lab-dev \
  --fleet-name fleet-aks-chaos-lab-dev \
  --gate-name <gate-name>
```
> `rg-aks-chaos-lab-dev` および `fleet-aks-chaos-lab-dev` は既定値（`appName=aks-chaos-lab`, `environment=dev`）の例です。環境に応じて置き換えてください。

承認が完了すると Update Run が生成され、Fleet が AKS 制御プレーンの Kubernetes バージョンを更新します。承認しない限り Update Run は開始されません。
Node OS イメージの更新は AKS のメンテナンスウィンドウ（毎週水曜日）に自動実行され、Fleet の承認は不要です。
アクション グループ ID (`actionGroupId`) を指定しない場合でもアラート リソースは作成されますが通知は行われません（ARG クエリによるモニタリング用途）。

### 方法1: Azure Developer CLI (推奨)

```bash
# 1. プロジェクトのセットアップ
# alpha機能（Helm/Kustomize）を有効化
azd config set alpha.aks.kustomize on
azd config set alpha.aks.helm on
azd init

# 2. インフラストラクチャのプロビジョニング
azd provision

# 3. アプリケーションのデプロイ
azd deploy
# サービス構成: api / observability / chaos-mesh
# - api: k8s/base を kustomize 適用（azd が一時 .env を注入し、replacements で各リソースへ反映）
# - observability: kube-system に AMA 設定ConfigMapを適用し、Ingressメトリクスのアノテーションスクレイプを有効化
# - chaos-mesh: Helm リリース（charts.chaos-mesh.org, chart=chaos-mesh, version=2.7.3）を chaos-testing に導入
# Web Application Routing の NginxIngressController は k8s/base/nginx-ingress-controller.yaml で定義

# または一括実行
azd up

# 4. 動作確認
azd show
kubectl get pods -n chaos-lab

# 5. アプリケーションのテスト
kubectl get ingress -n chaos-lab
# Ingress の ADDRESS を使用してアクセステスト
curl http://<INGRESS_ADDRESS>/
curl http://<INGRESS_ADDRESS>/health

# 6. WARメトリクスの収集を有効化（SLO監視）
# azd経由で適用済み（サービス: observability）。手動適用する場合は以下でも可:
# kubectl apply -k k8s/observability
# アノテーションベースのスクレイプ有効化後、kube-system/ama-metrics Pod が再起動して設定が反映されます
```

注意:
- まれに RBAC 伝播の遅延や環境の呼び出し主体差異により、`...subnets/join/action` のエラーが出る場合があります。
- 本テンプレートは AKS の UAMI に対してサブネットの Network Contributor を自動付与しています。数分待ってから `azd provision` を再実行してください。
- それでも解消しない場合は、エラーメッセージに表示された client id（呼び出し主体）に対して、当該サブネットに Network Contributor を一時的に付与して再実行してください。

### 開発・テスト環境での使用

**ローカル開発**
```bash
cd src
uv sync --group dev
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**テスト実行**
```bash
cd src
make test            # 単体テスト
make test-cov        # カバレッジレポート生成（htmlcov/）
make lint            # リント（ruff）
make typecheck       # 型チェック（ty）
make qa              # リント+テスト+型チェック 一括
```

### 方法2: Bicep直接デプロイ（サブスクリプション スコープ）

```bash
# 1. サブスクリプション スコープでデプロイ（RGはテンプレートが作成します）
# リソースグループはテンプレートが作成します。事前作成は不要です。
az deployment sub create \
  --location japaneast \
  --template-file infra/main.bicep \
  --parameters location=japaneast

# 2. AKS認証情報取得（作成された RG/AKS 名は既定だと以下の通り）
# 生成されたリソースグループ名の例: rg-aks-chaos-lab-dev
az aks get-credentials \
  --resource-group rg-aks-chaos-lab-dev \
  --name aks-aks-chaos-lab-dev

# 3. アプリケーションデプロイ（kustomizeを直接使用する場合）
# 事前に `k8s/base/.env` を作成（azd の環境変数と同等の内容: AZURE_* / APPLICATIONINSIGHTS_CONNECTION_STRING など）した上で以下を実行
kustomize build k8s/base | kubectl apply -f -
```

## デプロイ内容詳細

### 1. インフラストラクチャ (Bicep)
- **リソースグループ**: 全リソースを管理
- **VNet + サブネット**: AKS(10.10.1.0/24) + PE(10.10.2.0/24)
- **NSG**: `snet-aks` に NSG を関連付け、受信 TCP 80/443 を許可
- **AKS**: Advanced Networking, Container Insights有効, SKU=Standard/Automatic（Uptime SLA）, Availability Zones=1/2/3
  - **自動アップグレード（Baseモード）**:
    - Kubernetesバージョン: Fleet Manager経由（手動承認制御、`channel=Stable`）
    - Node OSイメージ: AKSネイティブ（`nodeOSUpgradeChannel=NodeImage`、メンテナンスウィンドウ: 毎週水曜日）
  - 可観測性向上: Azure Monitor managed Prometheus（メトリクス）+ Grafana Dashboard + Container Insights（ログ/メトリクス）+ Cost Analysis。LA Workspaceは `log-...` を使用。
- **ACR**: Premium SKU, Kubelet identityにAcrPull権限付与, Private Endpoint(`registry`サブリソース) + Private DNS(`privatelink.azurecr.io`) 構成, PublicNetworkAccess=Enabled
- **Azure Managed Redis**: Private Endpoint経由, accessPolicyAssignments設定
- **UAMI**: Workload Identity用, Federated Credential設定
- **可観測生向上**: Log Analytics, Application Insights

### 2. アプリケーション (Kubernetes)
- **Namespace**: chaos-lab
- **ServiceAccount**: chaos-app-sa (Workload Identity有効、Client ID自動注入)
- **Deployment**: 1レプリカ（初期値）, Redis + OpenTelemetry統合
- **HorizontalPodAutoscaler**: CPU 70%/メモリ 80%閾値、2-4レプリカ自動スケール
- **ConfigMap**: app-config (動的生成、環境変数含む)
- **Service**: ClusterIP
- **NginxIngressController**: カスタムコントローラー（`nginx-static`、静的IP設定）
- **Ingress**: Web Application Routing（`ingressClassName: nginx-static`）
 - **NetworkPolicy**: `chaos-app` は Ingress Controller からの受信のみ許可（`k8s/base/networkpolicy.yaml`）
- **CiliumNetworkPolicy**: `chaos-app` の送信は Redis と App Insights を許可（DNS含む、`k8s/base/ciliumnetworkpolicy-egress-allowlist.yaml`）
 - **CiliumNetworkPolicy**: App Insights 送信と認証(MSAL)も許可（`*.in.applicationinsights.azure.com`, `*.livediagnostics.monitor.azure.com`, `dc.services.visualstudio.com`, `live.applicationinsights.azure.com`, `login.microsoftonline.com` の 443/TCP）

### 3. 動的設定管理（azd Kustomize env + replacements）
- `azure.yaml` の `services.api.k8s.kustomize.env` に、azd 環境変数をキー/値で列挙（例: `AZURE_REDIS_HOST: ${AZURE_REDIS_HOST}` 等）
  - azd が Kustomize ディレクトリ（`k8s/base`）に一時 `.env` を生成
- `k8s/base/kustomization.yaml`
  - `configMapGenerator.literals` で静的アプリ設定（`APP_PORT`、`LOG_LEVEL`、`TELEMETRY_*` など）を定義
  - `configMapGenerator.envs` に `.env` を指定（azd が生成する動的値を取り込み）
  - `replacements` で ServiceAccount 注釈 / Ingress host / NginxIC アノテーションを上書き
- `postdeploy` は不要（ベースYAMLは不変）

#### 設定項目の例

静的（literals）:
- `APP_PORT`, `LOG_LEVEL`, `TELEMETRY_ENABLED`, `CUSTOM_METRICS_ENABLED`, `TELEMETRY_SAMPLING_RATE`

動的（.env 由来）:
- `AZURE_CHAOS_APP_IDENTITY_CLIENT_ID`, `AZURE_INGRESS_FQDN`, `AZURE_INGRESS_PUBLIC_IP_NAME`, `AZURE_RESOURCE_GROUP`
- `APPLICATIONINSIGHTS_CONNECTION_STRING`, `AZURE_REDIS_HOST`, `AZURE_REDIS_PORT`

## Web Application Routing
- AKS の Web Application Routing アドオンを有効化（`ingressProfile.webAppRouting.enabled=true`）
- カスタム NginxIngressController リソース（`k8s/base/nginx-ingress-controller.yaml`）を作成
- `ingressClassName: nginx-static` を使用
- 静的IPアドレスをLoadBalancerに割り当て（`service.beta.kubernetes.io/azure-pip-name` アノテーション）
- メトリクスは Pod アノテーションにより AMA がスクレイプ（p95/5xxでSLO監視）
- ホスト名ラベル付きメトリクスによる詳細な監視が可能

## Chaos 実験テンプレート（実装）
- 実験は step/branch/action で構成、各 action で `urn:csci:microsoft:azureKubernetesServiceChaosMesh:*Chaos/2.2` を指定
- `jsonSpec` は Chaos Mesh の spec 部分のみを JSON 化して埋め込む（Bicep で定義済、モジュール: `infra/modules/chaos/experiments.bicep`）
- 例: PodChaos（Pod unavailable、2分）
```
{
  "type": "continuous",
  "selectorId": "aks",
  "duration": "PT5M",
  "parameters": [
    {"key": "jsonSpec", "value": "{\"action\":\"pod-failure\",\"mode\":\"one\",\"selector\":{\"namespaces\":[\"chaos-lab\"],\"labelSelectors\":{\"app\":\"chaos-app\"}},\"duration\":\"300s\"}"}
  ]
}
```

### NetworkPolicy の適用確認
```
kubectl get networkpolicy -n chaos-lab
kubectl describe networkpolicy -n chaos-lab chaos-app-allow-from-ingress
kubectl get ciliumnetworkpolicy -n chaos-lab
kubectl describe ciliumnetworkpolicy -n chaos-lab chaos-app-egress-allowlist
```

注意:
- CNI 実装によっては kubelet の Readiness/Liveness Probe が NetworkPolicy の影響を受ける場合があります。問題が発生した場合は例外ルールの追加を検討してください。

### Chaos Studio 実験（Bicep 管理）
- 参照モジュール: `infra/modules/chaos/experiments.bicep`
- トグル/パラメータ（`infra/main.bicep`）
  - `enableChaosExperiments`（既定: true）
  - `chaosNamespace`（既定: `chaos-lab`）
  - `chaosAppLabel`（既定: `chaos-app`）
  - `chaosDuration`（既定: `PT5M`）
### 利用可能な実験
| 実験種類 | 障害内容 | 実験リソース名 |
|---|---|---|
| **PodChaos** | Pod障害（unavailable） | `exp-aks-pod-failure` |
| **NetworkChaos** | ネットワーク遅延 | `exp-aks-network-delay` |
| **NetworkChaos** | ネットワーク停止（ブラックホール/100% loss） | `exp-aks-network-loss` |
| **StressChaos** | CPU/メモリストレス | `exp-aks-stress` |
| **IOChaos** | ファイルI/O遅延 | `exp-aks-io` |
| **TimeChaos** | システム時刻操作 | `exp-aks-time` |
| **HTTPChaos** | HTTP通信障害 | `exp-aks-http` |
| **DNSChaos** | DNS解決障害 | `exp-aks-dns` |

注意: Chaos Mesh の既知不具合により KernelChaos は現時点では除外しています。詳細: https://github.com/chaos-mesh/chaos-mesh/issues/4059

#### 実験の開始（例: CLI）
> azd を使用した場合は Chaos Mesh が自動導入されています。手動デプロイの場合は事前に Chaos Mesh を AKS に導入してください。

```bash
# 実験一覧（Bicepで作成済み）
az resource list \
  --resource-group <RG> \
  --resource-type Microsoft.Chaos/experiments \
  --query "[].name" -o tsv

# 実験の開始（例: exp-aks-pod-failure）
az rest \
  --method post \
  --uri \
  "/subscriptions/<SUB>/resourceGroups/<RG>/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure/start?api-version=2024-01-01"

# 実験の停止（同URIで /stop）
az rest \
  --method post \
  --uri \
  "/subscriptions/<SUB>/resourceGroups/<RG>/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure/stop?api-version=2024-01-01"
```

## 📈 負荷テスト

- `src/` ディレクトリで `make` ターゲットを使って、Locust ベースの負荷を生成できます（`uv` と `kubectl` が必要）。
- `BASE_URL` 未指定時は `AZURE_INGRESS_FQDN` を優先し自動検出、未設定の場合は Ingress から自動検出します。

```bash
cd src

# smoke（軽量・クイック検証）
make load-smoke

# baseline（デフォルト）
make load-baseline

# stress / spike プロファイル
make load-stress
make load-spike

# 手動で BASE_URL 指定（他のパラメータも同様に上書き可）
BASE_URL=http://<host-or-ip> make load-baseline
USERS=100 SPAWN_RATE=10 DURATION=300 make load-baseline
```

- 推奨: 実運用に近い検証のため、負荷をかけながら Azure Chaos Studio の実験を実行してください（例: 別ターミナルで `make load-baseline` を継続しつつ、PodChaos/NetworkChaos を開始）。

注意:
- 期間管理の方針: Chaos Mesh 側の jsonSpec に `duration`（既定: `meshDuration=300s`）を含め、Azure アクションの `duration` はフォールバックとして設定します（実装の優先順位に一致）。

## Chaos Mesh 導入（azd/Helm）
- `azure.yaml` の `services.chaos-mesh` に `k8s.namespace: chaos-testing` を指定（azd が自動でNS作成）
- `k8s.helm` で以下を指定: repo=`https://charts.chaos-mesh.org`, chart=`chaos-mesh`, version=`2.7.3`, values=`infra/helm/chaos-mesh-values.yaml`
- 導入/削除: `azd deploy` / `azd down`

# デプロイ計画 - AKS Chaos Lab

## 前提
- **動作環境**: Linux (WSL) または macOS
- ツール: azd >=1.18, az >=2.75, Docker, jq, kubectl, helm
- サブスクリプション権限: Contributor以上

## AKS SKUオプション
本リポジトリは**AKS Base**モードと**AKS Automatic**モードの両方をサポートしています。パラメーターファイル（`infra/main.parameters.json`）で`aksSkuName`を変更することで選択可能です：
- **Base**: 従来のAKS（デフォルト）
- **Automatic**: より自動化された運用を提供する新しいAKSモード

## リソース構成（Bicep想定）
- RG, VNet/Subnet
- AKS (Azure CNI, OIDC, Workload Identity有効)
- ACR (Pull for AKS MI)
- Azure Managed Redis (Private Endpoint)
- Log Analytics + Application Insights (Workspace-based)
- Azure Chaos Studio: ターゲット登録（AKS/VMSSノード）、実験定義

## プロビジョニング手順（概略）
1) `azd init` -> env作成
2) `azd up` -> Bicepで上記リソースを作成
3) AKSへ接続設定（get-credentials）、Web Application Routing nginx をデプロイ
4) k8sマニフェスト適用（Deployment/Service/Ingress/ConfigMap/Secret/HPA/PDB）
5) Chaos Studio 実験定義の作成と有効化

## 変数
- スケール: レプリカ数/HPAターゲット
- テレメトリ: OTEL_TRACES_SAMPLER(_ARG)
- Redis: HOST/PORT/SSL/プール設定

## 検証
- /health 200
- / 200 + Redis操作
- Chaos: CPU/メモリ/Pod Kill 実験での動作とメトリクス/トレース確認

## ロールバック/クリーンアップ

## Container Insights（AMA + DCR）運用ノート
- 概要: 本プロジェクトは Azure Monitor agent(AMA) と Data Collection Rule(DCR) を用いた Container Insights を採用します。AKS 側の `azureMonitorProfile.containerInsights.enabled` はエージェント有効化のスイッチであり、実際の収集/送信先は DCR/DCRA で定義します。

- Portal Insights と V1/V2 の関係（重要）:
  - 現時点で、AKS の Portal「Insights」画面の一部カード（例: Logs and events の一部）は ContainerLog(V1) の有無を前提に表示判定している挙動が確認されています。公式ドキュメントで「V1 が必要」と明記された一次情報は見当たりませんが、運用上の回避として V1 を併用すると該当カードの「Enable logs」表示が解消されます。
  - 本プロジェクトでは一時的な回避策として `Microsoft-ContainerLog`(V1) と `Microsoft-ContainerLogV2` の両方を収集しています。将来的に Portal 側の挙動が更新されたら V1 を停止してください。

- DCR/DCRA の確認コマンド:
  - DCRA 一覧: `az monitor data-collection rule association list --scope <AKS リソースID>`
  - DCR 一覧: `az monitor data-collection rule list -g <ResourceGroup>`

- 取り込み確認（KQL 例）:
  - `ContainerLogV2 | take 10`
  - `ContainerLog | take 10`
  - `KubePodInventory | summarize count() by ClusterName | take 10`
  - `InsightsMetrics | summarize count()`

- コスト最適化のヒント:
  - 名前空間フィルタ（DCR の `namespaceFilteringMode` と `namespacesForDataCollection`）で対象を絞り込む。
  - V2 への移行が十分進んだら V1（`Microsoft-ContainerLog`）を停止する。
- `azd down --force --purge`
- RG削除確認

## 統合テストパイプライン

GitHub Actionsを使用した手動トリガー型の統合テストパイプラインを提供しています。

### 概要

`.github/workflows/integration-test.yml` は、一時的なAzure環境を自動的にプロビジョニングし、アプリケーションをデプロイ、統合テストを実行後にクリーンアップするパイプラインです。

### 特徴

- **手動トリガー（workflow_dispatch）**: コストと時間を考慮し、PRごとの自動実行ではなく必要なタイミングで手動実行
- **OIDC認証**: GitHub ActionsからAzureへのセキュアな認証（シークレット不要）
- **GitHub Environment**: `integration-test` 環境を使用し、どのブランチからでも実行可能
- **環境分離**: `inttest-{run_id}` 命名規則で一時環境を作成
- **自動クリーンアップ**: テスト成功・失敗に関わらずリソースを削除

### セットアップ方法

#### 1. Azure リソースの作成

**User Managed Identity の作成**

```bash
# リソースグループの作成
az group create --name rg-aks-chaos-lab-msi --location japaneast

# User Managed Identity の作成
az identity create \
  --name msi-aks-chaos-lab \
  --resource-group rg-aks-chaos-lab-msi \
  --location japaneast

# Client ID を取得（後で GitHub に設定）
az identity show \
  --name msi-aks-chaos-lab \
  --resource-group rg-aks-chaos-lab-msi \
  --query clientId -o tsv
```

**サブスクリプションへの Contributor ロール割り当て**

```bash
# MSI の Principal ID を取得
MSI_PRINCIPAL_ID=$(az identity show \
  --name msi-aks-chaos-lab \
  --resource-group rg-aks-chaos-lab-msi \
  --query principalId -o tsv)

# Contributor ロールを割り当て（Azure リソースの作成・管理用）
az role assignment create \
  --assignee-object-id $MSI_PRINCIPAL_ID \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope /subscriptions/$(az account show --query id -o tsv)

# AKS RBAC Cluster Admin ロールを割り当て（Kubernetes API 操作用）
az role assignment create \
  --assignee-object-id $MSI_PRINCIPAL_ID \
  --assignee-principal-type ServicePrincipal \
  --role "Azure Kubernetes Service RBAC Cluster Admin" \
  --scope /subscriptions/$(az account show --query id -o tsv)
```

**Federated Identity Credential の作成（Environment ベース）**

```bash
# GitHub Environment 用の Federated Credential を作成
az identity federated-credential create \
  --name "github-env-integration-test" \
  --identity-name msi-aks-chaos-lab \
  --resource-group rg-aks-chaos-lab-msi \
  --issuer "https://token.actions.githubusercontent.com" \
  --subject "repo:torumakabe/aks-chaos-lab:environment:integration-test" \
  --audiences "api://AzureADTokenExchange"
```

> **Note**: Environment ベースの Federated Credential を使用することで、どのブランチからでもワークフローを実行できます。

#### 2. GitHub Environment と Secrets の作成

GitHub CLI を使用して Environment と Secrets を作成します：

```bash
# Environment の作成
gh api repos/<owner>/<repo>/environments/integration-test -X PUT --silent

# Secrets の値を取得
CLIENT_ID=$(az identity show --name msi-aks-chaos-lab --resource-group rg-aks-chaos-lab-msi --query clientId -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Environment Secrets の設定
gh secret set AZURE_CLIENT_ID --env integration-test --body "$CLIENT_ID"
gh secret set AZURE_TENANT_ID --env integration-test --body "$TENANT_ID"
gh secret set AZURE_SUBSCRIPTION_ID --env integration-test --body "$SUBSCRIPTION_ID"

# 設定確認
gh secret list --env integration-test
```

> **Note**: Variables ではなく **Secrets** として設定することで、ログへの露出を防ぎます。

### 使用方法

1. GitHub リポジトリの **Actions** タブを開く
2. **Platform Integration Test** ワークフローを選択
3. **Run workflow** をクリック
4. パラメータを設定:
   - **Branch**: テスト対象ブランチ（空欄の場合はトリガーしたブランチを使用）
   - **Test scope**: `full` / `infra-only` / `app-only`
   - **AKS SKU**: `Base` / `Automatic`（デフォルト: `Base`）
5. **Run workflow** で実行開始

> **Note**: `--ref` オプションでブランチを指定して実行した場合、Branch 入力を空欄にすることでそのブランチのコードがテストされます。

### ジョブ構成

| ジョブ | 説明 | タイムアウト |
|-------|------|-------------|
| validate | Bicepテンプレートの検証 | 15分 |
| provision-and-deploy | azd provision + deploy で環境構築とアプリデプロイ | 35分 |
| test | 統合テスト実行（Smoke test + pytest） | 10分 |
| cleanup | リソースグループ削除 | 15分 |

### 実行時間の目安

- **全体所要時間**: 約25-30分
- **同時実行制限**: 1（後続はキューイング）

### トラブルシューティング

- **プロビジョニング失敗**: Azure クォータ制限、リージョンの容量不足を確認
- **デプロイ失敗**: ACRへのプッシュ権限、AKSへのデプロイ権限を確認
- **Kubernetes API エラー（namespaces forbidden）**: MSI に `Azure Kubernetes Service RBAC Cluster Admin` ロールが割り当てられているか確認
- **Smoke test 失敗（HTTP 000）**: アプリケーションの起動に時間がかかっている可能性。リトライロジックが含まれているため、自動的に再試行されます
- **SSL証明書エラー**: 自己署名証明書を使用しているため、統合テストでは `verify=False` が必要（テストコードに設定済み）
- **クリーンアップ失敗**: 手動でリソースグループ `rg-aks-chaos-lab-inttest-{run_id}` を削除
