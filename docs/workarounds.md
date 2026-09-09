# ワークアラウンド棚卸し

このリポジトリで継続中のワークアラウンドと、それぞれの **解消条件** をまとめる。`review-repo` エージェントの定期チェック対象。GA や仕様改善で不要になったものは剥がす。

ID は履歴追跡用に固定する。削除済み ID は再利用しない。

## 2026-09-09 棚卸し結果

gh-aw v0.88.7 の公式ソースと、週次 workflow 3 件のコンパイル結果を確認した。

| ID | 反映内容 | 検証結果 |
|---|---|---|
| D-10 | 削除 | `safe-outputs` の `noop: false` を削除した。v0.88.7 の[設定抽出処理](https://github.com/github/gh-aw/blob/v0.88.7/pkg/workflow/safe_outputs_config_extraction.go)では暗黙 noop の Issue 報告は既定で無効であり、[maintenance 判定](https://github.com/github/gh-aw/blob/v0.88.7/pkg/workflow/noop.go)でも暗黙 noop は対象外である。再生成した 3 件の lock で noop の設定と handler の Issue 報告が無効であること、`agentics-maintenance.yml` が生成されないことを確認した。ログ用 noop ツールは利用可能になるが、実行結果を毎回 create-issue で記録する本文は維持した。 |

## 2026-09-02 棚卸し結果

既存の azd `eval` 環境で、API version の移行結果を確認した。

| ID | 反映内容 | 検証結果 |
|---|---|---|
| D-4 | 削除 | `metricAlerts@2026-01-01` の what-if が provider validation を通過し、既存の SLI Metric Alert 6 件も同 API version でデプロイ済みであることを確認した。preview API を継続する回避策が不要になったため削除した。 |

## 2026-07-24 棚卸し結果

既存の azd `eval` 環境で、削除候補を個別に差分検証した。

| ID | 反映内容 | 検証結果 |
|---|---|---|
| A-3 | 継続 | managed DCR の `Monitoring Metrics Publisher` を外して SLI の PUT を実行すると、`DestinationAmwAccountAccessValidator` が access denied を返した。割り当てを復旧すると provision が成功した。 |
| A-7 | 削除 | 現行 spec の `eq` と scalar `value` へ変更し、what-if の operator 差分解消、SLI provision、GET、宛先メトリクスを確認した。 |
| C-2 | 更新 | 最新 GA `2026-05-01` は `addonAutoscaling` 未対応のため preview 継続。公式 schema で対応を確認した最新 preview `2026-05-02-preview` へ全 managedClusters 参照を更新した。 |
| D-2 | 削除 | `azure.yaml` で今回検証した azd 1.28.1 以上を必須化し、`eval` の refresh と SLI what-if が成功した。旧版向け復旧手順を削除した。 |
| D-4 | 継続 | `metricAlerts@2026-01-01` は Bicep build を通るが、`eval` の provider validation は `NoRegisteredProviderFound` で拒否した。`2024-03-01-preview` を継続する。 |

## 2026-06-26 棚卸し結果

リポジトリの現行 IaC と upstream issue の状態を確認し、以下を整理した。

| ID | 反映内容 | 検証結果 |
|---|---|---|
| C-2 | 一部削除 | Fleet は `2026-06-01` GA API に移行し、eval で `azd provision base` 成功。AKS は `2026-04-01` GA API で `addonAutoscaling` が未対応のため、`2026-03-02-preview` を継続。 |
| D-2 | 経過観察 | Azure/azure-dev#8064 は close 済み。azd 1.26.0 で eval の fresh `azd down` / `azd up` / `azd env refresh` が成功し、`DeploymentNotFound` は再現しなかった。旧版 azd 向けの復旧手順として記述は残す。 |
| D-8 | 削除 | Azure/azure-dev#8239 は close 済み。azd 1.26.0 の eval `azd up` で reserved-word warning は再現せず、Bicep コメントから false-positive 記述を削除。 |

## 2026-05-17 棚卸し結果

実環境を使って「workaround を削除できるか」を確認し、以下を削除または整理した。

| ID | 反映内容 | 検証結果 |
|---|---|---|
| A-6 | 削除 | `AZD_DEPLOY_TIMEOUT` 未設定の fresh `azd up` が成功。 |
| B-1 | 削除 | `Microsoft.Insights/components@2020-02-02` でも OTLP managed DCR / managed RG が作成された。 |
| B-3 | 削除 | B-2 の DCRA 先行削除を残す前提では、`postdown` force-delete なしで `ai_*_managed` が残留しない。 |
| B-4 | 削除 | 現行 Microsoft Learn では誤記 `AKSAzureMonitorAISupportPreview` が確認できない。feature flag 管理は C-1 に集約。 |
| C-1 | 一部削除 | `AKS-OMSAppMonitoring` 未登録でも full `azd up` 成功。`Microsoft.Insights/components@2020-02-02` 前提では `OtlpApplicationInsights` 未登録でも OTLP managed DCR / managed RG 作成成功。Azure Monitor SLI destination metrics は `EnableCustomMetricsV2` が Pending のまま動作したため、SLI 用 feature flag 登録手順を削除。残り 2 flag は AKS preflight で必要なため継続。 |
| C-2 | 一部削除 | 当時の検証では AKS `managedClusters` を `2026-03-01` GA API に移行し、Fleet は GA API が `UnsupportedApiVersion` のため preview 継続とした。現行 IaC の状態は 2026-06-26 棚卸しを参照。 |
| D-1 | 削除 | fresh 環境で `up{job="node"}` / `node_cpu_seconds_total` / `node_memory_MemAvailable_bytes` を即時確認。 |
| D-3 | 削除 | 200 requests で App Insights `requests` が rows=206 / itemCount=206。sampling 影響を再現せず。 |
| D-6 | 書き換え | OTLP logs pipeline 自体は実装済み。一方、fresh `azd up` 直後に Pod へ `OTEL_*` env が注入されない事象が残る。 |

---

## A. Azure Monitor SLI 関連

### A-1. `infra.layers` で base/sli を分離し、external SLI metric 出現を待つ

- **概要**: `azd up` の workflow を `provision base` → `deploy api` → `deploy observability` → `deploy chaos-mesh` → `deploy external-sli-publisher` → `provision sli` に分割し、`sli` layer の `preprovision` で SLI 用 good / total metrics が Managed Prometheus に出るまで待つ。
- **理由**: Azure Monitor SLI は作成時点で入力 metric と partitioning dimensions が Managed Prometheus に存在することを要求する。メトリクス materialize 前に SLI を作ると validation で失敗する。
- **場所**: `azure.yaml`、`infra/sli/main.bicep`、`docs/adr/012-functions-direct-external-sli-probe.md`
- **解消条件**: SLI が「将来生成されるメトリクス」を前提にした作成を許容する API になる。
- **確認方法**: 一時環境で external SLI metrics 出現待ちなしに `infra/sli/main.bicep` を作成し、SLI が作成エラーにならないか試す。
- **最終確認**: 2026-05-19、入力 metric / dimensions 不在時の SLI 作成 validation failure を避けるため継続。

### A-2. `scripts/wait-for-external-sli-signals.py` で external SLI metric 出力待機

- **概要**: `sli` layer の `preprovision` hook で、Managed Prometheus 上に `chaos_app_external_availability_total`、`chaos_app_external_latency_total`、および `latencyThresholdLe` に対応する `chaos_app_external_latency_good{le="<latencyThresholdLe>"}` が出るまで待つ。Azure Monitor SLI destination `:Value` metric は `postprovision` hook では待たない。
- **理由**: A-1 と同じ。SLI 作成前に external SLI input metrics が materialize されている必要がある。SLI 作成後の destination metric は評価開始まで時間がかかるため、`azd up` の完了条件にしない。必要な場合は `uv run scripts/wait-for-external-sli-signals.py --skip-source --require-sli-destination` で手動確認する。
- **場所**: `scripts/wait-for-external-sli-signals.py`、`azure.yaml`
- **解消条件**: A-1 と同じ。
- **確認方法**: A-1 と同じ。destination metric の手動確認は、SLI 作成後に `uv run scripts/wait-for-external-sli-signals.py --skip-source --require-sli-destination` を実行する。

### A-3. AMW managed resource group 内 DCR への SLI RBAC 付与

- **概要**: `MA_<amw-name>_<region>_managed` リソースグループ内の AMW と同名 DCR に対して、SLI 用 UAMI に `Monitoring Reader` と `Monitoring Metrics Publisher` を付与する。
- **理由**: AMW 本体への RBAC だけでは SLI の storage location validation を通らない。Microsoft Learn は destination workspace default DCR の最小権限として `Monitoring Reader` を記載しているが、実機の validator は `Monitoring Metrics Publisher` も要求する。
- **場所**: `infra/modules/azmonitor/sli-managed-dcr-rbac.bicep`、ADR-009 §RBAC
- **解消条件**: Microsoft 側で AMW 本体への RBAC だけで SLI が作れるよう挙動が修正される、または公式に managed RG への RBAC が必要だと文書化され、別の方法（policy / built-in role）が用意される。
- **確認方法**: managed DCR への role assignment を一時的に外し、SLI 作成が通るか試す。
- **最終確認**: 2026-07-24、`eval` 環境で managed DCR の `Monitoring Metrics Publisher` を外し、SLI の description 変更で PUT を発生させると `DestinationAmwAccountAccessValidator` access denied。割り当てを同じ ID で復旧し、RBAC 伝播後に provision 成功。`Monitoring Reader` と `Monitoring Metrics Publisher` は削除不可。

### A-4. `predown` hook で Service Group scope SLI と環境別 Service Group を削除

- **概要**: `azd down` 前に `uv run scripts/cleanup-azure-monitor-sli-resources.py` を実行し、Service Group scope の SLI と環境別 Service Group を削除する。
- **理由**: `azd down` は subscription / resource group scope までしか到達せず、tenant scope（Service Group / SLI）が残留する。
- **場所**: `scripts/cleanup-azure-monitor-sli-resources.py`、`azure.yaml`、ADR-009
- **解消条件**: `azd` が tenant scope のリソースを `down` で cascade delete できるようになる、または Service Group が subscription scope のリソースとして提供される。
- **確認方法**: Service Group cleanup を一時的に無効化して `azd down` 後、Service Group / SLI が残らないか確認する。
- **最終確認**: 2026-05-17、Service Group cleanup を外すと Service Group と availability / latency SLI が残留。削除不可。

### A-5. 環境別 Service Group への `Service Group Administrator` 直付与が必要

- **概要**: 親 Service Group の Contributor では子 Service Group の `Microsoft.Monitor/slis/write` が継承されないため、環境別 Service Group に対して直接 `Service Group Administrator` を付与する。
- **理由**: Service Group の親子関係は Azure RBAC のリソース ID パス継承と独立している。
- **場所**: `docs/deployment.md` §必要なロール §2、ADR-009 §RBAC
- **解消条件**: Service Group RBAC が Azure RBAC の path 継承に揃う、または親 Service Group からの継承で SLI 作成が許可される。
- **確認方法**: 親 Service Group の Contributor のみを付与し、子 Service Group での SLI 作成が通るか確認する。
- **最終確認**: 2026-05-17、親 Service Group Contributor のみでは `Microsoft.Monitor/slis/write` が AuthorizationFailed。削除不可。

### A-8. Service Group API の探索と不在応答を補う

- **概要**: cleanup は Service Group ID が未指定の場合、対象環境の base デプロイの操作記録から ID を取得する。SG 本体の GET が `ResourceNotFound` を返した場合は削除済みとして扱い、SLI 一覧は取得しない。SG 削除は `Location` で非同期処理の完了を待ち、失敗や時間切れの場合は再実行用に base デプロイ記録を残す。
- **理由**: 使用中の `2024-02-01-preview` には Service Group の一覧 GET がなく、SG 不在時の SLI 一覧取得は403を返す場合がある。これらを通常の一覧取得や不存在判定として扱うと、探索に失敗したり、削除済み環境の cleanup が失敗を繰り返したりする。`az rest` は非同期削除を待たず、Azure CLI 2.90.0 の `az resource` も SG の tenant scope ID を受け付けないため、SG 削除の完了待ちはスクリプトが担当する。
- **場所**: `scripts/cleanup-azure-monitor-sli-resources.py`、[環境削除](deployment.md#環境削除)
- **解消条件**: Service Group 管理 API またはその CLI に一覧操作が追加され、作成直後を含め対象を取得できるようになれば、デプロイ操作記録による探索を置き換える。SG 不在時の SLI API 応答を権限不足と区別できるようになれば、SG 本体の先行確認を再評価する。CLI が構造化したエラーコードを返すようになれば、現在の stderr からの抽出を置き換える。CLI が tenant scope の SG 削除と非同期処理の完了待ちを扱えるようになれば、スクリプト内の HTTP 処理を置き換える。GA への移行だけでは撤去しない。
- **確認方法**: `review-repo full` の棚卸しで、[公開 API 定義](https://github.com/Azure/azure-rest-api-specs/tree/main/specification/management/resource-manager/Microsoft.Management/ServiceGroups)と[管理 API の説明](https://learn.microsoft.com/azure/governance/service-groups/manage-service-groups)を確認し、一覧操作、不在時の応答、CLI の tenant scope 対応と完了待ちの改善を報告する。実環境の調査を依頼された場合は、既存 SG と不在 SG の GET / SLI 一覧を読み取り専用で照会し、変更後の応答を確認する。
- **最終確認**: 2026-09-08、公開仕様に一覧 GET がなく、実 API でも404。SG 本体の不在は404 `ResourceNotFound`、同じ SG の SLI 一覧は403 `AuthorizationFailed`。`eval` の base デプロイの操作記録から SG ID を取得できた。

---

## B. OTLP / Application Insights 関連 (ADR-006)

### B-2. `predown` hook で AKS 上の `OtlpAppInsightsExtension` DCRA を先に削除

- **概要**: `azd down` 前に AKS の OTLP DCR association を削除する。
- **理由**: B-3 の `postdown` force-delete を削除したため、App Insights managed RG (`ai_*_managed`) を残さないには AKS 上の DCRA を先に外す必要がある。DCRA を残したまま base RG を削除すると `ai_*_managed` が残留する。
- **場所**: `scripts/cleanup-azure-monitor-sli-resources.py`、ADR-009
- **解消条件**: AKS DCRA を残したまま `azd down` しても `ai_*_managed` が残留しない deprovision flow になる。
- **確認方法**: B-2 の DCRA 削除を一時的に無効化して `azd down` を実行し、`ai_*_managed` が残らないか確認する。
- **最終確認**: 2026-05-17、B-2 と B-3 を両方外すと `ai_*_managed` が残留。B-2 を残して B-3 を外した場合は残留なし。

---

## C. プレビュー機能 / Preview API バージョン

### C-1. 2 つの feature flag を `az feature register` 必須

- **概要**: `AKS-AddonAutoscalingPreview`, `AzureMonitorAppMonitoringPreview` をサブスクリプション単位で事前登録する。
- **理由**: これらは現行構成の AKS VPA addon autoscaling / AKS App Monitoring に必要。`AKS-OMSAppMonitoring` は full `azd up` 成功、`OtlpApplicationInsights` は GA App Insights API (`Microsoft.Insights/components@2020-02-02`) 前提で OTLP managed DCR / managed RG 作成成功を確認したため、手順から削除済み。Azure Monitor SLI destination metrics 用に扱っていた `Microsoft.Insights/EnableCustomMetricsV2` / `Microsoft.Insights/EnableAmwAutoscale` は、2026-06-25 時点で `EnableCustomMetricsV2` が Pending のまま SLI destination metrics が出ることを確認したため、手順から削除済み。
- **場所**: `docs/deployment.md` §プレビュー機能とリソースプロバイダー登録
- **解消条件**: 各機能が GA し、feature flag 登録が不要になる。
- **確認方法**: feature を未登録に戻した一時サブスクリプション状態で fresh `azd up` または `azd provision base` が通るか確認する。
- **最終確認**: 2026-05-17、`AKS-AddonAutoscalingPreview` 未登録では AKS preflight が失敗、`AzureMonitorAppMonitoringPreview` 未登録では AKS preflight が失敗。`AKS-OMSAppMonitoring` 未登録では full `azd up` 成功。`OtlpApplicationInsights` 未登録かつ App Insights GA API 前提の最小構成では、OTLP managed DCR / managed RG 作成成功。2026-06-25、`Microsoft.Insights/EnableCustomMetricsV2` は Pending のまま Azure Monitor SLI destination metrics が AMW に出ることを確認したため、SLI 用 feature flag 登録手順を削除。

### C-2. AKS の preview API バージョンを継続使用

- **概要**: 現行 IaC では AKS `managedClusters` に `Microsoft.ContainerService/managedClusters@2026-05-02-preview` を使用する。Fleet 関連 resource type は `Microsoft.ContainerService/fleets@2026-06-01` と同 version の member / update strategy / auto upgrade profile に移行済み。
- **理由**: AKS の GA `2026-06-01` には、現行構成の VPA addon autoscaling に必要な `workloadAutoScalerProfile.verticalPodAutoscaler.addonAutoscaling` が存在しない。現行 preview `2026-05-02-preview` の公式 schema には同プロパティが定義されているため、preview を継続する。
- **場所**: `infra/modules/aks.bicep` と managedClusters を参照する各 Bicep module、`infra/modules/fleet.bicep`
- **解消条件**: AKS の VPA addon autoscaling を含む GA API バージョンが提供される。
- **確認方法**: AKS `managedClusters` を最新の GA API に置換して `azd provision base --preview` と `azd provision base` が通るか確認する。
- **最終確認**: 2026-09-09、公式 REST 仕様の [stable 一覧](https://github.com/Azure/azure-rest-api-specs/tree/main/specification/containerservice/resource-manager/Microsoft.ContainerService/aks/stable)で最新 GA が `2026-06-01` であることを確認。[同 GA の定義](https://raw.githubusercontent.com/Azure/azure-rest-api-specs/main/specification/containerservice/resource-manager/Microsoft.ContainerService/aks/stable/2026-06-01/managedClusters.json)には `addonAutoscaling` がなく、[現行 preview の定義](https://raw.githubusercontent.com/Azure/azure-rest-api-specs/main/specification/containerservice/resource-manager/Microsoft.ContainerService/aks/preview/2026-05-02-preview/managedClusters.json)には存在する。今回は公開仕様のみを確認し、実環境への適用は行っていない。
- **実環境での確認**: 2026-07-24、公式 schema で GA `2026-05-01` に `addonAutoscaling` がなく、preview `2026-05-02-preview` に存在することを確認。managedClusters の全参照を最新 preview へ更新し、eval の base 差分デプロイが成功した。GET では `provisioningState: Succeeded` と `addonAutoscaling: Enabled` を確認した。

### C-3. Azure Monitor 系 managed resource group 命名は制御不可

- **概要**: AMW (`MA_<amw>_<region>_managed`)、App Insights (`ai_*`) の managed RG 名は固定パターンで命名できない。AKS の `MC_*` のみ `nodeResourceGroup` で制御可能。
- **理由**: Azure 側の仕様。
- **場所**: ADR-006 §マネージドリソースグループの制約
- **解消条件**: Azure 側で命名 API が提供される。
- **確認方法**: Microsoft Learn の AMW / App Insights ドキュメントで命名カスタマイズの記述が追加されたか確認する。
- **最終確認**: 2026-05-17、AMW / App Insights managed RG の命名 API は見つからず。削除不可。

---

## D. 既知の遅延 / 制約

### D-5. OpenTelemetry UpDownCounter `http.server.active_requests` の Pod 再起動時ドリフト

- **概要**: FastAPI / OTel auto-instrumentation が emit する `http.server.active_requests` は UpDownCounter (DELTA aggregation) で per-process state を持つ。Chaos 実験で Pod 再起動が頻発する環境では、`rate()` / `delta()` で集計した時に負値や jitter が出る。
- **理由**: UpDownCounter は process-local な state を保持し、Pod restart で reset されるが、Azure Monitor / Prometheus 側の集計クエリは Cumulative 前提で、reset を補正する仕組みがない。加えて、実機検証では標準 `http.server.active_requests` 自体が dot / underscore 名とも AMW に出ないケースがある。
- **場所**: `docs/observability.md` §運用上の注意、`src/api/app/telemetry.py`、`src/api/app/main.py`
- **解消条件**: 標準 `http.server.active_requests` が restart / no-traffic に強い形で安定 emit される、または Azure Monitor 側で reset 補正が提供される。
- **確認方法**: fresh 環境で App AMW に `http.server.active_requests` / `http_server_active_requests` が安定して出るか、かつ Pod restart 後に負値や jitter が出ないか確認する。
- **最終確認**: 2026-05-17、custom `chaos_app.active_requests` は出るが、標準 `http.server.active_requests` は dot / underscore 名とも系列なし。削除不可。

### D-6. API OTLP `Instrumentation` は Deployment より先に適用する必要がある

- **概要**: AKS App Monitoring admission webhook は `Instrumentation` custom resource を参照して `OTEL_EXPORTER_OTLP_*` を注入する。`Deployment/chaos-app` と `Instrumentation/chaos-app-otel` を同じ Kustomize unit で同時適用すると、Pod admission 時点で `Instrumentation` が存在せず、API Pod に OTLP env が入らない race が起きる。
- **理由**: Kubernetes は同一 Kustomize bundle 内の CR と Deployment の admission-time 依存を保証しない。既に admitted された Pod は後から retroactive に mutate されないため、Application Insights traces / metrics / logs と Redis dependency が欠落する。
- **場所**: `azure.yaml` の `api-instrumentation` service、`k8s/apps/chaos-app/instrumentation/`、`scripts/check-api-otel-injection.py`、`docs/observability.md` §運用上の注意、ADR-006
- **解消条件**: AKS App Monitoring が参照先 `Instrumentation` 未作成時でも Deployment / Pod を後から安全に再評価できる、または Kubernetes 側で CR と Deployment の admission-time ordering を宣言できる。
- **確認方法（現行動作）**: `api-instrumentation` の `postdeploy` hook が Instrumentation の準備完了を待ち、`api` の `predeploy` hook がその存在を、`postdeploy` hook が Pod への OTel 設定注入を自動確認する。これは先行適用を維持した構成の確認であり、撤去可能という証拠ではない。通常の適用順序と承認済み index 向けの操作は [デプロイ手順](deployment.md) を参照する。
- **確認方法（撤去判断）**: 公開仕様で未作成の Instrumentation への対応を確認したうえで、実験の承認を得た新規の一時環境で比較する。先行適用と存在待機を外し、Deployment の先行適用と同時適用のそれぞれで、手動の再適用や Pod restart なしに OTel 設定が注入され、Application Insights に traces / metrics / logs が届くことを確認する。先行適用を維持した対照構成と同じ版と設定を使い、Pod 作成時刻と注入結果を比較する。既存 eval の適用順序は変更しない。
- **最終確認**: 2026-05-20、`sli-flex-test` で `Instrumentation` が `Deployment` より 5 秒遅れて作成され API Pod の `OTEL_*` が欠落。`api-instrumentation` service と deploy hook で ordering / validation を追加。

### D-7. ama-metrics `mdsd.err` で `AMACoreAgent: Connection refused` が多発（実害なし・ログノイズのみ）

- **概要**: `ama-metrics` Deployment の replica pod (`prometheus-collector` container) で `mdsd.err` に `[CreateSocket] Failed to connect port 12564 ... to AMACoreAgent: Connection refused` と `[OtlpTokenFetcher] AMACoreAgent tenant not started, trying to start it. DCR Contents: ...dcr-<otlp>...` が約 60 秒周期で継続出力される。
- **理由**: replica pod の image には `amacoreagent` バイナリが同梱されているが、replica pod 内では `AMACoreAgent` プロセスが supervisor から起動されていない。同じ image を使う `ama-logs` DaemonSet 側では `AMACoreAgent` が正常起動している。
- **実害評価**: Managed Prometheus / Container Insights / ContainerNetworkLogs / OTLP traces / logs のデータパスは正常。残る影響は `mdsd.err` のディスク消費とログノイズのみ。
- **場所**: AKS managed addon の `kube-system/ama-metrics-*` Deployment。リポジトリ側のコードでは制御不能。
- **解消条件**: Microsoft 側で `prometheus-collector` image の supervisor が replica pod でも `AMACoreAgent` を起動する、あるいは OTLP DCR 配信を replica pod 対象から除外する修正が入る。
- **確認方法**: image tag の更新後に `kubectl -n kube-system exec <ama-metrics-pod> -c prometheus-collector -- ps -ef | grep amacoreagent` と `mdsd.err` を確認する。
- **追跡**: [#130](https://github.com/torumakabe/aks-chaos-lab/issues/130)（実害なしと判定済み・closed）。

### D-9. `ErrorAwareSampler` は span 終了後の ERROR を判定できない

- **概要**: `OTEL_TRACES_SAMPLER` が未設定の場合、API は `ParentBased(ErrorAwareSampler)` を使用する。この sampler は親のない root span の name または HTTP path attribute に `chaos`、`error`、`throw` を含む場合に常にサンプルし、それ以外を `TELEMETRY_SAMPLING_RATE` に従って判定する。親 span がある場合は親の sampling decision を継承するため、キーワードを含むリクエストでも未サンプルの親を継承すると保持されない。キーワードを含まないリクエストが span 終了時に ERROR となった場合も、その trace が保持される保証はない。
- **理由**: OpenTelemetry SDK は sampler を span 開始時に呼び出すため、span 終了時に設定される `status=ERROR` を判定材料にできない。リポジトリには Collector 側の tail-based sampling 構成がなく、アプリ側のキーワード判定は欠落を減らすための限定的な回避策である。
- **場所**: `src/api/app/telemetry.py` の `ErrorAwareSampler`、`src/api/tests/unit/test_telemetry.py`
- **解消条件**: SDK 側で全 span を Collector へ送り、Collector 側で `status=ERROR` を条件にした tail-based sampling を構成して、キーワードに依存せず ERROR trace を保持できるようになる。または OpenTelemetry SDK が span 終了時の状態に基づく sampling を提供する。
- **確認方法**: `uv run pytest src/api/tests/unit/test_telemetry.py -k error_aware_sampler` でキーワード判定と ratio-based 判定を確認する。tail-based sampling を導入する場合は、キーワードを含まないエンドポイントでエラーを発生させ、Collector と Application Insights で該当 trace が保持されることを確認する。
- **最終確認**: 2026-08-10、リポジトリ内に tail-based sampling 構成はなく、`ErrorAwareSampler` の単体テストはキーワード判定と ratio-based 判定を対象としている。

### D-11. Windowsでgh-awをPowerShellの子プロセスとして実行

- **概要**: `scripts/tasks.py`から`gh aw compile`を実行するときは、Windowsに限りPowerShellの`Start-Process`を介して標準入力、標準出力、標準エラーを一時ファイルへ分離する。実行時間は60秒に制限し、超過時は既存のプロセスツリー停止処理を使う。
- **理由**: Windowsでgh-aw v0.79.6をPythonの`subprocess`から直接起動すると、`--version`を含むコマンドが待機したまま終了しない。PowerShellの`Start-Process`で入出力を分離した同じ実行ファイルは約1秒で終了し、`compile`も正常に完了する。
- **場所**: `scripts/tasks.py`の`run_gh_aw_compile`
- **解消条件**: gh-awを更新し、WindowsでPythonの`subprocess`から直接実行した`gh aw --version`と`gh aw compile`が待機せず終了することを確認する。
- **確認方法**: Pythonの`subprocess`による直接起動とPowerShellの子プロセス起動の両方でgh-awを実行し、両方が同じ終了コードと生成結果になることを確認する。
- **最終確認**: 2026-08-27、gh-aw v0.79.6はPythonの`subprocess`による直接起動で60秒を超えて待機し、PowerShellの子プロセス起動では`--version`が1.141秒で終了して`compile`も成功した。

### D-12. Renovateはversionとchecksumを一体更新できないため、LefthookのpinをRenovate管理から外し専用taskで更新する

- **概要**: `.github/workflows/ci.yml`の`LEFTHOOK_VERSION`と`LEFTHOOK_SHA256`は対で固定する。`LEFTHOOK_SHA256`はRenovateが計算できないため、Renovateに`LEFTHOOK_VERSION`だけを更新させると、そのPRはCIの`sha256sum -c -`でchecksum不一致となり必ず失敗する。Renovate PRを必ず赤いCIの通知チャネルにしないため、Lefthookは`.github/renovate.json`の対象に含めない。更新候補の検出はscheduled checker（`freshness-checks`の`Lefthook` finding）が公式latest releaseとの比較および公式`lefthook_checksums.txt`との照合で行い、リポジトリ内の座標とchecksum形式は`check-version-pins`が検査する。更新は`uv run --no-project "${PWD}/scripts/tasks.py" update-lefthook-pin --version <version>`で、公式checksumと`LEFTHOOK_VERSION`を同じファイル書き込みで一体更新する。automergeは無効のままとする。
- **理由**: Renovateのregex custom managerは単一の`matchStrings`が捕捉した値の更新候補を提示するだけで、別ファイルや別行のchecksumを計算して同時に書き換える機能を持たない。versionとchecksumを同じcustom managerで安全に一括更新する一般的な方法は現時点でない。
- **場所**: `.github/workflows/ci.yml`の`LEFTHOOK_VERSION`/`LEFTHOOK_SHA256`、`scripts/tasks.py`の`check-version-pins`/`update-lefthook-pin`/`freshness-checks` task target（`.github/renovate.json`にLefthookのcustomManagerは置かない）
- **解消条件**: RenovateがGitHub Releaseのchecksum資産から関連値を解決し、同じcustomManagerでversionとchecksumを一体更新できるようになる。その時点でLefthookをRenovate管理へ戻し、専用checkerと更新taskの必要性を再評価する。
- **確認方法（現行動作）**: `freshness-checks`の結果で、pin versionが公式latest releaseと異なるとき`Lefthook` findingが`unverified`（`reason_code: update-available`）になること、pin済みchecksumが公式`lefthook_checksums.txt`と一致することを確認する。不正値の検出は一時コピーで`LEFTHOOK_SHA256`を1文字削り、`check-version-pins`が`fail`になることを確認する。これらは専用checkerの確認であり、Renovateによる一体更新の確認ではない。
- **確認方法（撤去判断）**: Renovateの公開仕様で、同じcustomManagerによるversionとchecksumの一体更新が提供されたことを確認する。その後、承認済みの検証用リポジトリで旧版からの更新を試し、同じPRで`LEFTHOOK_VERSION`と`LEFTHOOK_SHA256`が更新され、checksumが更新先の対象platform用公式資産と一致することを確認する。手動補正なしで既存CIが成功することを撤去判断の条件とし、versionだけの更新成功では専用処理を廃止しない。
- **最終確認**: 2026-08-31、pin版2.1.10に対し公式GitHub latest releaseは2.1.12であり、`freshness-checks`が`unverified`（`reason_code: update-available`）を返すこと、`lefthook_2.1.10_Linux_x86_64.gz`の公式checksumがci.ymlのpin値と一致することを確認した。

### D-13. Fleet 登録後に AKS 拡張を導入する

- **概要**: AKS の作成後、Fleet module の完了を待ってから Inspektor Gadget 拡張を導入する。拡張側に `dependsOn: [fleetManager]` を置き、Fleet を特定の拡張に依存させない。
- **理由**: Inspektor Gadget の初期化に伴う AKS 更新中に、Fleet member の登録が `ManagedClusterNotInExpectedState` で拒否されることがある。機能上の依存ではなく、同じ AKS への管理操作の重複を避けるための順序である。
- **場所と対象範囲**: `infra/main.bicep` の同一 deployment 内の Fleet module と拡張 module。Fleet module 内の登録、更新戦略、アラートなどが失敗すると、拡張導入も開始しない。module 完了後も Azure 内部の更新が残る場合があり、内部操作の競合や別 deployment、外部操作による更新まで排他するものではない。
- **拡張の追加と廃止**: 拡張を追加するときは、その拡張側に Fleet 完了後の依存を置く。拡張同士の AKS 更新も競合する場合は、該当する拡張間にも順序を設ける。Inspektor Gadget の廃止だけでは Fleet 側の依存を変更しない。宣言の除去と Azure 上の既存拡張の削除は別操作として扱う。
- **解消条件**: Azure 側が Fleet 登録と拡張初期化の競合を待機または安全に処理できることを確認し、依存を外した新規環境で初回構築が成功する。
- **確認方法（現行動作）**: 生成 ARM template で Fleet が AKS に、拡張が Fleet に依存することを確認する。新規環境の Activity Log で Fleet module 完了後に拡張が開始し、base 作成が azd コマンドの再実行なしで成功することを確認する。これは順序制御が動くことの確認であり、その必要性がなくなったという証拠ではない。
- **確認方法（撤去判断）**: 実験の承認を得た新規の一時環境で、拡張側の Fleet 依存だけを外した構成と、依存を維持した対照構成を比較する。region、AKSと拡張の版、その他の設定をそろえ、Fleet 登録と拡張初期化の開始時刻、完了状態、エラーを Activity Log で確認する。操作が重なった場合も base 作成が azd コマンドの再実行なしで成功することを確認し、初回構築の比較を反復する。既存 eval への再適用、操作が偶然重ならなかった実行、一度だけの成功を競合解消の証拠にしない。
- **再試行**: module 完了後に残る内部操作との競合は、拡張サービスの再試行に任せる。失敗が deployment に返される場合は、対象リソースと最終エラーコードを確認し、Bicep の `@retryOn` による回数上限付きの再試行を検討する。

### D-14. Local DNS の API 更新が既存ノードへ反映されない場合

- **対象範囲**: API の Local DNS プロファイルで設定値が `mode: Required`、読み取り専用の状態が `state: Enabled` だが、新規 Pod の resolver、ノード設定、9253 番のメトリクスで Local DNS の稼働を確認できない既存 pool。
- **追加対応**: 対象 pool の `az aks nodepool get-upgrades` で更新先を確認し、承認後に `az aks nodepool upgrade --node-image-only` を実行する。Kubernetes バージョンと保存済みの BlueGreen 設定は変更しない。この追加更新を通常の有効化へ無条件に組み込まない。
- **理由**: API 更新後も Local DNS のノード設定が生成されず、node image 更新後の新ノードでは稼働した。原因は未確定で、すべての AKS 構成に該当するとは判断していない。
- **解消条件**: 同じ条件の既存 pool で `localdns-config` の更新だけによりノード構成が反映され、新規 Pod の名前解決と Local DNS のメトリクスを確認できるようになる。
- **確認方法**: 更新前後のノード設定と新規 Pod の resolver を比較し、各ノードの `up{job="localdns-metrics"}` と問い合わせ counter を確認する。
- **最終確認**: 2026-09-07、`eval` の新規 System ノード2台で `169.254.10.11` を使う名前解決と `up=1` を確認した。
