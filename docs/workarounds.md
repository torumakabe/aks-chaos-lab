# ワークアラウンド棚卸し

このリポジトリで継続中のワークアラウンドと、それぞれの **解消条件** をまとめる。`review-repo` エージェントの定期チェック対象。GA や仕様改善で不要になったものは剥がす。

各エントリは以下の構造を持つ:

- **概要**: 何をしているか
- **理由**: なぜこのワークアラウンドが必要か
- **場所**: コード / ドキュメント上の参照
- **解消条件**: どうなったら剥がせるか
- **確認方法**: 解消されたかを確認する手順

---

## A. Azure Monitor SLI 関連

### A-1. `infra.layers` で base/sli を分離し、warm-up を挟む

- **概要**: `azd up` の workflow を `provision base` → `deploy api` → `deploy observability` → `deploy chaos-mesh` → `provision sli` に分割し、`observability.postdeploy` で warm-up traffic を流してから SLI を作成する。
- **理由**: Azure Monitor SLI は作成時点で Managed Prometheus recording rule 出力（`cluster_name` dimension 付き）を要求する。メトリクス materialize 前に SLI を作ると validation で失敗する。
- **場所**: `azure.yaml`、`infra/sli/main.bicep`、`docs/adr/009-azure-monitor-sli-and-prometheus-slo.md`
- **解消条件**: SLI が「将来生成されるメトリクス」を前提にした作成を許容する API になる。
- **確認方法**: 一時的に `infra.layers` を統合し、warm-up なしで `infra/sli/main.bicep` を `azd provision base` の中で同時に作成 → SLI が作成エラーにならないか試す。

### A-2. `scripts/warm-up-sli-signals.sh` で 180 秒のトラフィック生成と recording rule 出力待機

- **概要**: `observability` service の `postdeploy` hook で、Envoy 経由のトラフィックを生成し、Managed Prometheus 上で `gateway:chaos_app:http_*` recording rule の出力に `cluster_name` dimension が出るまで待つ。
- **理由**: A-1 と同じ。SLI 作成前に recording rule の出力が materialize されている必要があるため。
- **場所**: `scripts/warm-up-sli-signals.sh`、`azure.yaml:46-54`
- **解消条件**: A-1 と同じ。
- **確認方法**: A-1 と同じ。

### A-3. AMW managed resource group 内 DCR への `Monitoring Metrics Publisher` 付与

- **概要**: `MA_<amw-name>_<region>_managed` リソースグループ内の AMW と同名 DCR に対して、SLI 用 UAMI に `Monitoring Metrics Publisher` を付与する。
- **理由**: AMW 本体への RBAC だけでは SLI の storage location validation を通らない（Microsoft Learn 未記載の挙動）。
- **場所**: `infra/modules/azmonitor/sli-managed-dcr-rbac.bicep`、ADR-009 §RBAC
- **解消条件**: Microsoft 側で AMW 本体への RBAC だけで SLI が作れるよう挙動が修正される、または公式に managed RG への RBAC が必要だと文書化され、別の方法（policy / built-in role）が用意される。
- **確認方法**: managed DCR への role assignment を一時的に外し、SLI 作成が通るか試す。Microsoft Learn の AMW SLI 関連ページに記載が追加されたか確認する。

### A-4. `predown` hook で Service Group scope SLI と環境別 Service Group を削除

- **概要**: `azd down` 前に `scripts/cleanup-azure-monitor-sli-resources.sh pre` を実行し、Service Group scope の SLI と環境別 Service Group を削除する。
- **理由**: `azd down` は subscription scope までしか到達せず、tenant scope（Service Group / SLI）が残留する。
- **場所**: `scripts/cleanup-azure-monitor-sli-resources.sh:37-66`、`azure.yaml:21-25`、ADR-009 line 32
- **解消条件**: `azd` が tenant scope のリソースを `down` で cascade delete できるようになる、または Service Group が subscription scope のリソースとして提供される。
- **確認方法**: hook を一時的に無効化して `azd down` 後、Service Group / SLI が残らないか確認する。

