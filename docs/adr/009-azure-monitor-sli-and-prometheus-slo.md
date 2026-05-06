# ADR-009: Azure Monitor SLI と Prometheus operational alerts の役割分担

## Status

Accepted — Extends ADR-004（Gateway 層 Envoy メトリクスを SLO/SLI signal source にする判断は維持）

## Context

Azure Monitor Service Level Indicators (SLI) は、Service Group を単位に Availability / Latency の SLI、Baseline (SLO)、error budget、fast/slow burn rate alert を提供する。これは、短期のしきい値超過を検知する仕組みではなく、サービスの信頼性目標とエラーバジェットを扱うレイヤーである。

このリポジトリでは、ADR-004 により Gateway 層 Envoy メトリクスへ信頼性 signal を統一済みである。`app-slo-recording-rules-group-*` は raw Envoy メトリクスを `gateway:chaos_app:*` に整形しており、Azure Monitor SLI の入力にも、短期 Prometheus operational alerts の入力にも使う。

一方、従来の `app-slo-alerts-*` は `gateway:chaos_app:http_request_duration:p95 > 1` と `gateway:chaos_app:http_error_rate:ratio > 0.01` の 5 分しきい値アラートだった。これは SLO/error-budget alert ではなく、短期のインシデント検知・Chaos 実験観測のための operational alert である。

Azure Monitor SLI の Bicep 型は preview として `Microsoft.Monitor/slis@2025-03-01-preview` が提供されている。Service Group は `Microsoft.Management/serviceGroups@2024-02-01-preview`、membership は `Microsoft.Relationships/serviceGroupMember@2023-09-01-preview` で作成できる。Portal が作る SLI alert は `Microsoft.Insights/metricAlerts@2024-03-01-preview` の `PromQLCriteria` として作成される。

## Decision

