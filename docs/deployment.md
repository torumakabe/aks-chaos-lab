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

`api-instrumentation` は app-specific な `Instrumentation/chaos-app-otel` だけを `k8s/apps/chaos-app/instrumentation/` から適用します。クラスタ共通の `k8s/observability` には置きません。`Instrumentation` を `Deployment/chaos-app` より先に作成しないと、AKS App Monitoring の admission webhook が Pod template に `OTEL_EXPORTER_OTLP_*` を注入できず、API の Application Insights traces / metrics / logs と Redis dependency が欠落します。

External SLI publisher の Function host storage と deployment storage は managed identity 接続です。Storage account key / connection string に依存しないため、`allowSharedKeyAccess=false` の環境でも `azd deploy external-sli-publisher` を使います。publisher storage は `publicNetworkAccess=Disabled` とし、Function App を `snet-func` に VNet integration して blob / queue / table の Private Endpoint 経由で接続します。公式 azd sample と同じく、デプロイ実行 principal には Storage Blob Data Owner を付与します。

azd 1.33.0 は ZIP を Function App の SCM エンドポイント `/api/publish` へ送信します（[実装](https://github.com/Azure/azure-dev/blob/29133b640536436db9b56f8db4b1781cb136e5ba/cli/azd/pkg/azsdk/funcapp_host_client.go)）。デプロイ実行元に必要なのは SCM への接続で、storage の Private Endpoint へ直接アップロードする構成ではありません。storage への接続は Functions 側で行います。デプロイ失敗時に publisher storage の public access を一時的に開ける運用は行いません。

差分確認:

```bash
azd provision base --preview

# external SLI metrics 出現後、SLI finalize の差分を見る場合
azd provision sli --preview
```

このプロジェクトは `azure.yaml` の `requiredVersions` で azd 1.33.0 以上を要求します。古い azd ではプロジェクトを実行せず、azd を更新してください。

環境設定の復元などで `azd env refresh` を使うと、過去のデプロイ出力から値が取り込まれます。リージョンや VM サイズを変更する場合は、refresh 後に設定してください。

### Local DNS

`AZURE_AKS_LOCAL_DNS_MODE` の既定は `Required`（有効）です。System pool と NAP に `azd up` で適用します。未設定の既存環境も有効化対象です。無効化する場合は、事前に `azd env set AZURE_AKS_LOCAL_DNS_MODE Disabled -e <environment>` を実行してください。再有効化は `Required` を指定します。

対応する Kubernetes、OS、VM サイズは [Microsoft Learn](https://learn.microsoft.com/azure/aks/localdns-custom)、採用判断は [ADR-019](adr/019-adopt-aks-local-dns.md)を参照してください。

既存環境で初めて有効化する際は、ノード更新前に `kubectl apply -f k8s/apps/chaos-app/ciliumnetworkpolicy-egress-allowlist.yaml` で DNS 許可を反映してください。有効化と無効化ではノードの再イメージ化による停止を想定し、他の Chaos 実験を停止して実施します。PDB があっても無停止は保証されません。

`<environment>` は対象の azd 環境名に置き換えます。プレビューに意図しない差分がある場合は、続く `azd up` を実行しません。

```bash
azd provision base --preview -e <environment>
azd up -e <environment>
```

適用後は新規 Pod の名前解決と[ノード別メトリクス](observability.md#local-dns)を確認してください。API が返す Local DNS の状態が `state: Enabled` でも既存ノードに反映されない場合だけ、[D-14](workarounds.md#d-14-local-dns-の-api-更新が既存ノードへ反映されない場合)の追加対応を使います。DNS 障害注入の範囲は [Chaos 実験ガイド](chaos-experiments.md#local-dns-の比較検証)を参照してください。

### Node Auto Provisioning

Node Auto Provisioning（NAP）は既定で有効です。`AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING` が未設定の環境も有効化対象です。設計判断と採用条件は[ADR-018](adr/018-adopt-aks-node-auto-provisioning-for-arm64-capacity.md)、既定有効化の判断は[ADR-020](adr/020-enable-node-auto-provisioning-by-default.md)を参照してください。

NAP有効時はSystem AgentPoolがArm64 2台固定となり、Cluster Autoscalerは無効になります。未設定の既存環境でも次回のbase layer適用からこの構成が要求されるため、適用前に差分を確認してください。NAPを使わない新規環境や、NAP無効の既存環境を維持する場合は、適用前に `azd env set AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING false -e "<env>"` を実行してください。明示的な `false` は引き続き無効として扱います。

```bash
azd provision base --preview -e "<env>"
```

既存環境のpreviewにAKS以外の意図しない変更、またはSystem AgentPoolの削除や置換が含まれる場合は、base layerを適用しません。Azure PolicyがsubnetへBicep管理外のNSGを関連付ける環境では、previewにNSG関連付けの削除が表示されます。対象NSGがポリシー管理であり、subnetの名前、address prefix、delegationに変更がないことを確認した場合は、期待されたdriftとして扱います。

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
8. `azd provision base --preview -e "<env>"`を実行し、NAPに関する差分が解消したことを確認する。ポリシー管理のNSG関連付けは期待されたdriftとして残る場合がある。

## ローカル開発

リポジトリはuv workspace構成です。hostはルート`pyproject.toml`の互換範囲に従います。GitHub Actionsとlock更新workflowはsetup-uvで互換範囲の下限を選択します。Dockerのuv versionは`azd package api`との互換性のためDockerfileに明記し、`check-uv-version`がルートの下限との一致を検査します。ルートで一度同期すれば、`src/api`と`src/external-sli-publisher`の両方の依存と開発ツール（ruff、ty、pytest、locust）が揃います。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" check-uv-version
uv run --no-project "${PWD}/scripts/tasks.py" sync-dev
lefthook install
uv run --no-project "${PWD}/scripts/tasks.py" run
```

`lefthook install` は、コントリビュータ向けにこのリポジトリの Git hooks を登録します。pre-commit は毎コミット、Git index 内の `uv.lock` とルート `pyproject.toml` を既存の public lock validator で検査します。作業ツリーとは独立してコミット対象を読み、非public source、必須ファイルの欠落、未解決の競合、解析不能の場合はコミットを中止します。public lock の検査では、依存の同期、ネットワーク接続、ファイルの修復や再ステージは行いません。実行には uv とインストール済みの Python 3.14 以降が必要です。

ステージされた `infra/` 配下の Bicep ファイルがある場合は、`infra/main.bicep` と `infra/sli/main.bicep` をビルドします。hook の未導入や `--no-verify` による省略に備え、CI の public lock 検査も維持します。Lefthook のインストール方法は [公式手順](https://lefthook.dev/install/) を参照してください。

### 組織承認済み package index を使う環境

public package registry へ直接接続できない環境では、user-level の uv 設定に組織承認済みの Python package index を一つ構成します。`[[index]]`には`default = true`を指定し、public PyPIへのfallbackを無効にします。index URLや認証情報はリポジトリへ保存しません。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" qa-app
```

task runnerは有効なapproved-index設定を検出すると、通常のtaskでは、各processの最初のworkspaceコマンドを実行する前に`.venv`を再構築します。同期開始からtask processの終了までは、対象venvの正規化pathから導出したprocess間lockをOSの一時領域で保持するため、同じvenvを使う通常のworkspace taskは直列に実行されます。レビュー専用環境のprocess間引渡しは[full レビューの Python 環境](#full-レビューの-python-環境)を参照してください。明示的に環境だけを準備する場合は`sync-dev-approved-index`を実行します。

同期処理はuser-level設定とpublic lockのsourceを検査してから、public `uv.lock`を一時requirementsへ変換し、構成済みのpackage indexから`.venv`を作成します。変換後のrequirementsも検査し、direct URL、find-links、hash検証やTLS検証を無効にする設定、projectやdependency groupを変更する環境変数を拒否します。exportと後続の`uv run`はroot projectと対象venvの絶対pathへ固定します。registry packageには`--require-hashes`を適用し、workspace sourceはindexを介さず`.pth`で参照します。通常環境と分離した`.uv-state/cache/`を使い、一時requirementsは処理後に削除します。index固有のusernameとpassword環境変数は同期processだけへ渡し、ruff、ty、pytest、アプリなどの後続processから除去します。通常のtaskでは、同じprocess内の後続コマンドに、直前に構築した環境を使う`--no-sync`を適用します。

post-edit hookは依存関係の整合性を判定しません。Python編集時はprojectの`.venv`にある`ruff`を直接実行し、ruffがない場合は同期を要求します。lockと仮想環境の整合性は同期taskとCIで検証します。

`uv.lock`が同期中に変わった場合や同期が中断した場合、taskは対象コマンドを実行しません。同じコマンドを再実行してください。通常の同期へ戻す場合はuser-levelのapproved-index設定を無効にしてから`sync-dev`を実行します。

明示的な `sync-dev-approved-index` と `package-api-approved-index` には、取得元だけが変わった lock の限定修復があります。Git HEAD の public lock と比較し、registry と artifact の URL 変更、および artifact の `size` / `upload-time` の消失だけなら、通知して HEAD の内容へ戻します。パッケージ、版、依存関係、marker、workspace source、artifact 数や hash、その他の値が異なる場合は保存したまま停止します。

修復が必要な場合は、比較元の不在、lock の staged 変更、root/member の `pyproject.toml` の変更、競合、処理中の入力変更も停止理由です。対象 lock の絶対パス単位で修復を排他します。有効な public lock にはこの Git 条件を課しません。QA、lint、レビューに伴う暗黙同期と validator は修復しないため、取得元変更で停止した場合は明示的な同期 task を使ってください。依存や hash の変更を修復条件へ追加して回避しないでください。

pre-commit で拒否された場合は、まずステージされた2ファイルの差分を確認してください。取得元だけの変更を限定修復する場合は、対象のステージを解除してから上記の明示 task を実行し、修復後の差分を確認して再ステージします。初回コミットや依存変更を含む場合は限定修復の対象外です。[public lockfile の更新](#public-lockfile-の更新)手順で用意した内容をステージしてください。通常のコミットで追加操作は不要です。

task runnerを介さない `uv run` では、projectの自動同期を明示的に止めます。

```bash
UV_NO_SYNC=1 uv run <command>
```

PowerShellでは `$env:UV_NO_SYNC = "1"` を設定します。

### Docker build のpackage index

`azd up` の実行者は、public PyPI、公開コンテナレジストリ、Helm repository からの取得が利用方針で許可されていることを確認してください。コーディングエージェントは利用者による確認を得てから、対象環境に次を設定します。疎通成功や user-level 設定の不在を許可とはみなしません。

```bash
azd env set AZD_ALLOW_PUBLIC_SOURCES true -e "<env>"
```

`preup` hook は `AZD_ALLOW_PUBLIC_SOURCES` が未設定、不正、または `false` の場合に非ゼロで終了します。`true` でも、user-level uv に public PyPI 以外の index、取得元に関する追加設定、不正な設定、または引き継げない uv 環境変数があれば停止します。設定ファイルの探索は既存の専用 task と共通で、`UV_CONFIG_FILE`、`XDG_CONFIG_HOME`、OS ごとの既定位置に従います。公開取得の許可を取り消す場合は、この値を `false` に変更してください。

hook は標準ライブラリだけを使い、workspace の同期、ネットワーク接続、設定変更は行いません。起動にも `--offline --no-python-downloads` を指定するため、uv と Python 3.14 以降を事前に用意してください。このチェックは宣言された利用方針との矛盾を検出するもので、接続可否の確認や通信制御ではありません。個別の `azd package`、`azd deploy`、Docker build には `preup` は適用されません。

組織承認済み index が必要な環境では、許可設定や uv 設定を変更してチェックを通すのではなく、以下の専用手順を使います。hook は build や適用方法の切り替えを自動実行しません。公開コンテナレジストリや Functions remote build の public PyPI も禁止されている場合は、専用手順もそのままでは使用できません。

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

初回構築のコマンド例を次に示します。ログイン、環境作成、権限と feature flag の準備後に実行してください。すべての `<env>` に同じ環境名を指定し、`<image-reference>` には先頭の build task が出力したイメージ参照全体を指定します。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" package-api-approved-index
azd provision base -e "<env>"
azd deploy api-instrumentation -e "<env>"
azd exec -e "<env>" -- uv run --no-project "${PWD}/scripts/tasks.py" deploy-node-provisioning
azd exec -e "<env>" -- uv run --no-project "${PWD}/scripts/tasks.py" deploy-api-approved-index \
  --image "<image-reference>"
azd deploy observability -e "<env>"
azd deploy chaos-mesh -e "<env>"
azd deploy external-sli-publisher -e "<env>"
azd provision sli -e "<env>"
```

通常の `azd up`、`azd package api`、`azd deploy api` は public PyPI で API を build し、事前ビルドを引き継ぎません。制限環境では上記手順を使い、`azure.yaml` の一時書換えや public PyPI への fallback は行いません。API 更新時に他サービスを再適用する必要はありません。Functions の remote build は従来どおり public PyPI を使います。認証が必要な index は専用 Docker build の対象外です。`uv.lock` は public PyPI の URL を維持します。

### AKS の接続先と認証

azd と Azure CLI は別々にログインします。hook と専用 task は対象 AKS の情報を Azure CLI で照会するため、その identity にも管理プレーンの読み取り権限が必要です。Kubernetes の認証と RBAC は別に成立させてください。`azd exec -e "<env>"` は環境値を渡すだけで、Kubernetes の context を準備するコマンドではありません。

`KUBECONFIG` 未指定時は、hook と独自の稼働確認が処理専用の一時 kubeconfig を作り、対象 subscription、resource group、AKS を明示して user credentials を取得し、`azd` 認証へ変換します。実際の deploy は引き続き azd が標準の接続準備を行います。一時ファイルはその処理内だけで使い、終了時に削除します。先行 deploy や手元の既定 context に依存しません。azd 自体は共有の `~/.kube/config` を更新するため、既定設定の不変性は保証しません。

専用 kubeconfig を使う場合は、準備済みの単一ファイルを絶対パスで `KUBECONFIG` に指定し、同じ値を azd と hook に渡します。hook は ARM の対象 AKS と API server の対応を検証し、credentials の再取得、認証方式の変換、context の書換えは行いません。不在、不正、対象不一致は停止理由です。相対パス、複数ファイルのマージ、proxy 経由の独自 endpoint は対象外です。必要な `kubelogin` の準備と、そのファイルに設定された認証方式でのログインは利用者が行ってください。

readiness と OTel helper の `--timeout-seconds` は、環境解決から接続準備、待機、最後の照会までを含む全体上限です。rollout の個別上限は全体の残り時間を延長しません。期限切れや認証失敗では、その理由を確認し、別 context への切替えで成功を代用しないでください。

### public lockfile の更新

dependencyを変更する場合、public PyPIへ接続できるGitHub Actionsでlockfileを生成します。workflowはrepositoryへのwrite permissionを持たず、`uv.lock` をartifactとして返します。

```bash
gh run list --workflow refresh-uv-lock.yml --branch <branch>
gh run download <run-id> --name uv-lock-public --dir tmp/refresh-uv-lock
```

workflowはルートまたは `src/` 配下の `pyproject.toml`、`uv.lock`、workflow定義自身を変更したpull requestで実行されます。既定branchへmergeした後は`workflow_dispatch`でも実行できます。取得した`uv.lock`の差分を確認して変更branchへ追加すると、組織承認済みpackage indexを使う環境では次の通常のworkspace task実行時に再同期します。package indexがpublic lockと同一hashのartifactを提供できない場合、同期は失敗します。

Renovateはworkspaceの依存について更新候補の検出だけを行い、lockは更新しません。workspace member、`resolution-strategy = "lowest"`、public PyPIを参照する`uv.lock`、external SLI publisherのrequirements同期を一度の更新で維持できることを保証できないためです。lockの更新経路は`refresh-uv-lock.yml`のままとし、取得した`uv.lock`は`check-uv-version`、`check-public-lock`、`check-publisher-requirements`、既存QAで検証します。責務の全体像は[依存パッケージとツールの更新管理](dependency-management.md)を参照してください。

## テストと品質確認

アプリケーション:

クリーン環境や新しいworktreeでは、通常環境で`uv run --no-project "${PWD}/scripts/tasks.py" sync-dev`を実行してください。組織承認済みpackage indexを使う環境では、workspaceコマンドを含む通常のtaskが実行前に環境を同期します。レビュー専用環境の扱いは[full レビューの Python 環境](#full-レビューの-python-環境)を参照してください。絶対pathと`--no-project`は、task runnerの起動前にuvが別のprojectを探索または同期することを防ぎます。

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

`review-repo-full` は元 worktree を保護するため、隔離コピーの `.venv` を `UV_PROJECT_ENVIRONMENT` に指定し、別 process の `prepare-review-python-env` で準備します。approved-index を使う場合も上記の同期処理を通り、venv の消去、public lock と一時 requirements の検査、`--require-hashes`、専用 cache、同期前後の lock hash 照合を行います。過去に public PyPI から取得した artifact の再利用や public source への fallback は認めません。workspace source の `.pth` は隔離側の API と publisher を参照します。隔離コピーは OS sandbox ではなく、準備段階ではネットワークから package を取得する場合があります。

準備に成功した場合だけ、レビュー処理は同じ隔離先 venv と内部マーカー `AKS_CHAOS_LAB_REVIEW_ENV_PREPARED=1` を後続 QA の子 process へ明示的に渡し、QA を逐次実行します。task runner はこのマーカーの通常の環境継承を除去し、準備 process にも渡しません。準備に失敗した場合は後続 QA を起動せず、`unverified` として報告します。

引渡しを受けた task は Python の存在を確認し、後続実行に `--no-sync` を付けます。この分岐では取得元や lock hash を再検証しないため、取得元条件の根拠は直前の準備成功とレビュー処理による限定された引渡しにあります。マーカーや `--no-sync` 自体が取得元を保証するわけではなく、元 worktree や任意の既存 venv を再利用するための設定ではありません。

venv 単位の process 間 lock は準備 process が取得し、その終了時に解放します。後続 QA はこの lock を再取得せず、レビュー専用 venv と逐次実行を使います。レビュー全体で lock を保持する方式ではなく、`--no-sync` も環境を読み取り専用にはしません。

### GitHub Actions の統合テスト

`Platform Integration Test`（`.github/workflows/integration-test.yml`）は手動起動です。`full` は一時環境で既存の `azd up` を実行し、HTTP endpoint を検査します。`infra-only` は base layer の構築だけを行い、アプリと sli layer は適用しません。AKS は現在の IaC と同じ Base に固定し、`Automatic` と `app-only` は選択肢に含めません。

GitHub Environment `integration-test` に認証用 secrets `AZURE_CLIENT_ID`、`AZURE_TENANT_ID`、`AZURE_SUBSCRIPTION_ID` を設定します。identity には本書の構築と削除に必要な権限を付与します。`full` で公開取得が許可されている場合は、同 Environment の変数 `AZD_ALLOW_PUBLIC_SOURCES` を `true` に設定してください。workflow は未設定の値を補完しません。runner は `ubuntu-latest` を維持し、uv と Python を各処理の前に準備します。

cleanup job は構築の成否にかかわらず、実行IDに対応する環境名と subscription を既存の `cleanup-azure-monitor-sli-resources.py` に渡します。`.azure` の引き継ぎは行いません。RG の削除完了待機を含めて job の上限を90分とし、削除失敗は job の失敗として報告します。

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

`predown` hook は対象環境の `AZURE_SUBSCRIPTION_ID` を必須とし、取得できなければ削除前に停止します。Azure CLI の既定 subscription は使わず、取得した値を各操作へ明示します。Service Group は tenant scope のため、環境名による所有確認も維持します。指定した RG が対象環境の命名規則に一致しない場合は、RG を削除せず停止します。

Service Group ID が環境変数にない場合は、対象環境の base デプロイの操作記録から取得します。操作記録を手動で削除した環境では、`AZURE_MONITOR_SLI_SERVICE_GROUP_ID` または `AZURE_MONITOR_SLI_SERVICE_GROUP_NAME` を指定してください。SG 本体の取得が `ResourceNotFound` を返した場合だけ SG の削除を省略し、SLI 一覧の403を削除済みとは扱いません。

SG の DELETE が202を返した場合は、応答の `Location` で非同期削除の完了を確認します。`Retry-After` に従い、指定がなければ5秒間隔で、削除要求と完了待ちを合わせて最大10分待機します。処理中の SG 本体の GET は削除完了の判定に使いません。

Service Group 側の探索や削除に失敗した場合は、エラーを記録して subscription 内の後始末を続け、最後に非ゼロで終了します。この場合、再実行で SG を特定できるよう base デプロイ記録は残します。RG、AKS、DCRA の存在確認は、正常な応答で対象の不在を確認できた場合だけ削除済みと扱います。存在確認や DCRA 削除後の確認に失敗した場合は、base RG を削除せず停止します。Service Group API の制約と改善確認項目は [workarounds.md §A-8](workarounds.md#a-8-service-group-api-の探索と不在応答を補う) を参照してください。

hook は Service Group scope の SLI / Service Group / AKS の OTLP Application Insights DCR association / deployment record / base resource group を整理します。AKS 上の OTLP DCRA を先に削除することで、App Insights managed resource group (`ai_<appi-name>_<guid>_managed`) は base RG の削除に連動して消えます。詳細な順序と理由は [ADR-009](adr/009-azure-monitor-sli-and-prometheus-slo.md)、[docs/workarounds.md §A-4](workarounds.md#a-4-predown-hook-で-service-group-scope-sli-と環境別-service-group-を削除)、[§B-2](workarounds.md#b-2-predown-hook-で-aks-上の-otlpappinsightsextension-dcra-を先に削除) を参照してください。

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
