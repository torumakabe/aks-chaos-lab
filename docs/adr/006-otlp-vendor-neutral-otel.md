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
az feature register --namespace Microsoft.ContainerService --name AKSAzureMonitorAISupportPreview

# 2. 登録完了を確認（"Registered" になるまで数分かかる場合がある）
az feature show --namespace Microsoft.ContainerService --name AKSAzureMonitorAISupportPreview --query properties.state

# 3. リソースプロバイダーに登録を反映
az provider register --namespace Microsoft.ContainerService

# 4. OTLP プロバイダーを登録
az provider register --namespace Microsoft.Monitor

# 5. aks-preview 拡張をインストール／更新
az extension add --name aks-preview --upgrade
```

## Consequences

- **利点**: アプリケーションコードが完全にベンダー非依存。Azure 以外の OTel Collector にも接続可能。SDK のアップデートが OTel コミュニティのペースに追随
- **利点**: Connection String がアプリケーションの env var から消え、Instrumentation CRD に集約される。設定の関心が分離される
- **リスク**: プレビュー機能のため破壊的変更の可能性がある。ラボ環境のため許容する
- **リスク**: CiliumNetworkPolicy の egress ルールは当面既存の直接 App Insights 向けルールを残す。AKS OTel Collector のクラスタ内アドレスが確定した後に簡素化する
- **前提**: `AKSAzureMonitorAISupportPreview` 機能フラグと `AIMonitoringOTLP` プロバイダーの登録が必要（手動・初回のみ）
- **既存 ADR との関係**: ADR-003（Container Insights Custom プリセット）、ADR-004（Envoy Gateway メトリクス SLO）は影響なし。それぞれ別のテレメトリパイプラインを扱う
