# 環境構築と運用手順

このドキュメントは、AKS Chaos Lab を構築・検証・削除するための手順をまとめます。設計判断の背景は [ADR 一覧](adr/INDEX.md)、既知のワークアラウンドと解消条件は [workarounds.md](workarounds.md) を参照してください。

## 前提ツール

- Linux (WSL) または macOS
- [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
- Azure CLI + Bicep extension
- `kubectl`
- Python 3.13+ + [`uv`](https://github.com/astral-sh/uv)
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
- `eval` 環境で SLI 作成が成功した実績のある最小構成は、環境別 Service Group に対する **Service Group Administrator** の直接付与

Service Group RBAC の制約は [docs/workarounds.md §A-5](workarounds.md#a-5-環境別-service-group-への-service-group-administrator-直付与が必要) で棚卸ししています。

### 3. SLI 用 User Assigned Managed Identity に自動付与するロール

`infra/modules/azmonitor/sli-rbac.bicep` と `infra/modules/azmonitor/sli-managed-dcr-rbac.bicep` が、SLI 用 UAMI に以下を自動付与します。デプロイ主体にサブスクリプション スコープ権限があれば、追加操作は不要です。

- Azure Monitor Workspace: Monitoring Reader / Monitoring Data Reader / Monitoring Metrics Publisher
- Prometheus pipeline DCR: Monitoring Reader / Monitoring Metrics Publisher
- AMW managed resource group `MA_<amw-name>_<region>_managed` 内の同名 DCR: Monitoring Metrics Publisher

managed DCR で `Monitoring Metrics Publisher` が不足すると、SLI の storage location validation が失敗します。

### 4. AKS の Microsoft Entra 統合

AKS は `aadProfile.managed=true` + `enableAzureRbac=true` + `disableLocalAccounts=true` のため、`kubectl` は Microsoft Entra ID 経由になります。Bicep が deployment 実行 identity に **Azure Kubernetes Service RBAC Cluster Admin** を付与するため、追加のテナント権限は不要です。

## プレビュー機能とリソースプロバイダー登録

`azd up` 前に、サブスクリプション単位で以下のプレビュー機能とリソースプロバイダーを登録します。登録には Owner / Contributor 相当の権限が必要です。

```bash
# AKS のアドオン VPA
az feature register --namespace Microsoft.ContainerService --name AKS-AddonAutoscalingPreview

# OTLP 経由の Application Insights / Azure Monitor managed Prometheus 連携 (ADR-006)
az feature register --namespace Microsoft.ContainerService --name AzureMonitorAppMonitoringPreview

# 反映後に provider を再登録
az provider register --namespace Microsoft.ContainerService
```

`az feature show --namespace <ns> --name <name>` で `state: Registered` になってから `azd up` を実行してください。これらの feature flag は [review-repo エージェント](../.github/agents/review-repo.agent.md) の棚卸し対象です。

## 環境構築

本リポジトリは **AKS Base** モード前提で動作します。`infra/main.parameters.json` の `aksSkuName` と環境変数 `AKS_SKU_NAME` は `Base` のみ受け付けます。理由は [ADR-010](adr/010-aks-automatic-unsupported-due-to-deployment-safeguards.md) を参照してください。

Azure Kubernetes Fleet Manager が更新管理を担います。

- Fleet フリート / メンバー / 更新戦略 / 自動アップグレード プロファイルを `infra/modules/fleet.bicep` で自動作成
- 更新戦略は `beforeGates` に Approval ゲートを含み、手動承認まで Update Run を開始しない
- Control plane 用と NodeImage 用の autoUpgradeProfile が同じ承認ゲートを共有
- Azure Monitor Scheduled Query Rule `fleet-approval-pending` が、Approval Gate が Pending の間アクション グループに通知

Approval Gate の承認例:

```bash
az extension add --name fleet
az fleet gate list \
  --resource-group rg-aks-chaos-lab-dev \
  --fleet-name fleet-aks-chaos-lab-dev \
  --state Pending
az fleet gate approve \
  --resource-group rg-aks-chaos-lab-dev \
  --fleet-name fleet-aks-chaos-lab-dev \
  --gate-name <gate-name>
```

リソース名は `appName` と `environment` に応じて読み替えてください。

### `azd up`

```bash
azd config set alpha.aks.kustomize on
azd config set alpha.aks.helm on
azd init
azd up
```

`azd up` は `azure.yaml` の `workflows.up` に従い、以下の順で実行されます。

1. `azd provision base` (`infra/main.bicep`) — VNet / AKS / Redis / Application Insights / Managed Prometheus / Service Group / SLI 用 Managed Identity / RBAC を作成
2. `azd deploy api` — chaos-app をデプロイ
3. `azd deploy observability` — Envoy Gateway などをデプロイし、`postdeploy` hook で `scripts/warm-up-sli-signals.sh` がトラフィック生成と Managed Prometheus recording rules の `cluster_name` 出現を待機
4. `azd deploy chaos-mesh` — Chaos Mesh を Helm install
5. `azd provision sli` (`infra/sli/main.bicep`) — Azure Monitor SLI definitions と SLI metric alerts を作成

SLI layer はアプリ deploy、observability deploy、traffic warm-up 後に実行する必要があるため、`infra.layers` で `base` と `sli` を分離しています。この判断は [ADR-009](adr/009-azure-monitor-sli-and-prometheus-slo.md) を参照してください。

差分確認:

```bash
azd provision base --preview

# warm-up / アプリ deploy 後、SLI finalize の差分を見る場合
azd provision sli --preview
```

`azd up` 中に `DeploymentNotFound` が出ても、ARM 上の deployment が `Succeeded` で残るケースがあります。その場合は `azd up` を再実行してください。詳細は [docs/workarounds.md §D-2](workarounds.md#d-2-azd-の-subscription-scope-deployment-polling-が散発的に-deploymentnotfound-を返す) を参照してください。

## ローカル開発

```bash
cd src
uv sync --group dev
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## テストと品質確認

アプリケーション:

```bash
cd src
make test
make test-cov
make lint
make typecheck
make qa
```

リポジトリ全体:

```bash
make qa
```

`make qa` は workflows、Bicep、Kubernetes manifests、アプリの QA をまとめて実行します。必要な外部ツールはルート `Makefile` の `install-tools` / `check-*` ターゲットを参照してください。

## 負荷テスト

`src/` ディレクトリで Locust ベースの負荷を生成できます。`BASE_URL` 未指定時は `AZURE_INGRESS_FQDN` を優先し、未設定の場合は Gateway から自動検出します。

```bash
cd src

make load-smoke
make load-baseline
make load-stress
make load-spike

BASE_URL=http://<host-or-ip> make load-baseline
USERS=100 SPAWN_RATE=10 DURATION=300 make load-baseline
```

Chaos 実験の観察時は、別ターミナルで `make load-baseline` を継続しながら [docs/chaos-experiments.md](chaos-experiments.md) の実験を開始すると挙動を追いやすくなります。

## 環境削除

Azure Monitor SLI を有効化した環境では、Service Group scope の `Microsoft.Monitor/slis` と環境別 Service Group が resource group の外に存在します。削除時は cleanup hook を有効にして project-level `azd down` を実行してください。

```bash
CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES=true azd down --force --purge
```

`predown` hook は Service Group scope の SLI / Service Group / AKS の OTLP Application Insights DCR association / deployment record / base resource group を整理します。AKS 上の OTLP DCRA を先に削除することで、App Insights managed resource group (`ai_<appi-name>_<guid>_managed`) は base RG の削除に連動して消えます。詳細な順序と理由は [ADR-009](adr/009-azure-monitor-sli-and-prometheus-slo.md) と [docs/workarounds.md §D-2](workarounds.md#d-2-azd-の-subscription-scope-deployment-polling-が散発的に-deploymentnotfound-を返す) を参照してください。

注意点:

- cleanup hook は system-protected deny assignment を解除しません
- `azd down <layer>` のような layer 指定 down はサポート対象外です
- `CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES=true` を付けない通常の `azd down` では cleanup hook は skip されます
