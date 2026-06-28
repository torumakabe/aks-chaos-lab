# ADR-014: Latency SLI を `le` bucket と `EQ` filter で定義する

## Status

Accepted — Amends [ADR-012](012-functions-direct-external-sli-probe.md) の Latency SLI 部分

## Context

ADR-012 では、Latency SLI の good signal を external SLI publisher 側で「HTTP 2xx かつ duration <= `externalSliLatencyThresholdMs`」として判定していた。この方式では、SLO のしきい値を変えるたびに Function App の再デプロイが必要になる。また、しきい値を変更しても過去の good / total metric は旧しきい値の意味を保ったまま残るため、評価期間をまたぐ SLO 値の解釈が難しくなる。

Latency SLI のしきい値は、probe 実装ではなく SLI 定義で決める方がよい。SLI 定義にしきい値を置けば、Function App を再デプロイせずに `azd provision sli` だけで SLO 条件を変更できる。

ただし、`Microsoft.Monitor/slis@2025-03-01-preview` の filter DSL には仕様と実装の乖離がある。OpenAPI spec が宣言する比較演算子や `values` array は ARM 実装で拒否され、実機検証では未文書化の `EQ` operator と scalar `value` が必要だった。詳細は [docs/workarounds.md §A-7](../workarounds.md#a-7-latency-sli-の-dimension-filter-で未文書化演算子-eq-と未文書化スカラー-value-を使用) に記録する。

## Decision

Latency SLI は request-based のまま、external SLI publisher が `le` ラベル付きの単一 good metric を発行し、Azure Monitor SLI 側が `EQ` filter で評価対象 bucket を選択する。

- Good signal: `chaos_app_external_latency_good{le="<bucket>"}`
- Total signal: `chaos_app_external_latency_total`
- Bucket: `0.1`, `0.25`, `0.5`, `1`, `2`, `5` 秒
- 既定しきい値: `latencyThresholdLe = "1"`
- SLI filter: `dimensionName = "le"`, `operator = "EQ"`, `value = latencyThresholdLe`

Publisher は Prometheus histogram の `_bucket`, `_sum`, `_count` 形式ではなく、Azure Monitor SLI の Request-based good / total 入力として metric を発行する。運用上の metric セマンティクス、catch-up、timeout 制約、確認方法は [docs/observability.md](../observability.md#azure-monitor-sli) に置く。

## Consequences

- SLO しきい値の変更は SLI layer の Bicep 変更と `azd provision sli` で完結し、Function App の再デプロイを不要にできる。
- Publisher は bucket 境界を持つため、bucket の追加や削除には Publisher 再デプロイが必要になる。
- `EQ` と scalar `value` は未文書化の API 挙動に依存する。仕様または実装が変更された場合は、[docs/workarounds.md §A-7](../workarounds.md#a-7-latency-sli-の-dimension-filter-で未文書化演算子-eq-と未文書化スカラー-value-を使用) の解消条件に従って見直す。
- この metric は SLI 評価用であり、`histogram_quantile()` や `rate()` による汎用 Prometheus histogram 解析には使わない。分位点や平均の診断には Gateway Envoy 由来の internal histogram / recording rules を使う。

## Alternatives considered

- **Publisher 側でしきい値判定する方式**: 実装は単純だが、しきい値変更に Function App 再デプロイが必要になるため不採用。
- **bucket ごとに別 metric 名を発行する方式**: Azure Monitor SLI の filter DSL を避けられるが、metric 名と Bicep の分岐が増え、教材としても分かりにくいため不採用。
- **Window-based SLI で latency threshold を直接表す方式**: Availability と Latency の構成が非対称になり、現行の request-based SLI 体系から外れるため不採用。
