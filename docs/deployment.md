# デプロイメント - AKS Chaos Lab

本書はAKSへのデプロイ手順と構成方針を示す。

## 前提条件
- [Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/) **推奨**
- Azure CLI + Bicep extension
- kubectl
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
- **AKS**: Kubernetes 1.33, Azure CNI Overlay + Cilium, Workload Identity, Auto-upgrade: patch
  - **SKU**: Standard（Uptime SLA 有効）
  - **Cluster Autoscaler**: 有効（最小1ノード、最大3ノード）
  - **Cost Analysis**: AKS コスト分析アドオンを有効化
  - **Availability Zones**: 1 / 2 / 3（リージョン対応時）
- **Advanced Container Networking**: L7ネットワークポリシー + 可観測性
- **Container Insights**: Log Analytics統合による統合監視（Addon: omsagent, enableContainerLogV2）
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

注意（権限エラーのまれな発生と対処）:
- まれに RBAC 伝播の遅延や環境の呼び出し主体差異により、`...subnets/join/action` のエラーが出る場合があります。
- 本テンプレートは AKS の UAMI に対してサブネットの Network Contributor を自動付与しています。数分待ってから `azd provision` を再実行してください。
- それでも解消しない場合は、エラーメッセージに表示された client id（呼び出し主体）に対して、当該サブネットに Network Contributor を一時的に付与して再実行してください。

### 方法2: Bicep直接デプロイ（サブスクリプション スコープ）

```bash
# 1. サブスクリプション スコープでデプロイ（RGはテンプレートが作成します）
az deployment sub create \
  --location japaneast \
  --template-file infra/main.bicep \
  --parameters location=japaneast

# 2. AKS認証情報取得（作成された RG/AKS 名は既定だと以下の通り）
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
- **AKS**: Kubernetes 1.33, Advanced Networking, Container Insights有効, Auto-upgrade=patch（x.y 指定で最新パッチに追随）, SKU=Standard（Uptime SLA）, Availability Zones=1/2/3
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
  "duration": "PT2M",
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
  - `chaosDuration`（既定: `PT2M`）
- 対応実験: PodChaos / NetworkChaos（遅延/停止）/ StressChaos / IOChaos / TimeChaos（それぞれ実験リソース）
- 作成される実験リソース名:
  - `exp-aks-pod-failure`
  - `exp-aks-network-delay`
  - `exp-aks-network-loss`
  - `exp-aks-stress`
  - `exp-aks-io`
  - `exp-aks-time`
  - `exp-aks-http`
  - `exp-aks-dns`

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

注意:
- 期間管理の方針: Chaos Mesh 側の jsonSpec に `duration`（既定: `meshDuration=300s`）を含め、Azure アクションの `duration` はフォールバックとして設定します（実装の優先順位に一致）。

## Chaos Mesh 導入（azd/Helm）
- `azure.yaml` の `services.chaos-mesh` に `k8s.namespace: chaos-testing` を指定（azd が自動でNS作成）
- `k8s.helm` で以下を指定: repo=`https://charts.chaos-mesh.org`, chart=`chaos-mesh`, version=`2.7.3`, values=`infra/helm/chaos-mesh-values.yaml`
- 導入/削除: `azd deploy` / `azd down`

# デプロイ計画 - AKS Chaos Lab

## 前提
- ツール: azd >=1.18, az >=2.75, Docker, jq, kubectl, helm
- サブスクリプション権限: Contributor以上

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
- `azd down --force --purge`
- RG削除確認
