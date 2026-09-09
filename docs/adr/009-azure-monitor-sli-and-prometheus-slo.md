# ADR-009: Azure Monitor SLI と Prometheus operational alerts の役割分担

## Status

Accepted — Extends ADR-004

[ADR-011](011-external-availability-sli-publisher.md) は Azure Monitor SLI の入力を Gateway Envoy 由来から外形 availability test 由来へ変更した。ADR-011 を置き換えた [ADR-012](012-functions-direct-external-sli-probe.md) は入力を Azure Functions direct probe 由来へ変更し、本 ADR の「Gateway Envoy recording rules を Azure Monitor SLI 入力にする」「AKS 内 synthetic traffic で no-data を補う」部分を supersede した。[ADR-014](014-histogram-bucket-latency-sli.md) は ADR-012 の Latency SLI を `le` bucket + `eq` filter 方式へ amend した。

## Context

ADR-004 では Gateway Envoy メトリクスを信頼性 signal として採用した。その後、Azure Monitor Service Level Indicators (SLI) を使い、Availability / Latency SLI、Baseline、error budget、burn-rate alert を Azure Monitor 側で扱えるようになった。

一方、Managed Prometheus の短期アラートは、Kubernetes 基盤と external SLI publisher の異常検知に使う。これは SLO の error budget を評価する仕組みとは目的が異なる。

そのため、Azure Monitor SLI と Prometheus alerts を同じ「SLO アラート」として扱うと、長期の信頼性評価と短期の運用検知が混ざって読みにくくなる。

## Decision

- Azure Monitor SLI で、外形 Availability / Latency に基づく SLO / SLI と error budget を評価し、burn-rate alert で通知する。
- Managed Prometheus の短期アラートは、Kubernetes 基盤と external SLI publisher の異常を検知する operational / incident detection alert として維持する。publisher の停止は `ExternalSliPublisherHeartbeatMissing` で検知する。
- Prometheus alert 名、labels、feature flag は `slo` ではなく operational alert として命名する。
- Gateway Envoy 由来の p95 レイテンシとエラー率の recording rules は、短期診断用に維持する。Gateway 指標による高エラー率、遅延、無通信の短期アラートは廃止し、SLO 評価と通知には外形 SLI を使う。
- Azure Monitor SLI の現在の入力は、ADR-012 と ADR-014 に従い、Azure Functions external SLI publisher が発行する external availability / latency metrics とする。
- Azure Monitor SLI definitions と SLI metric alerts は warm-up 後の `sli` layer で作成する。

## Consequences

- 長期の SLO / error budget 評価と、数分単位の operational alert を分離できる。
- Gateway Envoy recording rules は短期診断に限定する。Gateway 内部の指標を使う短期通知は行わず、基盤と publisher の異常検知は Managed Prometheus alerts で継続する。
- Azure Monitor SLI は preview API と Service Group scope resource に依存する。必要な RBAC、Service Group cleanup、入力 metric 出現待ちは [docs/deployment.md](../deployment.md) と [docs/workarounds.md](../workarounds.md) に置く。
- SLI input / destination metrics、Gateway Envoy recording rules、external SLI publisher の観測方法は [docs/observability.md](../observability.md) に置く。
- 旧設計の AKS 内 synthetic traffic と `ChaosAppNoTraffic` は廃止済みであり、SLI 用の probe は AKS 外の external SLI publisher が実行する。

## Alternatives considered

- **SLI output metrics を Prometheus rule group に戻して alert 化する方式**: Azure Monitor SLI の本来の alert 経路ではなく、責務が曖昧になるため不採用。
- **Prometheus operational alert を SLO alert として扱う方式**: 短期しきい値検知と error budget 評価が混ざるため不採用。
- **基盤と publisher の異常検知も Azure Monitor SLI のみに置き換える方式**: 外形 SLI の error budget 評価と、Kubernetes 基盤や publisher 自体の異常検知は対象と目的が異なるため不採用。