### A-5. 環境別 Service Group への `Service Group Administrator` 直付与が必要

- **概要**: 親 Service Group の Contributor では子 Service Group の `Microsoft.Monitor/slis/write` が継承されないため、環境別 Service Group に対して直接 `Service Group Administrator` を付与する。
- **理由**: Service Group の親子関係は Azure RBAC のリソース ID パス継承と独立している。
- **場所**: README §必要なロール §2、ADR-009 §RBAC
- **解消条件**: Service Group RBAC が Azure RBAC の path 継承に揃う、または親 Service Group からの継承で SLI 作成が許可される。
- **確認方法**: テスト用テナントで親 Service Group の Contributor のみを付与し、子 Service Group での SLI 作成が通るか確認する。

### A-6. 初回 `azd up` で `AZD_DEPLOY_TIMEOUT=3600` を推奨

- **概要**: 初回 `azd up`（特に `azd provision sli` 段階）で環境変数 `AZD_DEPLOY_TIMEOUT=3600`（60 分）を設定する。
- **理由**: 新規 AMW では Managed Prometheus recording rule の cold-start に最大 ~32 分かかる実測値があり、デフォルト `AZD_DEPLOY_TIMEOUT=1200s`（20 分）では `provision sli` 段階で recording rule 出力待機がタイムアウトする。`scripts/warm-up-sli-signals.sh` の内部待機もこの timeout の影響を受ける。
- **場所**: README §環境構築、`scripts/warm-up-sli-signals.sh`
- **解消条件**: AMW の recording rule cold-start が短縮される（数分以内）、または SLI が「将来生成されるメトリクス」を許容する API になる（A-1 と同じ条件）。
- **確認方法**: `AZD_DEPLOY_TIMEOUT` 未設定で初回 `azd up` を実行し、20 分以内に `provision sli` が成功するか確認する。

---

## B. OTLP / Application Insights 関連 (ADR-006)

### B-1. App Insights を `AzureMonitorWorkspaceIngestionMode=OptedIn` で新規作成

- **概要**: Application Insights を AMW 紐付けの OTLP モードで作る。preview API `Microsoft.Insights/components@2025-01-23-preview` を使用。
- **理由**: GA API ではこのプロパティを設定できず、既存リソースの PATCH でも変更できない（新規作成のみ）。
- **場所**: `infra/modules/azmonitor/core.bicep:36-51`、ADR-006
- **解消条件**: OTLP App Insights / `AzureMonitorWorkspaceIngestionMode` が GA API に降りる。
- **確認方法**: GA API での `Microsoft.Insights/components` schema を確認し、`AzureMonitorWorkspaceIngestionMode` が含まれるか確認する。Bicep schema CLI で `az bicep build` に warning が出ないかチェック。

### B-2. `predown` hook で AKS 上の `OtlpAppInsightsExtension` DCRA を先に削除

- **概要**: `azd down` 前に AKS の OTLP DCR association を削除する。
- **理由**: AKS が削除されると DCRA も自動消滅するが、その後 App Insights を削除しても managed DCR/DCE は deny assignment で残る。pre 段階で DCRA を明示的に消しておくことで、後段の `postdown` force-delete (B-3) が AKS 残骸の影響を受けない defense-in-depth を提供する。
- **場所**: `scripts/cleanup-azure-monitor-sli-resources.sh:102-160`、ADR-009 line 78
- **解消条件**: B-3 と同じ。
- **確認方法**: hook を一時的に無効化して `azd down` を実行し、`postdown` の force-delete のみで `ai_*` managed RG が削除されるか確認する。

### B-3. `postdown` hook で App Insights managed resource group (`ai_*`) を `forceDeletionResourceTypes` で削除

