# ワークアラウンド棚卸し

このリポジトリで継続中のワークアラウンドと、それぞれの **解消条件** をまとめる。`review-repo` エージェントの定期チェック対象。GA や仕様改善で不要になったものは剥がす。

ID は履歴追跡用に固定する。削除済み ID は再利用しない。

## 2026-05-17 棚卸し結果

実環境を使って「workaround を削除できるか」を確認し、以下を削除または整理した。

| ID | 反映内容 | 検証結果 |
|---|---|---|
| A-6 | 削除 | `AZD_DEPLOY_TIMEOUT` 未設定の fresh `azd up` が成功。 |
| B-1 | 削除 | `Microsoft.Insights/components@2020-02-02` でも OTLP managed DCR / managed RG が作成された。 |
| B-3 | 削除 | B-2 の DCRA 先行削除を残す前提では、`postdown` force-delete なしで `ai_*_managed` が残留しない。 |
| B-4 | 削除 | 現行 Microsoft Learn では誤記 `AKSAzureMonitorAISupportPreview` が確認できない。feature flag 管理は C-1 に集約。 |
| C-1 | 一部削除 | `AKS-OMSAppMonitoring` 未登録でも full `azd up` 成功。`Microsoft.Insights/components@2020-02-02` 前提では `OtlpApplicationInsights` 未登録でも OTLP managed DCR / managed RG 作成成功。残り 2 flag は未登録で失敗したため継続。 |
| C-2 | 一部削除 | AKS `managedClusters` は `2026-03-01` GA API に移行。Fleet は GA API が `UnsupportedApiVersion` のため preview 継続。 |
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

- **概要**: `sli` layer の `preprovision` hook で、Managed Prometheus 上に `chaos_app_external_availability_total` と `chaos_app_external_latency_total` が出るまで待つ。`postprovision` hook では `--skip-source --require-sli-destination` を付け、Azure Monitor SLI destination `:Value` metric まで確認する。
- **理由**: A-1 と同じ。SLI 作成前に external SLI input metrics が materialize されている必要がある。SLI 作成後は input metric だけでは end-to-end 検証にならないため、destination metric を別途確認する。
- **場所**: `scripts/wait-for-external-sli-signals.py`、`azure.yaml`
- **解消条件**: A-1 と同じ。
- **確認方法**: A-1 と同じ。

### A-3. AMW managed resource group 内 DCR への SLI RBAC 付与

- **概要**: `MA_<amw-name>_<region>_managed` リソースグループ内の AMW と同名 DCR に対して、SLI 用 UAMI に `Monitoring Reader` と `Monitoring Metrics Publisher` を付与する。
- **理由**: AMW 本体への RBAC だけでは SLI の storage location validation を通らない（Microsoft Learn 未記載の挙動）。`Monitoring Reader` は destination workspace default DCR の最小権限要件に合わせる。
- **場所**: `infra/modules/azmonitor/sli-managed-dcr-rbac.bicep`、ADR-009 §RBAC
- **解消条件**: Microsoft 側で AMW 本体への RBAC だけで SLI が作れるよう挙動が修正される、または公式に managed RG への RBAC が必要だと文書化され、別の方法（policy / built-in role）が用意される。
- **確認方法**: managed DCR への role assignment を一時的に外し、SLI 作成が通るか試す。
- **最終確認**: 2026-05-19、managed DCR の `Monitoring Metrics Publisher` を外すと `DestinationAmwAccountAccessValidator` access denied。`Monitoring Reader` も destination metric 読み出し要件として付与。削除不可。

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

### C-1. 4 つの feature flag を `az feature register` 必須

- **概要**: `AKS-AddonAutoscalingPreview`, `AzureMonitorAppMonitoringPreview`, `Microsoft.Insights/EnableCustomMetricsV2`, `Microsoft.Insights/EnableAmwAutoscale` をサブスクリプション単位で事前登録する。
- **理由**: これらは現行構成の AKS VPA addon autoscaling / AKS App Monitoring / Azure Monitor SLI destination metrics に必要。`AKS-OMSAppMonitoring` は full `azd up` 成功、`OtlpApplicationInsights` は GA App Insights API (`Microsoft.Insights/components@2020-02-02`) 前提で OTLP managed DCR / managed RG 作成成功を確認したため、手順から削除済み。
- **場所**: `docs/deployment.md` §プレビュー機能とリソースプロバイダー登録
- **解消条件**: 各機能が GA し、feature flag 登録が不要になる。
- **確認方法**: feature を未登録に戻した一時サブスクリプション状態で fresh `azd up` または `azd provision base` が通るか確認する。
- **最終確認**: 2026-05-17、`AKS-AddonAutoscalingPreview` 未登録では AKS preflight が失敗、`AzureMonitorAppMonitoringPreview` 未登録では AKS preflight が失敗。2026-05-19、`EnableCustomMetricsV2` / `EnableAmwAutoscale` 未反映では Service Group namespace の SLI destination metric query が `Custom metrics are not enabled on this resource` で失敗。`AKS-OMSAppMonitoring` 未登録では full `azd up` 成功。`OtlpApplicationInsights` 未登録かつ App Insights GA API 前提の最小構成では、OTLP managed DCR / managed RG 作成成功。

