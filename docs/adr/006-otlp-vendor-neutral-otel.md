# ADR-006: Application Insights OTLP 統合とベンダー非依存 OTel 計装への移行

## Status

Accepted

2026-05-07 に Logs signal を OTLP export 対象へ追加した。ADR-009 の cleanup 方針により、OTLP Application Insights DCR association は AKS 存在中に削除する前提が補強された。

## Context

アプリケーションのテレメトリは `azure-monitor-opentelemetry` ディストロを使い、Connection String で直接 Application Insights に送信していた。この構成では、アプリケーションコードが Azure 固有 SDK と接続文字列に依存する。

AKS App Monitoring は、OTLP/HTTP で受け取った traces、metrics、logs を Application Insights に転送できる。Instrumentation CRD と admission webhook を使えば、Pod に OTLP endpoint を注入できるため、アプリケーションは標準 OpenTelemetry SDK と OTLP exporter だけを知ればよい。

## Decision

- `azure-monitor-opentelemetry` を除去し、標準 OpenTelemetry SDK と OTLP HTTP exporter を使う。
- SDK の自動注入は使わず、アプリケーション側の手動計装を維持する。
- Application Insights connection string は Pod env から外し、Instrumentation CRD の `spec.destination` に集約する。
- `Instrumentation/chaos-app-otel` は app-specific pre-deploy unit として `Deployment/chaos-app` より先に適用する。
- Application Insights OTLP の要件に合わせ、metrics は Delta temporality で export する。
- アプリケーション log は `logging.getLogger("app")` 配下だけを OTLP logs として export する。

## Consequences

- アプリケーションコードは Azure 固有 SDK から切り離され、Azure 以外の OpenTelemetry Collector にも接続しやすくなる。
- Application Insights の接続設定は Kubernetes の Instrumentation と azd env に集約され、アプリケーションの env var から connection string を除外できる。
- App Insights 直接 egress ルールは不要になり、CiliumNetworkPolicy は AKS OTel Collector Pod 宛に整理できる。
- AKS App Monitoring の OTLP 経路は preview のため、破壊的変更の可能性をラボ環境の制約として受け入れる。
- OTLP 対応 App Insights と Azure Monitor Workspace は Azure 管理の resource group を作る。命名や削除順序の制約は [docs/workarounds.md](../workarounds.md#b-otlp--application-insights-関連-adr-006) と [docs/deployment.md](../deployment.md) に記録する。
- OTLP logs の対象、Python OpenTelemetry Logs SDK の成熟度、ContainerLogV2 との使い分けは [docs/observability.md](../observability.md#otlp-logs) に置く。
