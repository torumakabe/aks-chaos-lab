# 環境構築と運用手順

このドキュメントは、AKS Chaos Lab を構築・検証・削除するための手順をまとめます。設計判断の背景は [ADR 一覧](adr/INDEX.md)、既知のワークアラウンドと解消条件は [workarounds.md](workarounds.md) を参照してください。

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
- `eval` 環境で SLI 作成が成功した実績のある最小構成は、環境別 Service Group に対する **Service Group Administrator** の直接付与

Service Group RBAC の制約は [docs/workarounds.md §A-5](workarounds.md#a-5-環境別-service-group-への-service-group-administrator-直付与が必要) で棚卸ししています。

### 3. SLI 用 User Assigned Managed Identity に自動付与するロール

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

1. `azd provision base` (`infra/main.bicep`) — VNet / AKS / Inspektor Gadget 拡張 / Redis / Application Insights / Managed Prometheus / external SLI publisher infra / Service Group / SLI 用 Managed Identity / RBAC を作成
2. `azd deploy api-instrumentation` — chaos-app 固有の Application Insights OTLP `Instrumentation` を先に適用し、AKS App Monitoring webhook が参照できる状態にする
3. `azd deploy api` — chaos-app をデプロイし、`postdeploy` hook で Pod に `OTEL_EXPORTER_OTLP_*` が注入されたことを確認
4. `azd deploy observability` — Envoy Gateway などをデプロイ
5. `azd deploy chaos-mesh` — Chaos Mesh を Helm install
6. `azd deploy external-sli-publisher` — Flex Consumption の Azure Functions publisher をデプロイ
7. `azd provision sli` (`infra/sli/main.bicep`) — layer `preprovision` hook で external SLI input metrics の出現を待ってから Azure Monitor SLI definitions と SLI metric alerts を作成

SLI layer は external SLI publisher が Managed Prometheus に good / total metrics を出した後に実行する必要があるため、`infra.layers` で `base` と `sli` を分離しています。この判断は [ADR-012](adr/012-functions-direct-external-sli-probe.md) を参照してください。

Azure Monitor SLI destination metrics は、SLI resource 作成後に評価が始まるまで時間がかかることがあります。`azd up` は destination metric の出現を待ちません。評価開始を手動で確認する場合は、デプロイ後に次のコマンドを実行します。

```bash
uv run scripts/wait-for-external-sli-signals.py --skip-source --require-sli-destination
```

`api-instrumentation` は app-specific な `Instrumentation/chaos-app-otel` だけを `k8s/apps/chaos-app/instrumentation/` から適用します。クラスタ共通の `k8s/observability` には置きません。`Instrumentation` を `Deployment/chaos-app` より先に作成しないと、AKS App Monitoring の admission webhook が Pod template に `OTEL_EXPORTER_OTLP_*` を注入できず、API の Application Insights traces / metrics / logs と Redis dependency が欠落します。

External SLI publisher の Function host storage と deployment storage は managed identity 接続です。Storage account key / connection string に依存しないため、`allowSharedKeyAccess=false` の環境でも `azd deploy external-sli-publisher` を使います。publisher storage は `publicNetworkAccess=Disabled` とし、Function App を `snet-func` に VNet integration して blob / queue / table の Private Endpoint 経由で接続します。公式 azd sample と同じく、デプロイ実行 principal には deployment package upload 用の Storage Blob Data Owner を付与します。デプロイ実行環境は storage Private Endpoint を名前解決・到達できる必要があります。デプロイ失敗時に publisher storage の public access を一時的に開ける運用は行いません。

差分確認:

```bash
azd provision base --preview

# external SLI metrics 出現後、SLI finalize の差分を見る場合
azd provision sli --preview
```

このプロジェクトは `azure.yaml` の `requiredVersions` で、今回 `eval` 環境を検証した azd 1.28.1 以上を要求します。古い azd ではプロジェクトを実行せず、azd を更新してください。

リージョンや AKS node VM size を変更した直後に既存の azd 環境を再利用する場合、`azd env refresh` は過去の Azure deployment outputs から旧値を取り込むことがあります。`azd env refresh` の後、`azd down` や `azd up` の前に対象環境の値を明示してください。

```bash
azd env refresh -e eval --no-prompt
azd env set AZURE_LOCATION japaneast -e eval
azd env set AZURE_AKS_NODE_VM_SIZE Standard_D4pds_v6 -e eval
```

### Node Auto Provisioning

Node Auto Provisioning（NAP）は既定で無効です。設計判断と採用条件は[ADR-018](adr/018-adopt-aks-node-auto-provisioning-for-arm64-capacity.md)を参照してください。

NAPを有効にする場合は、対象環境へ明示的に設定してからbase layerの差分を確認します。NAP有効時はSystem AgentPoolがArm64 2台固定となり、Cluster Autoscalerは無効になります。

```bash
azd env refresh -e eval --no-prompt
azd env set AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING true -e eval
azd provision base --preview -e eval
```

既存環境のpreviewにAKS以外の意図しない変更、またはSystem AgentPoolの削除や置換が含まれる場合は、base layerを適用しません。Azure PolicyがsubnetへBicep管理外のNSGを関連付ける環境では、previewにNSG関連付けの削除が表示されます。対象NSGがポリシー管理であり、subnetの名前、address prefix、delegationに変更がないことを確認した場合は、期待されたdriftとして扱います。

既存AKSだけを移行するときは、System AgentPoolのCluster Autoscalerを無効化してから、AKSのnode provisioning profileだけを更新します。

```bash
RESOURCE_GROUP="$(azd env get-value AZURE_RESOURCE_GROUP -e eval)"
AKS_NAME="$(azd env get-value AZURE_AKS_CLUSTER_NAME -e eval)"

az aks nodepool update \
  --resource-group "$RESOURCE_GROUP" \
  --cluster-name "$AKS_NAME" \
  --name default \
  --disable-cluster-autoscaler

az aks update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --node-provisioning-mode Auto \
  --node-provisioning-default-pools None
```

System AgentPoolが2台Readyで、既存workloadが健全であることを確認します。NAP resourceは既定の`azd up` workflowへ含めていません。AKS側にNAPのCRDが作成されたことを確認してから、User workload用のAKSNodeClassとNodePoolを明示的に適用します。

```bash
kubectl wait \
  --for=condition=Established \
  crd/nodepools.karpenter.sh \
  crd/aksnodeclasses.karpenter.azure.com \
  --timeout=10m

azd deploy node-provisioning -e eval
```

無効化するときは、次の順序で操作します。System AgentPoolは全工程で2台を維持します。

1. NAP capacityを必要とするUser workloadを停止する。
2. User NodeClaimが0台になったことを確認する。
3. `kubectl delete -k k8s/node-provisioning`でNodePoolとAKSNodeClassを削除する。
4. `az aks update --node-provisioning-mode Manual`でNAPを無効化する。
5. `az aks nodepool update --enable-cluster-autoscaler --min-count 1 --max-count 3`でSystem AgentPoolのCluster Autoscalerを復元する。
6. System AgentPool、既存workload、外部health endpointを確認する。
7. `AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING`を`false`へ戻す。
8. `azd provision base --preview -e eval`を実行し、NAPに関する差分が解消したことを確認する。ポリシー管理のNSG関連付けは期待されたdriftとして残る場合がある。

## ローカル開発

リポジトリはuv workspace構成です。hostはルート`pyproject.toml`の互換範囲に従います。GitHub Actionsとlock更新workflowはsetup-uvで互換範囲の下限を選択します。Dockerのuv versionは`azd package api`との互換性のためDockerfileに明記し、`check-uv-version`がルートの下限との一致を検査します。ルートで一度同期すれば、`src/api`と`src/external-sli-publisher`の両方の依存と開発ツール（ruff、ty、pytest、locust）が揃います。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" check-uv-version
uv run --no-project "${PWD}/scripts/tasks.py" sync-dev
lefthook install
uv run --no-project "${PWD}/scripts/tasks.py" run
```

`lefthook install` は、コントリビュータ向けにこのリポジトリの Git hooks を登録します。ステージされた `infra/` 配下の Bicep ファイルがある場合、pre-commit は `infra/main.bicep` をビルドし、失敗したコミットを中止します。Lefthook のインストール方法は [公式手順](https://lefthook.dev/install/) を参照してください。

### 組織承認済み package index を使う環境

public package registry へ直接接続できない環境では、user-level の uv 設定に組織承認済みの Python package index を一つ構成します。`[[index]]`には`default = true`を指定し、public PyPIへのfallbackを無効にします。index URLや認証情報はリポジトリへ保存しません。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" qa-app
```

task runnerは有効なapproved-index設定を検出すると、各task processの最初のworkspaceコマンドを実行する前に`.venv`を再構築します。同期開始からtask processの終了までは、対象venvの正規化pathから導出したprocess間lockをOSの一時領域で保持するため、同じvenvを使うworkspace taskは直列に実行されます。明示的に環境だけを準備する場合は`sync-dev-approved-index`を実行します。

同期処理はuser-level設定とpublic lockのsourceを検査してから、public `uv.lock`を一時requirementsへ変換し、構成済みのpackage indexから`.venv`を作成します。変換後のrequirementsも検査し、direct URL、find-links、hash検証やTLS検証を無効にする設定、projectやdependency groupを変更する環境変数を拒否します。exportと後続の`uv run`はroot projectと対象venvの絶対pathへ固定します。registry packageには`--require-hashes`を適用し、workspace sourceはindexを介さず`.pth`で参照します。通常環境と分離した`.uv-state/cache/`を使い、一時requirementsは処理後に削除します。index固有のusernameとpassword環境変数は同期processだけへ渡し、ruff、ty、pytest、アプリなどの後続processから除去します。同じtask process内の後続コマンドだけは、直前に構築した環境へ`--no-sync`を適用します。

post-edit hookは依存関係の整合性を判定しません。Python編集時はprojectの`.venv`にある`ruff`を直接実行し、ruffがない場合は同期を要求します。lockと仮想環境の整合性は同期taskとCIで検証します。

`uv.lock`が同期中に変わった場合や同期が中断した場合、taskは対象コマンドを実行しません。同じコマンドを再実行してください。通常の同期へ戻す場合はuser-levelのapproved-index設定を無効にしてから`sync-dev`を実行します。

task runnerを介さない `uv run` では、projectの自動同期を明示的に止めます。

```bash
UV_NO_SYNC=1 uv run <command>
```

PowerShellでは `$env:UV_NO_SYNC = "1"` を設定します。

### Docker build のpackage index

通常のDocker buildはpublic PyPIを使用します。

```bash
docker build -f src/api/Dockerfile -t aks-chaos-lab:local .
```

組織承認済みpackage indexが必要な環境では、専用taskでAPI imageをbuildし、そのimageを`azd deploy --from-package`へ渡します。taskはuser-level `uv.toml`を検証し、BuildKit secretとしてbuildへ渡します。

```bash
azd env select <environment>
uv run --no-project "${PWD}/scripts/tasks.py" package-api-approved-index
azd deploy api-instrumentation --no-prompt
uv run --no-project "${PWD}/scripts/tasks.py" deploy-api-approved-index
```

通常の`azd package api`と`azd deploy api`はpublic PyPIを使用します。認証が必要なindexは専用taskの対象外です。`uv.lock`はpublic PyPIのURLを維持します。

### public lockfile の更新

dependencyを変更する場合、public PyPIへ接続できるGitHub Actionsでlockfileを生成します。workflowはrepositoryへのwrite permissionを持たず、`uv.lock` をartifactとして返します。

```bash
gh run list --workflow refresh-uv-lock.yml --branch <branch>
gh run download <run-id> --name uv-lock-public --dir tmp/refresh-uv-lock
```

workflowは`pyproject.toml`、`uv.lock`、workflow定義自身を変更したpull requestで実行されます。既定branchへmergeした後は`workflow_dispatch`でも実行できます。取得した`uv.lock`の差分を確認して変更branchへ追加すると、組織承認済みpackage indexを使う環境では次のworkspace task実行時に再同期します。package indexがpublic lockと同一hashのartifactを提供できない場合、同期は失敗します。

Dependabotのuv ecosystemは現時点では有効にしません。Dependabotがworkspace member、`resolution-strategy = "lowest"`、public PyPIを参照する`uv.lock`、external SLI publisherのrequirements同期を一度の更新で維持することを、設定のmerge前に保証できないためです。代表的なDependabot更新でこれらを確認でき、`check-uv-version`、`check-public-lock`、`check-publisher-requirements`、既存QAがすべて成功する検証経路を用意できた時点で採用を再評価します。それまでは`refresh-uv-lock.yml`を更新経路とします。

`.github/dependabot.yml`の構成、グループ、説明コメントは開発者が管理します。`gh aw compile`が管理する範囲は、GitHub Actions ecosystemの`github/gh-aw-actions`に対する完全一致のignore entryと、その行の管理コメントだけです。ワイルドカードの`github/gh-aw-actions/*`、生成済みlock workflowの除外、Docker imageのignoreはリポジトリ側で管理します。

## テストと品質確認

アプリケーション:

クリーン環境や新しいworktreeでは、通常環境で`uv run --no-project "${PWD}/scripts/tasks.py" sync-dev`を実行してください。組織承認済みpackage indexを使う環境では、workspaceコマンドを含むtaskが実行前に環境を同期します。絶対pathと`--no-project`は、task runnerの起動前にuvが別のprojectを探索または同期することを防ぎます。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" test
uv run --no-project "${PWD}/scripts/tasks.py" test-cov
uv run --no-project "${PWD}/scripts/tasks.py" lint
uv run --no-project "${PWD}/scripts/tasks.py" typecheck
uv run --no-project "${PWD}/scripts/tasks.py" qa-app
```

Bicep:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" build-bicep
```

Git hooks:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" test-hooks
```

リポジトリ全体:

```bash
uv run --no-project "${PWD}/scripts/tasks.py" qa
```

`uv run --no-project "${PWD}/scripts/tasks.py" qa`はworkflows、Bicep、Kubernetes manifests、アプリ、リポジトリ用Python scriptsのQAをまとめて実行します。必要な外部ツールの確認は`uv run --no-project "${PWD}/scripts/tasks.py" install-tools`と`check-*`ターゲットで実行できます。

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

## 既存環境の SLI 信号移行 cleanup

古い構成から移行する環境では、AKS 内 synthetic traffic、legacy Prometheus alert group、旧 SLI resources がテンプレート削除だけでは残る場合があります。移行対象は dry-run で確認できます。

```bash
uv run scripts/cleanup-legacy-sli-sources.py
```

削除する場合は `--execute` を付けます。Request-based SLI として再作成が必要な既存 SLI resources も削除する場合だけ `--delete-sli-resources` を追加してください。

```bash
uv run scripts/cleanup-legacy-sli-sources.py --execute
uv run scripts/cleanup-legacy-sli-sources.py --execute --delete-sli-resources
```

## 環境削除

Azure Monitor SLI を有効化した環境では、Service Group scope の `Microsoft.Monitor/slis` と環境別 Service Group が resource group の外に存在します。削除時は cleanup hook を有効にして project-level `azd down` を実行してください。

```bash
azd down --force --purge
```

`predown` hook は Service Group scope の SLI / Service Group / AKS の OTLP Application Insights DCR association / deployment record / base resource group を整理します。AKS 上の OTLP DCRA を先に削除することで、App Insights managed resource group (`ai_<appi-name>_<guid>_managed`) は base RG の削除に連動して消えます。詳細な順序と理由は [ADR-009](adr/009-azure-monitor-sli-and-prometheus-slo.md)、[docs/workarounds.md §A-4](workarounds.md#a-4-predown-hook-で-service-group-scope-sli-と環境別-service-group-を削除)、[§B-2](workarounds.md#b-2-predown-hook-で-aks-上の-otlpappinsightsextension-dcra-を先に削除) を参照してください。

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