### C-2. Fleet の preview API バージョンを継続使用

- **概要**: `Microsoft.ContainerService/fleets@2025-04-01-preview` など Fleet 関連 resource type は preview API を使用する。AKS `managedClusters` は `2026-03-01` GA API に移行済み。
- **理由**: Fleet update strategy の Approval gate / auto upgrade profile は、検証時点の GA API では ARM preflight が `UnsupportedApiVersion` になり作成できない。
- **場所**: `infra/modules/fleet.bicep`
- **解消条件**: Fleet の必要機能を含む GA API バージョンが提供される。
- **確認方法**: Fleet resources を latest GA API に置換して `azd provision base --preview` が通るか確認する。
- **最終確認**: 2026-05-17、AKS は `managedClusters@2026-03-01` で preview 成功。Fleet は `fleets@2025-03-01` で `UnsupportedApiVersion`。

### C-3. Azure Monitor 系 managed resource group 命名は制御不可

- **概要**: AMW (`MA_<amw>_<region>_managed`)、App Insights (`ai_*`) の managed RG 名は固定パターンで命名できない。AKS の `MC_*` のみ `nodeResourceGroup` で制御可能。
- **理由**: Azure 側の仕様。
- **場所**: ADR-006 §マネージドリソースグループの制約
- **解消条件**: Azure 側で命名 API が提供される。
- **確認方法**: Microsoft Learn の AMW / App Insights ドキュメントで命名カスタマイズの記述が追加されたか確認する。
- **最終確認**: 2026-05-17、AMW / App Insights managed RG の命名 API は見つからず。削除不可。

---

## D. 既知の遅延 / 制約

### D-2. azd の subscription scope deployment polling が散発的に `DeploymentNotFound` を返す

