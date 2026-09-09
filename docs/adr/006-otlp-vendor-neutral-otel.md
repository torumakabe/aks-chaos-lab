# ADR-006: Application Insights OTLP 統合とベンダー非依存 OTel 計装への移行

## Status

Accepted

2026-05-07 に Logs signal を OTLP export 対象へ追加した。ADR-009 の cleanup 方針により、OTLP Application Insights DCR association は AKS 存在中に削除する前提が補強された。

## Context

API のテレメトリは `azure-monitor-opentelemetry` ディストロを使い、接続文字列で直接 Application Insights に送信していた。この構成では、API のテレメトリ送信処理が Azure Monitor 固有 SDK と Application Insights 接続文字列に依存する。

AKS App Monitoring は、OTLP/HTTP で受け取った traces、metrics、logs を Application Insights に転送できる。Instrumentation CRD と admission webhook を使えば、Pod に OTLP endpoint を注入できるため、アプリケーションは標準 OpenTelemetry SDK と OTLP exporter だけを知ればよい。

## Decision

- `azure-monitor-opentelemetry` を除去し、標準 OpenTelemetry SDK と OTLP HTTP exporter を使う。
- SDK の自動注入は使わず、アプリケーション側の手動計装を維持する。
- Application Insights 接続文字列の設定元は azd の `api-instrumentation` に集約し、Instrumentation CRD の `spec.destination.applicationInsightsConnectionString` に渡す。API 用の azd 生成設定（`app-config` と admission 前の Deployment マニフェストの明示 env）には `APPLICATIONINSIGHTS_CONNECTION_STRING` を設定せず、API コードからも参照しない。
- `Instrumentation/chaos-app-otel` は app-specific pre-deploy unit として `Deployment/chaos-app` より先に適用する。
- Application Insights OTLP の要件に合わせ、metrics は Delta temporality で export する。
- アプリケーション log は `logging.getLogger("app")` 配下だけを OTLP logs として export する。

API は `inject-configuration: chaos-app-otel` による標準 OTLP 環境変数の注入を利用する。AKS App Monitoring の admission webhook が Instrumentation の destination に基づいて接続文字列を Deployment の Pod template env に追加し、Pod にその値が存在することは、基盤が管理する注入の範囲に含まれる。レビューでは、この存在だけを違反とせず、API 用の生成設定への接続文字列の再導入や API コードからの参照を違反と判定する。

## Consequences

- API のテレメトリ送信処理は Azure Monitor 固有 SDK から切り離され、Azure 以外の OpenTelemetry Collector にも接続しやすくなる。他の Azure サービスを利用するための SDK は、この判断の対象外とする。
- Application Insights の接続設定は Instrumentation とそれを生成する azd 設定に集約される。API は標準 OTLP 環境変数を使い、接続文字列には依存しない。この分離は、admission 後の Pod 環境変数に接続文字列が存在しないことを保証するものではない。
- App Insights 直接 egress ルールは不要になり、CiliumNetworkPolicy は AKS OTel Collector Pod 宛に整理できる。
- AKS App Monitoring の OTLP 経路は preview のため、破壊的変更の可能性をラボ環境の制約として受け入れる。
- OTLP 対応 App Insights と Azure Monitor Workspace は Azure 管理の resource group を作る。命名や削除順序の制約は [docs/workarounds.md](../workarounds.md#b-otlp--application-insights-関連-adr-006) と [docs/deployment.md](../deployment.md) に記録する。
- chaos-app の request telemetry の保存先と診断方法は [docs/observability.md](../observability.md#アプリケーション-trace-と-request-telemetry) に置く。
- OTLP logs の対象、Python OpenTelemetry Logs SDK の成熟度、ContainerLogV2 との使い分けは [docs/observability.md](../observability.md#otlp-logs) に置く。