- **概要**: `azd down` 後に `scripts/cleanup-azure-monitor-sli-resources.sh post` を実行し、ARM の `forceDeletionResourceTypes` クエリパラメータ (`Microsoft.Insights/dataCollectionEndpoints,Microsoft.Insights/dataCollectionRules`) を指定して `ai_<appi-name>_<guid>_managed` RG を直接削除する。
- **理由**: OTLP 有効化された App Insights は managed RG 内に DCR/DCE と system-protected deny assignment を作る。parent App Insights 削除時の cascade は DCE 削除時点で deny assignment に阻まれて失敗し、managed RG が orphan として残留する。`forceDeletionResourceTypes` を指定した RG-scope DELETE は、内部リソースの個別 DELETE 権限を要求せず、deny assignment を回避できる（[Microsoft Learn: Managed workspaces in Application Insights — Deny assignments don't prevent deletion of the resource group](https://learn.microsoft.com/azure/azure-monitor/app/managed-workspaces) の挙動が初めて発動する）。`azd-env-name` タグで環境別に絞り込み、他環境の managed RG を巻き込まない。
- **場所**: `scripts/cleanup-azure-monitor-sli-resources.sh:162-221`、`azure.yaml:26-30`、ADR-009
- **解消条件**: OTLP App Insights の deprovision flow が改善され、parent App Insights 削除で managed RG が確実に cascade 削除されるようになる。
- **確認方法**: postdown hook を一時的に無効化して `azd down` を実行し、`ai_<appi-name>_*_managed` RG が自動的に消えるか確認する。`Microsoft Learn` の OTLP App Insights 関連ページで cascade 改善の記載が追加されたか確認する。

### B-4. 記事記載の `AKSAzureMonitorAISupportPreview` は使わず別の 3 flag を登録

- **概要**: 公式記事（[OTLP App Insights enrollment](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-otlp)）が記載する `AKSAzureMonitorAISupportPreview` は実在しない。代わりに `AzureMonitorAppMonitoringPreview` + `AKS-OMSAppMonitoring` + `OtlpApplicationInsights` の 3 つを登録する。
- **理由**: `AKSAzureMonitorAISupportPreview` の登録はサイレントに成功するが効果がない。
- **場所**: README §プレビュー機能とリソースプロバイダー登録、ADR-006 line 92
- **解消条件**: Microsoft が記事を訂正する、または該当機能 GA で全 flag 不要になる。
- **確認方法**: Microsoft Learn の OTLP App Insights enrollment ページで feature flag 名が正されたか確認する。

---

## C. プレビュー機能 / Preview API バージョン

### C-1. 4 つの feature flag を `az feature register` 必須

- **概要**: `AKS-AddonAutoscalingPreview`, `AzureMonitorAppMonitoringPreview`, `AKS-OMSAppMonitoring`, `OtlpApplicationInsights` をサブスクリプション単位で事前登録する。
- **理由**: プレビュー機能のため明示登録が必要。
- **場所**: README §プレビュー機能とリソースプロバイダー登録
- **解消条件**: 各機能が GA し、feature flag 登録が不要になる。
- **確認方法**: `review-repo` エージェントの §7（プレビュー機能 / リソースプロバイダー登録の鮮度）参照。

### C-2. AKS / Fleet の preview API バージョンを継続使用

- **概要**: `Microsoft.ContainerService/managedClusters@2026-01-02-preview`、`Microsoft.ContainerService/fleets@2025-04-01-preview` などを使用。
- **理由**: Gateway API (App Routing Istio) や Blue-green node OS upgrade が GA API にない。
- **場所**: `infra/modules/aks.bicep:225,298,320` (`TODO: Migrate to GA API version when available`)、`infra/modules/fleet.bicep`
- **解消条件**: 必要機能を含む GA API バージョンが提供される。
- **確認方法**: [Bicep / ARM 仕様 (`Microsoft.ContainerService/managedClusters`)](https://learn.microsoft.com/azure/templates/microsoft.containerservice/managedclusters?pivots=deployment-language-bicep) の latest GA バージョンに、`gatewayApi` / `nodeOSUpgradeChannel` / Blue-green 関連プロパティが揃ったか確認する。`bicep-api-version-updater` スキルでも検出可。

### C-3. Azure Monitor 系 managed resource group 命名は制御不可

- **概要**: AMW (`MA_<amw>_<region>_managed`)、App Insights (`ai_*`) の managed RG 名は固定パターンで命名できない。AKS の `MC_*` のみ `nodeResourceGroup` で制御可能。
- **理由**: Azure 側の仕様。
- **場所**: ADR-006:76-80
- **解消条件**: Azure 側で命名 API が提供される。
- **確認方法**: Microsoft Learn の AMW / App Insights ドキュメントで命名カスタマイズの記述が追加されたか確認する。

---

## D. 既知の遅延 / 制約

### D-1. node exporter メトリクスが最大 24 時間遅延する

- **概要**: AKS / Container Insights 環境作成直後、node 関連メトリクス（CPU、memory、network 等）が即座に採取されない。最大 24 時間で揃う。
- **理由**: node exporter のインストール優先度が他のタスクより低い（[Azure/prometheus-collector#483](https://github.com/Azure/prometheus-collector/issues/483)）。
- **場所**: README:281
- **解消条件**: upstream issue が解決される。
- **確認方法**: issue #483 のステータスを確認。`review-repo` 実行時にコメントを覗く。

### D-2. azd の subscription scope deployment polling が散発的に `DeploymentNotFound` を返す

- **概要**: `azd up` / `azd down` の subscription scope deployment 操作 (`BeginCreateOrUpdateAtSubscriptionScope`) で `Deployment '<env>-<layer>?-<unix>' could not be found.` が返って失敗することがある。同名 deployment record は ARM 上で `Succeeded` 状態で残っているケースが直接観測されている (詳細は **観測ログ** 参照)。
- **理由**: 未確定。ARM の deployment record が一時的に GET で見えないウィンドウ (read-after-write inconsistency) があるように見えるが、根本原因は未特定。観測事実の報告として upstream issue [Azure/azure-dev#8064](https://github.com/Azure/azure-dev/issues/8064) を起票済み。
- **発生 phase**:
  1. **`azd down` の void POST**: PR #6267 以降、`bicep_provider.go:Destroy()` は `groupedResources == 0` でも `voidSubscriptionDeploymentState` を呼ぶようになった。layer ごとに 1 回ずつ POST → poll が走るため、`infra.layers` 機能を使うプロジェクトでは発生機会が倍になる。
  2. **`azd up` の通常 deployment**: void POST 限定ではない。本リポジトリでは `azd up` の SLI provision (`provision sli` step) でも再現しており、ARM 上 28 秒で `Succeeded` のところ azd 側が `DeploymentNotFound` を報告した。
- **観測ログ** (識別情報マスク済の証跡は upstream issue 添付 zip を参照):
  - down 時 void POST の `Succeeded` 証跡: `azd down` が `DeploymentNotFound` を返した直後に `az deployment sub show --name <name>` で `provisioningState: Succeeded` を確認。
  - up 時 SLI provision の `Succeeded` 証跡: 同様に `provisioningState: Succeeded`, `duration: PT28.4735574S` を確認。
  - 両方とも ARM 上の `correlationId` が azd 側の `TraceID` と一致。
- **構造的対処 (down 側のみ・実装済み)**: `predown` hook が呼ぶ `scripts/cleanup-azure-monitor-sli-resources.sh pre` で、`azd down` の Destroy 経路を **両 layer で graceful skip path に短絡**することで `voidSubscriptionDeploymentState` を呼ばないようにする。これにより **down 側の polling 404 リスクは構造的に除去**される。具体的な順序:
  1. **SLI layer**: Service Group scope SLI / Service Group / OTLP DCRA を個別に削除した後、SLI layer の sub-scope deployment record (`tags.azd-env-name=<env>` AND `tags.azd-layer-name=sli`) を削除。
  2. **base layer**: base RG (`AZURE_RESOURCE_GROUP` 出力。fallback で `rg-aks-chaos-lab-<env>`) を `az group delete --no-wait` で削除し、`az group exists` で `False` になるまで polling 同期。AMW / App Insights の managed RG はこの RG-scope DELETE の cascade で削除される。
  3. **base layer record**: 上記完了後に base layer の sub-scope deployment record (`tags.azd-env-name=<env>` AND `tags.azd-layer-name=base`) を **strict** (失敗で exit 1) に削除。
- **`azd down` の挙動**: 両 layer の `CompletedDeployments` が `ErrDeploymentsNotFound` を返すため、`down.go` が graceful path ("No Azure resources were found." 表示) に分岐し、`voidSubscriptionDeploymentState` 自体が呼ばれない。
- **`postdown` の責務**: 上記 cascade で managed RG が削除されない (deny assignment) ケース向けに、`postdown` で `forceDeletionResourceTypes` を指定した RG-scope DELETE を維持する。
- **対処できない範囲 (up 側)**: `azd up` の通常 deployment polling は azd 側で起こるため、リポジトリ側からは予防できない。再現した場合は `azd up` を再実行する (deployment は ARM 上は `Succeeded` で残るため、再実行時は incremental で問題なく続行できる)。
- **観測パターン (環境クリーンアップ後の検証)**:
  ```bash
  az deployment sub list --query "[?tags.\"azd-env-name\"=='<env>'] | reverse(sort_by(@, &properties.timestamp)) | [].{name:name,layer:tags.\"azd-layer-name\",reason:tags.\"azd-deploy-reason\",state:properties.provisioningState,ts:properties.timestamp}" -o table
  ```
- **アップストリーム**: 観測報告として [Azure/azure-dev#8064](https://github.com/Azure/azure-dev/issues/8064) を起票済 (識別情報マスク済の azd output / ARM record を添付)。関連する近接 issue: [Azure/azure-dev#6207](https://github.com/Azure/azure-dev/issues/6207) (PR #6267 で「resources が手動削除済みでも void state する」修正、down 側のみ対応) と [Azure/azure-dev#7603](https://github.com/Azure/azure-dev/pull/7603) (Destroy 経路の大改修、closed without merge)。
### D-3. Application Insights `requests/duration` の P95 / P99 が trace sampling の影響を受ける

- **概要**: App Insights Portal で表示される `requests/duration` の P95 / P99 は trace sampling 後のサブセットから集計されるため、本リポジトリの既定 sampling rate (`OTEL_TRACES_SAMPLER_ARG=0.1`) のように低いとサンプル数不足で値がぶれる。レアな遅延が取りこぼされる。
- **理由**: Azure Monitor exporter は traces から requests metric を再構築する経路があり、Microsoft Learn にも明記されていない。
- **場所**: `src/app/telemetry.py` (`ErrorAwareSampler` で chaos / error 経路は常時 sample)、README `## 🔭 可観測性`。
- **解消条件**: SLI / SLO の latency 一次信号は **Managed Prometheus の Envoy histogram** 由来 recording rule (`gateway:chaos_app:http_request_duration:p95`) を使う方針なので、本リポジトリでは構造的に解消される。App Insights P95 はあくまで参考値。
- **確認方法**: AMW recording rule の P95 と App Insights P95 を同一時間軸で比較し、系統的な差分があるかを確認。

### D-4. Azure Monitor SLI Metric Alert (Portal 型) の動作仕様が未公開

- **概要**: `Microsoft.Insights/metricAlerts@2024-03-01-preview` の `PromQLCriteria` で SLI を criteria にする Portal 型 alert は preview 段階で、`query` フィールドのプレースホルダー動作 (例: `'up'`) や alert instance のトリガー条件が公式 docs に記載されていない。本リポジトリでは [`infra/modules/azmonitor/sli-metric-alerts.bicep`](../infra/modules/azmonitor/sli-metric-alerts.bicep) で baseline / fast burn / slow burn alert を作成しているが、preview 期間中は alert instance 観測が不安定。
- **理由**: Azure Monitor SLI 自体が preview API (`Microsoft.Monitor/slis@2025-03-01-preview`) で動作中。Portal / Bicep / API の差分が docs に残っていない。
- **場所**: `infra/modules/azmonitor/sli-metric-alerts.bicep`、ADR-009 §Consequences。
- **解消条件**: Microsoft が SLI Metric Alert を GA し、Portal / Bicep / API の動作が docs にまとまる。
- **確認方法**: SLI burn rate alert の test fire (`chaos-app` Pod を 0 replicas にして 5 分以上維持) で alert instance が記録されるかを確認。短期 operational alert (`ChaosAppRequestFailureRateHigh` / `ChaosAppNoTraffic`) を SLI alert の代替として併用する。

### D-5. OpenTelemetry UpDownCounter `http.server.active_requests` の Pod 再起動時ドリフト

- **概要**: FastAPI / OTel auto-instrumentation が emit する `http.server.active_requests` は UpDownCounter (DELTA aggregation) で per-process state を持つ。Chaos 実験で Pod 再起動が頻発する環境では、`rate()` / `delta()` で集計した時に負値や jitter が出る。
- **理由**: UpDownCounter は process-local な state を保持し、Pod restart で reset されるが、Azure Monitor / Prometheus 側の集計クエリは Cumulative 前提で、reset を補正する仕組みがない (OTel SDK 仕様)。
- **場所**: README `## 🔭 可観測性` 注記、`src/app/telemetry.py` の auto-instrumentation 部分。
- **解消条件**: 本リポジトリでは構造的には解消できない (SDK 仕様)。負荷状態の一次信号は Envoy 経由の `gateway:chaos_app:http_request_rate` recording rule を使う運用で迂回する。
- **確認方法**: README に「`active_requests` を alert の基準にしない」旨を明記し、`gateway:chaos_app:http_request_rate` を使う運用を維持。
- **補足 (2026-05-07 issue #128 対応)**: 上記 D-5 はドリフトを前提に書いているが、実機検証では **ノートラフィック時に `active_requests` 自体が AMW Prometheus に届かない** 事象が発覚した (eval 環境)。原因は **synchronous UpDownCounter が `add()` の呼ばれない export interval ではデータポイントを emit しない仕様** (FastAPIInstrumentor は ASGI middleware 経由で正しく counter を作成しているが、kubelet probe による `/health` だけが流れるノートラフィック環境では `excluded_urls="health"` で除外され、結果として `add()` が一度も呼ばれない)。対応として、ノートラフィック耐性のあるアプリ独自 metric `chaos_app.active_requests` (ObservableGauge、`/health` 除外) を [`src/app/telemetry.py`](../src/app/telemetry.py) と [`src/app/main.py`](../src/app/main.py) の HTTP middleware で実装し、callback 経由で毎 export interval 現在値 (通常 0) を emit するよう変更した。標準 `http.server.active_requests` (FastAPIInstrumentor) は Pod 再起動時のドリフトと "no-traffic で series 不出現" の両方を抱えるため、SLO/alert/dashboard では `chaos_app.active_requests` を使うこと。

### D-6. OTLP logs (`OTelLogs`) がアプリから export されていない（**解消済み 2026-05-07 / #129**）

- **概要**: アプリ側で OTel logs の export pipeline を setup しておらず、AKS App Monitoring add-on (Path B / `kubernetes-open-protocol`) 経由の OTLP logs が LAW の `OTelLogs` テーブルに 1 件も届かなかった（eval 環境 2026-05-07 当初検証時点で 0 件）。なお Path B では `AppTraces` ではなく OTel ネイティブスキーマの `OTelLogs` テーブルにログが格納される（カラム: `Body`, `SeverityText`, `ServiceName`, `ScopeName`, `TraceId`, `SpanId` 等）。
- **解消対応 (#129)**: `src/app/telemetry.py` に `LoggerProvider` + `BatchLogRecordProcessor(OTLPLogExporter())` を追加し、`opentelemetry.instrumentation.logging.handler.LoggingHandler` を `logging.getLogger("app")` のみに attach する設計で実装。`LoggingInstrumentor` は `enable_log_auto_instrumentation=False` で起動し trace context 注入のみ有効化（root logger への自動 attach による二重送信を回避）。`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` または unified endpoint が設定されている場合のみ pipeline を構築する logs 専用 guard を追加し、traces-only 環境での `localhost` fallback を防止。lifespan shutdown で `shutdown_telemetry()` を呼び `force_flush + shutdown` を確実に行う。詳細は [#129](https://github.com/torumakabe/aks-chaos-lab/issues/129) と ADR-006 を参照。
- **重複排除 (2026-05-07)**: OTLP 経由で `OTelLogs` に流れる app logger log が ContainerLogV2 でも収集される二重課金状態を解消するため、`k8s/observability/container-azm-ms-agentconfig.yaml` を追加。stdout の `exclude_namespaces` に `chaos-lab` を含め、OTLP を canonical 経路にする。**stderr は除外しない** — uvicorn error / 未捕捉例外 / pre-init crash 等は OTLP の `app` logger scope 外であり、ContainerLogV2 stderr が唯一の証跡となるため。ConfigMap 反映には ama-logs DaemonSet の rolling restart 後さらに **最大 ~15 分の遅延**が観測されている (eval 環境)。詳細は [ADR-006 §"成熟度の前提とリスク"](adr/006-otlp-vendor-neutral-otel.md#成熟度の前提とリスク) を参照。
- **成熟度の前提**: Python OTel Logs SDK は公式 status で **Development** tier (Java/.NET/PHP/JS は Stable)、`opentelemetry.sdk._logs` は internal API として breaking change が継続中 (2025-11 `LogData` 削除、2026-03 SDK `LoggingHandler` deprecate)。`src/pyproject.toml` で OTel 依存を `>=1.41.0,<2.0.0` / `>=0.62b0,<1.0.0` に pin し、major bump および pre-1.0 minor/beta bump の自動 merge を禁止する運用とする。
- **確認方法**: deploy 後、LAW `OTelLogs` テーブルに app logger 経由 (`app.main` / `app.telemetry` / `app.redis_client`) の log が `ServiceName=chaos-app`, `ScopeName=app.*` で届くことを確認する。重複排除の検証は ADR-006 の marker-based KQL を使用する。

### D-7. ama-metrics `mdsd.err` で `AMACoreAgent: Connection refused` が多発

- **概要**: `kubectl -n kube-system logs <ama-metrics-pod> -c prometheus-collector` で `mdsd.err` ファイルへの `[CreateSocket] Failed to connect port 12564 socketId: ConfigID to AMACoreAgent: Connection refused` および `[OtlpTokenFetcher] AMACoreAgent tenant not started, trying to start it` が継続的に出力される。
- **理由**: ama-metrics pod 内の `prometheus-collector` container と `mdsd` core process 間の Unix socket 通信が起動順序または image 不整合で失敗している可能性。AKS App Monitoring は preview 段階で image (`ciprod`, `prometheus-collector`) が頻繁に更新される。
- **場所**: AKS の `kube-system/ama-metrics-*` daemon set / deployment。リポジトリ側のコードでは制御不能。
- **解消条件**: Microsoft 側で image 更新により解消される可能性が高い。アプリ側 OTLP traces (`OTelSpans` 19,713 件) や App Insights `dependencies` (15 分間で 78 件) は届いており、実害は限定的。詳細と追跡は [#130](https://github.com/torumakabe/aks-chaos-lab/issues/130) で記録。
- **確認方法**: ama-metrics pod の image tag を定期的に確認し、新 version で `mdsd.err` のエラー件数が減少するかを監視。

