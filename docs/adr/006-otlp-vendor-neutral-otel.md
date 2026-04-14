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
- **Delta temporality を設定**: Application Insights OTLP は Delta temporality を要求するため、`OTLPMetricExporter` の `preferred_temporality` で全メトリクス型に `AggregationTemporality.DELTA` を指定

## Prerequisites

OTLP 統合にはプレビュー機能フラグとプロバイダーの登録が必要（サブスクリプションごとに初回のみ）。

```bash
# 1. プレビュー機能フラグを登録
# 注: 記事記載の AKSAzureMonitorAISupportPreview は存在しない。以下が正しい名前
az feature register --namespace Microsoft.ContainerService --name AzureMonitorAppMonitoringPreview
az feature register --namespace Microsoft.ContainerService --name AKS-OMSAppMonitoring

# 2. 登録完了を確認（"Registered" になるまで数分かかる場合がある）
az feature show --namespace Microsoft.ContainerService --name AzureMonitorAppMonitoringPreview --query properties.state
az feature show --namespace Microsoft.ContainerService --name AKS-OMSAppMonitoring --query properties.state

# 3. リソースプロバイダーに登録を反映
az provider register --namespace Microsoft.ContainerService

# 4. Insights プロバイダーの OTLP 機能フラグ
az feature register --namespace Microsoft.Insights --name OtlpApplicationInsights
az feature show --namespace Microsoft.Insights --name OtlpApplicationInsights --query properties.state
az provider register --namespace Microsoft.Insights

# 5. aks-preview 拡張をインストール／更新
az extension add --name aks-preview --upgrade
```

## OTLP 対応 App Insights の Bicep 構成

OTLP インジェストを有効にするには、以下の 3 リソースを Bicep で構成する:

1. **アプリテレメトリ用 Azure Monitor Workspace（AMW）**: Prometheus 用 AMW とは別に作成（`Microsoft.Monitor/accounts`）
2. **App Insights**: API バージョン `2025-01-23-preview`（Bicep スキーマ未収録、`#disable-next-line BCP081` が必要）で以下のプロパティを設定:
   - `AzureMonitorWorkspaceIngestionMode: 'OptedIn'`
   - `AzureMonitorWorkspaceResourceId: <app-amw-id>`
   - **重要**: 既存リソースの PATCH/PUT では OTLP を有効化できない。新規作成時のみ有効
3. **DCRA（Data Collection Rule Association）**: App Insights が自動作成するマネージド DCR（`DataCollectionRuleResourceId` プロパティから取得）を AKS クラスターに関連付け。この DCRA がないと ama-logs が OTLP DCR 構成をロードせず、ポート 4319 がリッスンしない

## CiliumNetworkPolicy の変更

OTLP 移行により、テレメトリ経路がアプリ → App Insights（直接）からアプリ → AKS OTel Collector（クラスタ内）に変わった:

- **削除**: App Insights 直接 egress ルール（`toFQDNs` の `*.in.applicationinsights.azure.com` 等）と関連 DNS allowlist
- **追加**: AKS OTel Collector Pod への egress（Pod ラベルで特定）
  - `ama-logs`（traces/logs）: `k8s:component: ama-logs-agent` → containerPort 4319
  - `ama-metrics-node`（metrics）: `k8s:dsName: ama-metrics-node` → containerPort 56681
- **維持**: `login.microsoftonline.com`（Azure Identity SDK が直接通信）
- **学び**: `toEntities: host` は hostPort DNAT トラフィックに効かない。Cilium BPF は DNAT 後の Pod-to-Pod トラフィックを見るため、実際のコレクター Pod をラベルで指定する必要がある

## Consequences

- **利点**: アプリケーションコードが完全にベンダー非依存。Azure 以外の OTel Collector にも接続可能。SDK のアップデートが OTel コミュニティのペースに追随
- **利点**: Connection String がアプリケーションの env var から消え、Instrumentation CRD に集約される。設定の関心が分離される
- **利点**: CiliumNetworkPolicy が簡素化。App Insights の FQDN パターン（5 件）が不要になり、クラスタ内 Pod 宛ルールに置換
- **リスク**: プレビュー機能のため破壊的変更の可能性がある。ラボ環境のため許容する
- **リスク**: App Insights OTLP の Bicep スキーマが未収録のため、`#disable-next-line BCP081` でプロパティを記述。GA 時にスキーマが正式対応すれば削除可能
- **前提**: 正しいフィーチャーフラグ（`AzureMonitorAppMonitoringPreview`、`AKS-OMSAppMonitoring`、`OtlpApplicationInsights`）の登録が必要。記事記載の `AKSAzureMonitorAISupportPreview` は存在しない（登録がサイレントに成功するが効果なし）
- **既存 ADR との関係**: ADR-003（Container Insights Custom プリセット）、ADR-004（Envoy Gateway メトリクス SLO）は影響なし。それぞれ別のテレメトリパイプラインを扱う
