# ADR-009: Azure Monitor SLI と Prometheus operational alerts の役割分担

## Status

Accepted — Extends ADR-004

ADR-011 は Azure Monitor SLI の正本 signal を Gateway Envoy 由来から外形 availability test 由来へ変更した。ADR-012 は正本を Azure Functions direct probe 由来へ変更し、本 ADR の「Gateway Envoy recording rules を Azure Monitor SLI 入力にする」「AKS 内 synthetic traffic で no-data を補う」部分を supersede した。ADR-014 は ADR-012 の Latency SLI を `le` bucket + `EQ` filter 方式へ amend した。

## Context

ADR-004 では Gateway Envoy メトリクスを信頼性 signal として採用した。その後、Azure Monitor Service Level Indicators (SLI) を使い、Availability / Latency SLI、Baseline、error budget、burn-rate alert を Azure Monitor 側で扱えるようになった。

一方、Managed Prometheus の短期アラートは、Chaos 実験やインシデント観測で数分以内の異常を知らせるために使う。これは SLO の error budget を評価する仕組みとは目的が異なる。

そのため、Azure Monitor SLI と Prometheus alerts を同じ「SLO アラート」として扱うと、長期の信頼性評価と短期の運用検知が混ざって読みにくくなる。

## Decision

- Azure Monitor SLI を、このラボの正規 SLO / SLI と error budget のレイヤーとして扱う。
- Managed Prometheus の短期アラートは、SLO alert ではなく operational / incident detection alert として扱う。
- Prometheus alert 名、labels、feature flag は `slo` ではなく operational alert として命名する。
- Gateway Envoy 由来の recording rules は、短期診断と operational alert の入力として維持する。
- Azure Monitor SLI の現在の正本 input は、ADR-012 と ADR-014 に従い、Azure Functions external SLI publisher が発行する external availability / latency metrics とする。
- Azure Monitor SLI definitions と SLI metric alerts は warm-up 後の `sli` layer で作成する。

## Consequences

- 長期の SLO / error budget 評価と、数分単位の operational alert を分離できる。
- Prometheus recording rules は、SLI の正本ではなく、診断と短期検知のために残す。
- Azure Monitor SLI は preview API と Service Group scope resource に依存する。必要な RBAC、Service Group cleanup、入力 metric 出現待ち、azd 旧版の回避手順は [docs/deployment.md](../deployment.md) と [docs/workarounds.md](../workarounds.md) に置く。
- SLI input / destination metrics、Gateway Envoy recording rules、external SLI publisher の観測方法は [docs/observability.md](../observability.md) に置く。
- 旧設計で検討した AKS 内 synthetic traffic と `ChaosAppNoTraffic` は履歴として扱う。現在の SLI 正本は external SLI publisher であり、AKS 内 CronJob ではない。

## Alternatives considered

- **SLI output metrics を Prometheus rule group に戻して alert 化する方式**: Azure Monitor SLI の本来の alert 経路ではなく、責務が曖昧になるため不採用。
- **Prometheus operational alert を SLO alert として扱う方式**: 短期しきい値検知と error budget 評価が混ざるため不採用。
- **Azure Monitor SLI のみで短期検知も置き換える方式**: Chaos 実験の即時観測には Managed Prometheus alerts の方が扱いやすいため不採用。