- **概要**: `azd up` / `azd down` の subscription scope deployment 操作で `Deployment '<env>-<layer>?-<unix>' could not be found.` が返って失敗することがある。同名 deployment record は ARM 上で `Succeeded` 状態で残っているケースが直接観測されている。
- **理由**: 未確定。ARM の deployment record が一時的に GET で見えないウィンドウがあるように見えるが、根本原因は未特定。観測事実の報告として upstream issue [Azure/azure-dev#8064](https://github.com/Azure/azure-dev/issues/8064) を起票済み。
- **場所**: `scripts/cleanup-azure-monitor-sli-resources.py`、ADR-009
- **構造的対処 (down 側のみ・実装済み)**: `predown` hook が Service Group scope SLI / Service Group / OTLP DCRA / SLI layer deployment record / base RG / base layer deployment record を順に削除し、`azd down` の Destroy 経路を両 layer で graceful skip path に短絡する。これにより down 側の `voidSubscriptionDeploymentState` を呼ばない。
- **対処できない範囲 (up 側)**: `azd up` の通常 deployment polling は azd 側で起こるため、リポジトリ側からは予防できない。再現した場合は ARM 上の該当 subscription deployment が `Succeeded` か確認し、成功していれば `azd env refresh <env> --no-prompt` で env outputs を同期してから `azd up --no-prompt` を再実行する。
- **確認方法**: 一時環境の削除後、`az deployment sub list` で該当 env の deployment record が残っていないことを確認する。
- **最終確認**: 2026-05-18、`eval` 環境の `azd up` で `provision sli` が `DeploymentNotFound` を返したが、ARM 上の `eval-sli-1779095707` は `Succeeded`。`azd env refresh eval --no-prompt` 後の `azd up --no-prompt` 再実行で成功。削除不可。

### D-4. Azure Monitor SLI Metric Alert (Portal 型) の動作仕様が未公開

- **概要**: `Microsoft.Insights/metricAlerts@2024-03-01-preview` の `PromQLCriteria` で SLI を criteria にする Portal 型 alert は preview 段階で、`query` フィールドのプレースホルダー動作や alert instance のトリガー条件が公式 docs に記載されていない。
- **理由**: Azure Monitor SLI 自体が preview API (`Microsoft.Monitor/slis@2025-03-01-preview`) で動作中。Portal / Bicep / API の差分が docs に残っていない。
- **場所**: `infra/modules/azmonitor/sli-metric-alerts.bicep`、ADR-009 §Consequences
- **解消条件**: Microsoft が SLI Metric Alert を GA し、Portal / Bicep / API の動作が docs にまとまる。
- **確認方法**: SLI burn rate alert の test fire (`chaos-app` Pod を 0 replicas にして 5 分以上維持) で alert instance が記録されるかを確認する。
- **最終確認**: 2026-05-17、`Microsoft.Monitor/slis` は `2025-03-01-preview` のみ。削除不可。

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
- **確認方法**: `azd deploy api-instrumentation` 後に `uv run scripts/check-api-otel-injection.py wait-instrumentation`、`azd deploy api` 後に `uv run scripts/check-api-otel-injection.py check-injected` を実行する。`kubectl rollout restart` は既存の未注入 Pod を復旧する手段であり、通常の deploy path には使わない。
- **最終確認**: 2026-05-20、`sli-flex-test` で `Instrumentation` が `Deployment` より 5 秒遅れて作成され API Pod の `OTEL_*` が欠落。`api-instrumentation` service と deploy hook で ordering / validation を追加。

### D-8. azd preflight が `Microsoft.Chaos/targets` に reserved-word warning を誤検知

- **概要**: `azd up` / `azd provision` 実行時に以下の warning が出る。`Microsoft.Chaos/targets` の名前 `microsoft-azurekubernetesservicechaosmesh` は Azure Chaos Studio 仕様で固定であり、変更不可。
  ```
  (!) Warning: Resource "microsoft-azurekubernetesservicechaosmesh" (Microsoft.Chaos/targets) contains the reserved word "MICROSOFT"
    Azure does not allow reserved words in resource names. The deployment will fail.
  ```
- **理由**: azd の preflight (`cli/azd/pkg/infra/provisioning/bicep/bicep_provider.go` の `azureReservedResourceNameContainsMatches = ["MICROSOFT", "WINDOWS"]`) が、`azureReservedResourceNameExemptTypes` に未登録の `Microsoft.Chaos/targets` を弾く。target type 名は Chaos Studio capability binding (`urn:csci:microsoft:azureKubernetesServiceChaosMesh:*`) と一対一で固定されており、サービス仕様上の固有名。ARM 側で reserved-word ルールは適用されないため、warning に反してデプロイは成功する（false positive）。
- **実害評価**: なし。`azd` 実行ログにノイズが出るのみ。
- **場所**: `infra/modules/chaos/experiments.bicep` の `chaosTarget` リソース（コメントで根拠を明記済み）。
- **解消条件**: upstream で `Microsoft.Chaos/targets` が exempt list に追加される。
- **追跡**: [Azure/azure-dev#8239](https://github.com/Azure/azure-dev/issues/8239)
- **確認方法**: 上記 upstream issue が close されたバージョンの `azd` で `azd up` を実行し、warning が出ないことを確認する。
- **最終確認**: 2026-05-19、`azd up` で warning が出るがデプロイは成功。Chaos experiment も正常動作。

### D-7. ama-metrics `mdsd.err` で `AMACoreAgent: Connection refused` が多発（実害なし・ログノイズのみ）

- **概要**: `ama-metrics` Deployment の replica pod (`prometheus-collector` container) で `mdsd.err` に `[CreateSocket] Failed to connect port 12564 ... to AMACoreAgent: Connection refused` と `[OtlpTokenFetcher] AMACoreAgent tenant not started, trying to start it. DCR Contents: ...dcr-<otlp>...` が約 60 秒周期で継続出力される。
- **理由**: replica pod の image には `amacoreagent` バイナリが同梱されているが、replica pod 内では `AMACoreAgent` プロセスが supervisor から起動されていない。同じ image を使う `ama-logs` DaemonSet 側では `AMACoreAgent` が正常起動している。
- **実害評価**: Managed Prometheus / Container Insights / ContainerNetworkLogs / OTLP traces / logs のデータパスは正常。残る影響は `mdsd.err` のディスク消費とログノイズのみ。
- **場所**: AKS managed addon の `kube-system/ama-metrics-*` Deployment。リポジトリ側のコードでは制御不能。
- **解消条件**: Microsoft 側で `prometheus-collector` image の supervisor が replica pod でも `AMACoreAgent` を起動する、あるいは OTLP DCR 配信を replica pod 対象から除外する修正が入る。
- **確認方法**: image tag の更新後に `kubectl -n kube-system exec <ama-metrics-pod> -c prometheus-collector -- ps -ef | grep amacoreagent` と `mdsd.err` を確認する。
- **追跡**: [#130](https://github.com/torumakabe/aks-chaos-lab/issues/130)（実害なしと判定済み・closed）。
