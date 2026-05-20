# ADR-006: Application Insights OTLP 統合とベンダー非依存 OTel 計装への移行

## Status

Accepted

## Context

アプリケーションのテレメトリは `azure-monitor-opentelemetry` ディストロを使い、Connection String で直接 Application Insights に送信していた。この構成は Azure 固有の SDK に依存し、接続先の設定がアプリケーションコードに組み込まれていた。

Microsoft は AKS 向けの OTLP ネイティブ Application Insights 統合をプレビュー公開した（[参照](https://learn.microsoft.com/azure/azure-monitor/containers/kubernetes-open-protocol)）。AKS の OTel Collector アドオンが OTLP/HTTP でテレメトリを受信し、Application Insights に転送する。Instrumentation CRD と webhook により、Pod に `OTEL_EXPORTER_OTLP_ENDPOINT` が自動注入される。

## Decision

- **Azure 固有 SDK を標準 OTel SDK + OTLP exporter に置換**: `azure-monitor-opentelemetry` を除去し、`opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http` を採用。アプリケーションコードはベンダー非依存になる
- **AKS Auto-Configuration を採用**: Instrumentation CRD（`monitor.azure.com/v1`）で Pod に OTLP エンドポイントを自動注入。`autoInstrumentationPlatforms: []`（空配列）で SDK 自動注入は使わず、手動計装を維持
- **Auto-Instrumentation は不採用**: Python の自動計装は限定プレビュー（申請制）であり、手動計装の方が `excluded_urls`・Redis 計装・カスタムメトリクスの制御に優れる
- **Connection String は Pod env から削除**: アプリコードは `OTEL_EXPORTER_OTLP_ENDPOINT`（AKS webhook が注入）のみを使用。Connection String は Instrumentation CRD の `spec.destination` に設定し、kustomize replacement で azd env var から注入
- **Instrumentation は app-specific pre-deploy unit として分離**: `Instrumentation/chaos-app-otel` は `k8s/apps/chaos-app/instrumentation/` に置き、`azd deploy api-instrumentation` で `Deployment/chaos-app` より先に適用する。クラスタ共通の `k8s/observability` には混ぜず、API deploy hook で `OTEL_EXPORTER_OTLP_*` 注入を検証する
- **Delta temporality を設定**: Application Insights OTLP は Delta temporality を要求するため、`OTLPMetricExporter` の `preferred_temporality` で全メトリクス型に `AggregationTemporality.DELTA` を指定
- **Logs シグナルも OTLP で export（issue #129 対応、2026-05-07 追加）**: アプリ側で `LoggerProvider` + `BatchLogRecordProcessor(OTLPLogExporter())` を構築し、`opentelemetry.instrumentation.logging.handler.LoggingHandler` を `logging.getLogger("app")` にのみ attach する。これにより stdlib `logging` 経由の app log が AKS App Monitoring add-on の OTLP 経路（Path B / `kubernetes-open-protocol`）を通り、LAW の **`OTelLogs` テーブル**（OTel ネイティブスキーマ）に届く（`ServiceName=chaos-app`, `ScopeName=app.main`, `Body`, `SeverityText` 等のカラムで識別）。`AppTraces` テーブル（Path A の Azure Monitor distro 経路）は使用しない。Trace context 注入（`otelTraceID` / `otelSpanID`）は `LoggingInstrumentor().instrument(set_logging_format=True, enable_log_auto_instrumentation=False)` で維持しつつ、root logger への自動 handler attach は無効化して二重送信を回避する。OTLP logs 用 endpoint guard（`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` または unified `OTEL_EXPORTER_OTLP_ENDPOINT` のいずれかが設定された場合のみ pipeline を構築）を追加し、traces のみ環境で `localhost:4318/v1/logs` への fallback による export error を防ぐ。lifespan shutdown で `shutdown_telemetry()` から `LoggerProvider.force_flush + shutdown` を呼び、SIGTERM 時の最終 log 喪失を回避する

## Prerequisites

OTLP 統合には AKS App Monitoring のプレビュー機能フラグとプロバイダーの登録が必要（サブスクリプションごとに初回のみ）。

```bash
# 1. プレビュー機能フラグを登録
az feature register --namespace Microsoft.ContainerService --name AzureMonitorAppMonitoringPreview

# 2. 登録完了を確認（"Registered" になるまで数分かかる場合がある）
az feature show --namespace Microsoft.ContainerService --name AzureMonitorAppMonitoringPreview --query properties.state

# 3. リソースプロバイダーに登録を反映
az provider register --namespace Microsoft.ContainerService

# 4. aks-preview 拡張をインストール／更新
az extension add --name aks-preview --upgrade
```

## OTLP 対応 App Insights の Bicep 構成

OTLP インジェストを有効にするには、以下の 3 リソースを Bicep で構成する:

1. **アプリテレメトリ用 Azure Monitor Workspace（AMW）**: Prometheus 用 AMW とは別に作成（`Microsoft.Monitor/accounts`）
2. **App Insights**: API バージョン `2020-02-02` で以下の OTLP プロパティを設定:
   - `AzureMonitorWorkspaceIngestionMode: 'OptedIn'`
   - `AzureMonitorWorkspaceResourceId: <app-amw-id>`
   - **重要**: 既存リソースの PATCH/PUT では OTLP を有効化できない。新規作成時のみ有効
3. **DCRA（Data Collection Rule Association）**: App Insights が自動作成するマネージド DCR（`DataCollectionRuleResourceId` プロパティから取得）を AKS クラスターに関連付け。この DCRA がないと ama-logs が OTLP DCR 構成をロードせず、ポート 4319 がリッスンしない

## CiliumNetworkPolicy の変更

OTLP 移行により、テレメトリ経路がアプリ → App Insights（直接）からアプリ → AKS OTel Collector（クラスタ内）に変わった:

- **削除**: App Insights 直接 egress ルール（`toFQDNs` の `*.in.applicationinsights.azure.com` 等）と関連 DNS allowlist
- **追加**: AKS OTel Collector Pod への egress（Pod ラベルで特定、ポート指定なし）
  - `ama-logs`（traces/logs）: `k8s:component: ama-logs-agent`
  - `ama-metrics-node`（metrics）: `k8s:dsName: ama-metrics-node`
  - ポートは AKS SKU モード（Base/Automatic）やバージョンで変わりうるため指定しない
- **維持**: `login.microsoftonline.com`（Azure Identity SDK が直接通信）
- **学び**: `toEntities: host` は hostPort DNAT トラフィックに効かない。Cilium BPF は DNAT 後の Pod-to-Pod トラフィックを見るため、実際のコレクター Pod をラベルで指定する必要がある

## マネージドリソースグループの制約

OTLP 対応 App Insights と Azure Monitor Workspace を使用すると、Azure が命名制御不可能なマネージドリソースグループを自動作成する:

| プレフィックス | 作成元 | 命名規則 | 中身 |
|---|---|---|---|
| `MA_` | Azure Monitor Workspace | `MA_<AMW名>_<リージョン>_managed` | DCR + DCE |
| `ai_` | App Insights（OTLP 有効時） | `ai_<APPI名>_<AppId>_managed` | DCR + DCE |

- ユーザー管理の 1 RG に対し、最大 4 つのマネージド RG が生成される（`MA_` × 2 + `ai_` × 1 + `MC_` × 1）
- `MC_`（AKS ノード用）は `nodeResourceGroup` パラメータで命名制御可能だが、`MA_` と `ai_` は制御不可
- `ai_` RG 内の DCR/DCE は App Insights OTLP エンドポイントが直接参照しており、削除すると OTLP パイプラインが壊れる
- `azd down` では AKS 上の DCR association を先に外さないと `ai_` RG が孤立する場合があるため、`predown` hook で `OtlpAppInsightsExtension` DCRA を削除する
- プレビュー機能の制約として受容する。GA 時に命名カスタマイズが追加される可能性はある

## Consequences

- **利点**: アプリケーションコードが完全にベンダー非依存。Azure 以外の OTel Collector にも接続可能。SDK のアップデートが OTel コミュニティのペースに追随
- **利点**: Connection String がアプリケーションの env var から消え、Instrumentation CRD に集約される。設定の関心が分離される
- **利点**: CiliumNetworkPolicy が簡素化。App Insights の FQDN パターン（5 件）が不要になり、クラスタ内 Pod 宛ルールに置換
- **リスク**: プレビュー機能のため破壊的変更の可能性がある。ラボ環境のため許容する
- **リスク**: App Insights OTLP の Bicep 型定義は `2020-02-02` でも `AzureMonitorWorkspaceIngestionMode` / `AzureMonitorWorkspaceResourceId` / `DataCollectionRuleResourceId` をまだ公開していないため、該当行で Bicep 型警告を明示的に抑制する
- **前提**: 正しいフィーチャーフラグ（`AzureMonitorAppMonitoringPreview`）の登録が必要
- **既存 ADR との関係**: ADR-003（Container Insights Custom プリセット）、ADR-004（Envoy Gateway メトリクス SLO）は影響なし。それぞれ別のテレメトリパイプラインを扱う

## 成熟度の前提とリスク

Issue #129 (OTLP logs export) 完了後 (2026-05-07)、Microsoft Learn / OpenTelemetry 公式 / Azure SDK 各種 issue に対して成熟度調査を実施した結果、以下の前提と運用上の注意を明示する。

### Path B (AKS App Monitoring add-on) は全 signals Preview

`kubernetes-open-protocol` 経由の OTLP 統合 (本 ADR で採用している経路) は traces / metrics / logs いずれも **Preview** で公開されている。`AzureMonitorAppMonitoringPreview` の登録が前提となる点は Prerequisites に記載済み。**GA タイムラインの公式アナウンスは現時点で存在しない**ため、ラボ環境としての利用は許容するが、本番 SLA を要する用途には推奨できない。

なお `azure-monitor-opentelemetry` distro (Path A) は GA 済み (v1.0.0 / 2023-09) だが、内部の `azure-monitor-opentelemetry-exporter` は依然 beta シリーズ (1.0.0bNN) であり、こちらも完全 stable ではない。Path A への切り替えで成熟度問題が解消するわけではない点に留意する。

### Python OTel Logs SDK は Development tier

OpenTelemetry Logs の仕様 (Data Model / Bridge API / SDK / OTLP Protocol) は Stable だが、**Python の logs 実装は公式 status page で "Development" tier**（Java / .NET / PHP / JS は Stable）。証跡:

- 名前空間 `opentelemetry.sdk._logs` がアンダースコア命名 (internal)
- CHANGELOG 冒頭に "Breaking changes ongoing" 警告
- 2025-11 PR #4676 で `LogData` クラスを削除 (`azure-monitor-opentelemetry-exporter` で silent ImportError 発生、Azure issue #44237)
- 2026-03 PR #4919 で `opentelemetry.sdk._logs.LoggingHandler` を deprecate (本実装は contrib `opentelemetry.instrumentation.logging.handler.LoggingHandler` に切替済み)
- トラッキング issue [open-telemetry/opentelemetry-python#3361](https://github.com/open-telemetry/opentelemetry-python/issues/3361) が依然 OPEN、target version なし

### 依存バージョンの pin 方針

上記の継続的 breaking change リスクから、`src/api/pyproject.toml` で OTel 依存を以下のように pin する:

```toml
"opentelemetry-sdk>=1.41.0,<2.0.0",
"opentelemetry-exporter-otlp-proto-http>=1.41.0,<2.0.0",
"opentelemetry-instrumentation-redis>=0.62b0,<1.0.0",
"opentelemetry-instrumentation-logging>=0.62b0,<1.0.0",
"opentelemetry-instrumentation-fastapi>=0.62b0,<1.0.0",
```

- floor は CI で動作確認済みの lock バージョン (1.41.0 / 0.62b0)
- upper bound (`<2.0.0` / `<1.0.0`) は major bump の自動取り込みを禁止
- **major だけでなく minor / beta upgrade も自動 merge 禁止**: `opentelemetry-instrumentation-*` は pre-1.0 のため minor (`0.62b0` → `0.63b0`) も breaking change を含み得る。Renovate / Dependabot で OTel group を grouped + manual review にする。実 OTLP endpoint への smoke test (LAW `OTelLogs` テーブルにアプリの `ScopeName=app.main` log が到達することを KQL で確認) を upgrade 時に実施する

### ContainerLogV2 と OTLP の重複排除

OTel logs export を有効化したことで、`chaos-lab` namespace の stdout が **ContainerLogV2 と OTLP（LAW `OTelLogs`）の両方で取得され二重課金状態**になった。これを解消するため `k8s/observability/container-azm-ms-agentconfig.yaml` で Container Insights agent を以下のように構成する:

- **stdout: `chaos-lab` を `exclude_namespaces` に追加** (OTLP が canonical 経路)
- **stderr: `chaos-lab` は除外しない** (uvicorn error / 未捕捉例外 / pre-init / post-shutdown crash / 非 `app` logger 出力は OTLP 経路では取得できない。これらは ContainerLogV2 stderr が唯一の証跡)

#### OTLP logs は ContainerLogV2 の完全代替ではない

OTLP logs pipeline は `logging.getLogger("app")` のみに `LoggingHandler` を attach する allowlist 方式 (`src/api/app/telemetry.py`)。**以下は OTLP では取得できない**ため、`chaos-lab` namespace の stderr は ContainerLogV2 で維持する判断とした:

| 取得不可能な log | 理由 |
|---|---|
| uvicorn access / error log | uvicorn の独自 logger は `app` 配下ではない |
| 未捕捉例外の traceback | Python が stderr に直接書き込む |
| telemetry 初期化前 / shutdown 後の log | `LoggerProvider` の lifecycle 外 |
| `print()` / library が直接 stdout/stderr に書き込む output | Python `logging` 経由ではない |
| third-party logger (urllib3, redis-py 等) | allowlist 外 |
| プロセス crash 時の最後の stderr | OTLP exporter の flush 前 |

#### 検証手順

apply 後、unique marker を発行して観測経路を確認する:

```bash
# 1. unique marker を app logger 経由で発行
MARKER="otlp-dedup-$(date +%s)"
kubectl -n chaos-lab exec deploy/chaos-app -- python -c "import logging; logging.getLogger('app').warning('$MARKER')"
```

```kusto
// 2. OTelLogs 側に届くこと (OTLP 経路 / Path B = kubernetes-open-protocol)
//    ※ Path B では AppTraces ではなく OTel ネイティブスキーマの OTelLogs テーブルに格納される
OTelLogs
| where TimeGenerated > ago(30m)
| where Body has "otlp-dedup-"
| project TimeGenerated, ServiceName, ScopeName, SeverityText, Body, TraceId
| order by TimeGenerated desc

// 3. ContainerLogV2 に届かないこと (chaos-lab stdout 除外確認、ConfigMap 反映に最大 ~15 分)
ContainerLogV2
| where TimeGenerated > ago(30m)
| where PodNamespace == "chaos-lab"
| where LogSource == "stdout"
| where LogMessage has "otlp-dedup-"
| summarize Count=count()  // 期待値: 0

// 4. KubePodInventory / KubeEvents は chaos-lab を引き続き取得していること
KubePodInventory | where TimeGenerated > ago(30m) | where Namespace == "chaos-lab" | summarize count() by Name
KubeEvents       | where TimeGenerated > ago(30m) | where Namespace == "chaos-lab" | summarize count()

// 5. ConfigMap parse error が出ていないこと
KubeMonAgentEvents
| where TimeGenerated > ago(2h)
| where Severity in ("Error", "Warning")
| order by TimeGenerated desc
```

ama-logs DaemonSet の rolling restart に伴い、apply 直後に短時間の OTLP ingestion gap が発生し得る。また ConfigMap の `exclude_namespaces` 設定が完全に反映されるまで **最大 ~15 分の遅延**が観測されている（eval 環境 2026-05-07 検証時）。本番投入前に必ず上記 KQL を実行する。

#### ロールバック手順

`chaos-lab` の stdout を ContainerLogV2 に戻す場合:

```bash
# k8s/observability/container-azm-ms-agentconfig.yaml の stdout exclude_namespaces から
# "chaos-lab" を削除して kustomize apply するだけ。ama-logs が自動で reload する。
```

ConfigMap 全体を削除する場合は `kubectl -n kube-system delete cm container-azm-ms-agentconfig` で AKS のデフォルト設定に戻る。