- Azure Monitor SLI を、このラボの正規 SLO/SLI・error budget レイヤーとして扱う。
- Managed Prometheus の短期アラートは、SLO alert ではなく operational / incident detection alert として扱う。
- `app-slo-alerts-*` は `app-operational-alerts-*` に改名する。
- `ChaosAppSLOLatencyP95High` は `ChaosAppRequestLatencyP95High` に改名する。
- `ChaosAppSLOErrorRateHigh` は `ChaosAppRequestFailureRateHigh` に改名する。
- `enablePrometheusAppSloAlerts` は `enablePrometheusAppOperationalAlerts` に改名する。
- alert labels では `slo` を使わず、`alert_type: operational`、`signal: latency-p95` / `signal: failure-rate`、`source: gateway-envoy` を使う。
- `app-slo-recording-rules-group-*` はリソース名の互換性を維持するため改名しない。ただし意味づけは「SLO 専用」ではなく、SLI source / reliability signal recording rules とする。
- Azure Monitor SLI の baseline / fast burn / slow burn alerts は、Portal 型 Metric Alert を Bicep 管理する `infra/modules/azmonitor/sli-metric-alerts.bicep` に一本化する。
- SLI output metrics を Managed Prometheus rule group に戻してアラート化する迂回構成は採用しない。これは Azure Monitor SLI の本来の alert 経路ではなく、用途を誤解して試した構成だったためである。
- Azure Monitor SLI は 1回の `azd up` で作成する。`azure.yaml` の `infra.layers` に `base` (`infra/`) と `sli` (`infra/sli/`) を分離し、`workflows.up` で `azd provision base` → `azd deploy api` → `azd deploy observability` → `azd deploy chaos-mesh` → `azd provision sli` の順序を宣言する。`observability` service の `postdeploy` hook は `scripts/warm-up-sli-signals.sh` だけを実行し、Azure リソースは作らずに traffic を流して `gateway:chaos_app:http_*` recording rules を `cluster_name` 付きで query 可能にする。
- 通常の `azd provision base` は Service Group、SLI 用 User Assigned Managed Identity、AMW/DCR RBAC、Service Group membership までを作る。SLI definitions と SLI metric alerts は warm-up 後の `azd provision sli` (`infra/sli/main.bicep`) で作る。これにより Managed Prometheus の入力メトリクス未準備で SLI 作成が走る経路が `azure.yaml` レベルで構造的に排除される。
- 親 Service Group を指定する場合は `azureMonitorSliParentServiceGroupId` を使い、その配下に環境別 Service Group を作成する。既存の環境別 Service Group を `azureMonitorSliServiceGroupResourceId` で指定した場合のみ、その Service Group を SLI scope として直接使う。
- `azd down` は resource group 外の Service Group scope resources を取りこぼす可能性があり、加えて subscription scope deployment の polling が ARM の read-after-write 不整合に晒されると `DeploymentNotFound` で偽陽性失敗する (詳細は [docs/workarounds.md §D-2](../workarounds.md#d-2-azd-の-subscription-scope-deployment-polling-が散発的に-deploymentnotfound-を返す)、upstream 観測報告 [Azure/azure-dev#8064](https://github.com/Azure/azure-dev/issues/8064))。環境削除時は `predown` hook で次の順序で **base / SLI 両 layer の Destroy 経路を graceful skip path に短絡** する: (1) Service Group scope SLI と Service Group、(2) AKS 上の OTLP Application Insights DCR association、(3) SLI layer の sub-scope deployment record (`tags.azd-layer-name=sli`)、(4) base RG (`AZURE_RESOURCE_GROUP` 出力。fallback で `rg-aks-chaos-lab-<env>`) を `az group delete --no-wait` + `az group exists` polling で同期削除、(5) base layer の sub-scope deployment record (`tags.azd-layer-name=base`) を strict に削除。これにより `azd down` 本体の `CompletedDeployments` が両 layer とも `ErrDeploymentsNotFound` を返し、`voidSubscriptionDeploymentState` が呼ばれず **down 側の polling 404 リスクが構造的に除去**される (`azd up` 側の polling 404 は azd 側で起こるためリポジトリ側からは予防できない、upstream 動向待ち)。さらに `postdown` hook で App Insights managed resource group (`ai_<appi-name>_<guid>_managed`) を `forceDeletionResourceTypes=Microsoft.Insights/dataCollectionEndpoints,Microsoft.Insights/dataCollectionRules` を指定した RG-scope DELETE で削除する。OTLP 有効化 App Insights では parent App Insights 削除時の cascade が deny assignment で必ず失敗し、managed RG が orphan として残るため (`forceDeletionResourceTypes` を指定した RG-scope DELETE は deny assignment を回避する)。`preinfradelete` hook は使わない。cleanup hook は `CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES=true` がある場合だけ削除する。`azd down <layer>` のような layer 指定 down は cleanup の対象範囲が一致しないためサポート対象外とし、project-level `azd down` のみを正規ルートとする。

## Azure Monitor SLI 作成に必要な権限

Azure Monitor SLI は Service Group scope の resource と、AMW / DCR / managed DCR への RBAC を組み合わせて動作する。base / sli の Bicep layer で再現するには、デプロイ主体と SLI 用 User Assigned Managed Identity の両方に必要な権限がそろっている必要がある。

### デプロイ主体に必要な権限

- サブスクリプション スコープで Bicep deployment を実行できる権限。
- 対象リソースグループに AKS、AMW、DCR、UAMI、Metric Alert などを作成できる権限。
- `Microsoft.Authorization/roleAssignments/write` を実行できる権限。Bicep が SLI 用 UAMI に RBAC を付与するために必要。
- Service Group を作成・更新できる権限。
- SLI を作成できる Service Group scope の権限。検証では `Microsoft.Monitor/slis/write` 不足で停止した。
- tenant root の Service Group 配下に作る場合は、tenant root Service Group に対する管理権限。別の親 Service Group を使う場合は `azureMonitorSliParentServiceGroupId` を指定し、その親 Service Group で必要な権限を持つこと。
- Service Group の親子関係は Azure RBAC のリソース ID パス継承とは別である。親 Service Group の Contributor だけでは、子 Service Group scope の `Microsoft.Monitor/slis/write` を満たせないことがある。
- 既存の環境別 Service Group を 1回デプロイの SLI scope として使う場合は、`azureMonitorSliServiceGroupResourceId` にその resource ID を指定する。親 Service Group の配下に環境別 Service Group を作る場合は `azureMonitorSliParentServiceGroupId` を指定する。

### Bicep が SLI 用 UAMI に付与する権限

- Managed Prometheus Azure Monitor Workspace:
  - Monitoring Reader
  - Monitoring Data Reader
  - Monitoring Metrics Publisher
- Prometheus pipeline Data Collection Rule:
  - Monitoring Reader
  - Monitoring Metrics Publisher
- AMW managed resource group 内の同名 Data Collection Rule:
  - Monitoring Metrics Publisher

managed resource group は `MA_<amw-name>_<region>_managed` という名前で作られる。検証では、AMW 本体だけでなく、この managed resource group 内の同名 DCR に `Monitoring Metrics Publisher` が必要だった。

## Consequences

- **利点**: SLO/SLI と短期 operational alert の責務が分かれ、Azure Monitor SLI の日・時間単位の評価モデルと、Prometheus の 5 分しきい値アラートが矛盾して見えなくなる。
- **利点**: Azure Monitor SLI をラボの主要な学習対象として扱いつつ、Chaos 実験で即時に反応する operational alert も維持できる。
- **利点**: Portal 型 SLI Metric Alerts の Bicep 管理に一本化し、SLI output metrics を Prometheus rule group に戻す迂回構成を削除できる。
- **利点**: 親 Service Group を指定して環境別 Service Group を作ることで、`eval` と同じ Service Group 構造を保ちつつ、1回の `azd up` で Azure Monitor SLI まで作成できる。
- **制約**: Prometheus recording rules は引き続き必要であり、Azure Monitor SLI だけで raw Envoy histogram / ratio 計算を完全に置き換える設計にはしない。
- **制約**: Service Group と Azure Monitor SLI は preview API を使う。クリーン環境での検証では、Service Group と SLI 定義を同じ nested deployment に置く構成が `Microsoft.Monitor/slis/write` の AuthorizationFailed になった。
- **制約**: `azd provision --preview` は、新規 Service Group 作成後に自動的に付く可能性がある Service Group scope 権限を考慮できない。preview を含めて 1回で検証するには、親 Service Group 配下に環境別 Service Group を作る構成で検証する。
- **制約**: Azure Monitor SLI 作成時 validation は、Managed Prometheus の入力 metric / dimension が実際に存在することを前提にする。クリーン環境の `azd provision` 時点では app deploy と Gateway traffic がまだないため、`gateway:chaos_app:http_*` metric の `cluster_name` dimension が存在せず `SloCreateValidateError` になり得る。
- **制約**: `azd provision` だけでは app deploy / warm-up を挟めないため、通常経路は `azd up` とする。SLI finalize の差分確認は、app deploy / warm-up 後に `infra/sli/main.bicep` を `az deployment sub what-if` で確認する。
- **整理**: `eval` 環境で成功していた理由は、対象 Service Group 直下にデプロイ主体の `Service Group Administrator` role assignment が存在していたためである。親 Service Group の Contributor だけで SLI 作成が成功していたわけではない。
- **整理**: base レイヤー (`infra/main.bicep`) と sli レイヤー (`infra/sli/main.bicep`) の責務を厳密に分割した。base は Service Group / SLI 用 UAMI / RBAC / Service Group membership / Managed Prometheus recording rules までで停止し、SLI definitions / metric alerts は warm-up 後の `azd provision sli` でのみ作成する。base 側の `enableAzureMonitorSliDefinitions` / `enableAzureMonitorSliAlerts` フラグや SLI definition 用パラメータは廃止した。sli レイヤーの `enabled` / `enableSliAlerts` だけで SLI 定義とアラートを制御する。
- **制約**: 2026-05 時点の preview API では、Azure Monitor SLI が Managed Prometheus recording rules を `customdefault` metric namespace として扱う。Bicep の既定値も `customdefault` とし、`azureMonitorSliMetricNamespace` で上書き可能にする。
- **制約**: SLI の partitioning dimension は `cluster_name` を使う。`eval` 環境と同じ既存 recording metric `gateway:chaos_app:http_success_rate:ratio` / `gateway:chaos_app:http_request_duration:p95` を Azure Monitor SLI の入力にする。1 分 window では Azure Monitor SLI の作成時 validation で partitioning dimension が query context から消えるため、SLO 用の window は 5 分にする。分単位の短期検知は SLO ではなく Managed Prometheus の operational alerts で扱う。
- **制約**: `eval` で `chaos-app` Pod を 0 replicas にした停止テストでは、SLI Availability の `downtime=1` と PromQL 条件成立を確認した。一方、Portal 型 SLI Metric Alert の alert instance は確認できなかったため、短期 operational alert の唯一の代替にはしない。
- **制約**: `sli-final` / `sli-verify` の再検証で、AKS 削除後に App Insights managed resource group 削除を試みると、managed DCR/DCE が AKS の OTLP DCR association や DCR/DCE の削除順序に阻まれて残り、DCR/DCE の直接削除も system deny assignment により失敗するケースを確認した。このため、AKS が存在する pre hook の時点で `OtlpAppInsightsExtension` DCR association を削除する。App Insights managed resource group は直接 DCR/DCE を削除せず、親 App Insights の削除または resource group 削除に任せる。
- **移行条件**: Azure Monitor SLI の alert 通知連携がラボの目的に十分であることを確認できた場合のみ、Prometheus operational alerts の既定値や通知ルーティングを再検討する。
