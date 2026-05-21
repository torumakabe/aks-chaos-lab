# ADR-014: Latency SLI を histogram bucket + `EQ` filter 方式に変更する

## Status

Accepted — Amends [ADR-012](012-functions-direct-external-sli-probe.md) の Latency SLI 部分

## Context

ADR-012 では Latency SLI の good signal を Publisher 側で「HTTP 2xx かつ duration <= `externalSliLatencyThresholdMs`」と二値判定し、`chaos_app_external_latency_good` / `_total` の 2 つの gauge として発行していた。SRE 運用観点で以下の課題があった。

- **しきい値が Publisher コードに固定**: SLO threshold を変えるたびに Function App の再デプロイが必要で、SLI 定義 (Bicep) を見ても閾値が分からない。
- **しきい値変更で過去データが意味を変える**: 旧 threshold 1000ms で good=1 だった window が、threshold 変更後も good=1 のまま履歴に残り、評価期間をまたぐと SLO 数値の連続性が崩れる。
- **教材価値**: 「SLO threshold は SLI 定義で決定する」原則を学ぶ題材として、定義側で threshold を持つ方式が望ましい。

### `Microsoft.Monitor/slis@2025-03-01-preview` の DSL 仕様/実装乖離

最初は Prometheus histogram convention に揃え、`chaos_app_external_latency_seconds_bucket{le="..."}` を入力にして、SLI 定義側で `dimensionName=le` の filter をかける案を実装した (Plan A)。実機検証で OpenAPI spec が宣言する `signalSources[].filters[].operator` の wire value (`==`, `!=`, `<`, `<=`, `>`, `>=`, `@in`, `!in`, `startswith`, `!startswith`, `contains`, `!contains` および x-ms-enum SDK 名 `Equal`, `LessThanOrEqual` 等) がいずれも `MalformedStructureError` で ARM に拒否されることが判明。Plan A は preview API では実装側に到達できず provision 失敗。

そこで一度「**bucket ごとに別 metric 名で good signal を発行し、SLI 定義は metric 名でしきい値を選ぶ**」方式 (Plan B) に切り替え、`chaos_app_external_latency_good_le_<suffix>` 6 系列 + `_total` 1 系列の合計 7 系列 gauge を発行した。

### `EQ` 演算子の発見

Plan B ロックイン後、issue 提出のための network 化された operator enum 全数試験を実施した結果、ARM 実装は spec が宣言する wire value を一つも受理せず、代わりに **未文書化の PascalCase 値**を受理することが判明した。

| 試行値 | 出所 | 結果 |
| --- | --- | --- |
| `==`, `!=`, `<`, `<=`, `>`, `>=`, `@in`, `!in`, `!startswith`, `!contains` | spec wire value | ❌ `MalformedStructureError` |
| `Equal`, `LessThanOrEqual`, `GreaterThan` 等 | x-ms-enum SDK 名 | ❌ `MalformedStructureError` |
| `LT`, `LE`, `GT`, `GTE`, `LessThan`, `Less`, `LessOrEqual` 等 | 推測 | ❌ `MalformedStructureError` |
| `EQ`, `Eq`, `In`, `NotIn`, `NotContains`, `NotStartsWith`, `contains`, `startswith` | 未文書化 | ✅ accepted |

数値比較系 (`<` `<=` `>` `>=`) は **どの命名でも実装が無い**ことも判明。一方で **string 等価比較 `EQ` は動く**。Prometheus の histogram bucket 選択は `le="<threshold>"` という string equality で済むため (`<=` 演算子は不要)、`EQ` で histogram bucket SLI を組むことが可能と分かった。

この知見と仕様/実装乖離は Azure/azure-rest-api-specs に [issue #43415](https://github.com/Azure/azure-rest-api-specs/issues/43415) として報告した。

## Decision

Plan B (per-bucket metric 名) を破棄し、**`le` ラベル付き単一 good metric + SLI 側で `EQ` filter** で `le` bucket を選ぶ方式 (Plan A') に移行する。

### Metric 仕様

Publisher は Latency SLI input を以下の形式で発行する。

- `chaos_app_external_latency_total` — observation 数 (1 per window)
- `chaos_app_external_latency_good` — `duration ≤ le && 2xx` を 1、それ以外 0。
  - 各 window で bucket 境界 `[0.1, 0.25, 0.5, 1, 2, 5]` 秒それぞれに対して 1 サンプル発行
  - ラベル `le="<bucket>"` で bucket を区別 (例: `le="0.25"`)

Prometheus histogram の `_bucket` / `_sum` / `_count` 慣例には従わない。`_sum` を出さないため `histogram_quantile()` は使えないが、本リポジトリでは平均/分位点を Latency SLI の代わりに Gateway Envoy 側 internal histogram で観測するため問題ない。

`le` 以外の partitioning ラベル (`environment`, `service`, `test`) は両 metric に共通。

### SLI 定義

Latency SLI は request-based のまま、`signalSources` を以下に変更する。

- Good: metricName = `chaos_app_external_latency_good`、filter `[{ dimensionName: 'le', operator: 'EQ', values: [latencyThresholdLe] }]`
- Total: metricName = `chaos_app_external_latency_total`、filter なし

`latencyThresholdLe` は SLI 層 (`infra/sli/main.bicep`) と SLI 定義モジュール (`infra/modules/azmonitor/sli-definitions.bicep`) で `@allowed(['0.1','0.25','0.5','1','2','5'])` の string param とし、default `"1"` (= 1 秒)。しきい値変更は `azd provision sli` のみで完了する (Function App 再デプロイ不要)。

### `EQ` 演算子の使用について

`EQ` は本記述時点 (`Microsoft.Monitor/slis@2025-03-01-preview`) で OpenAPI spec / Bicep auto-gen reference / SDK のいずれにも記載がない。`Microsoft.Monitor/slis` の filter DSL 全体について Microsoft Learn 上の概念ドキュメントも存在せず、Bicep / Terraform / REST / SDK ユーザーが正解の operator 名を公式情報源だけからは決定できない。本リポジトリでは [issue #43415](https://github.com/Azure/azure-rest-api-specs/issues/43415) の対応 (spec or 実装の修正) が出るまで `EQ` をハードコードする。Bicep モジュールに ADR/issue へのコメントを残す。

### Catch-up / 欠損 window セマンティクス

ADR-012 と同じく、欠落 window は publisher 実行時刻の sample に合算する。

- 成功 probe (HTTP 2xx, duration `d`): `latency_total += 1`、各 bucket について `latency_good{le=<bucket>} += (1 if d <= bucket else 0)`
- 失敗 probe (timeout / non-2xx / network error): `latency_total += 1`、`latency_good{le=*} += 0`
- 欠損 window (Function host 停止等で probe 不可): `latency_total += 1`、`latency_good{le=*} += 0`

### Probe timeout 制約

`probe_timeout_seconds > max(LATENCY_BUCKETS) = 5s` を Publisher の Settings validation で強制する。timeout が 5s 以下だと、成功した probe が「最大 bucket より大きい duration」を取り得ず all-bucket good になり、SLI で正しい弁別ができない。

### Gauge-per-window セマンティクス

Publisher は good / total を **monotonic counter ではなく window ごとに上書きする gauge** として書き出す。1 window = 1 observation で、`temporalAggregation: Sum` により SLI engine が window 内合計を取る。Prometheus 標準の `histogram_quantile()` や `rate()` はこの metric 系列に対しては動作しない (Plan A / Plan B でも同じ制約だった)。

### Migration 戦略

旧 metric (Plan B の `chaos_app_external_latency_good_le_<suffix>` 6 系列、および ADR-012 の `chaos_app_external_latency_good` / `_total`) と新 metric を **dual-publish しない**。`azd up` の標準フロー (`deploy external-sli-publisher` → `provision sli`) では、publisher が新 metric を出してから SLI 定義が新 metric を参照する順になる。preprovision hook (`scripts/wait-for-external-sli-signals.py`) は `chaos_app_external_latency_total` と `chaos_app_external_latency_good{le="<latencyThresholdLe>"}` の出現を待つので、SLI 作成 validation は通る。

この間、Latency SLI は短時間の no-data になる。Lab 用途では許容する。旧 metric series は AMW の retention で age out させ、明示的削除はしない。

### Rollback 条件

- 新 metric が出ない、または SLI 作成が validation で失敗する場合は publisher と Bicep を Plan B (per-bucket metric 名) に revert する。
- 将来 `EQ` 受理の挙動が変わって `MalformedStructureError` を返すようになった場合も同じ revert 経路で復旧できる。

## Consequences

### Positive

- SLO threshold が Bicep (SLI 定義) で完結する。閾値変更は `azd provision sli` 1 回で済み、Function App 再デプロイ不要。
- 同じ入力を残したまま `latencyThresholdLe` を `"0.5"` `"2"` などに変えて A/B 検討できる。
- Plan B より単純: metric 名 1 個 (Plan B では 6 個)、SLI signal source 1 個、bookkeeping (Bicep の suffix derivation, publisher の per-bucket 名前生成) が消える。
- `le` ラベル convention は Prometheus histogram 慣例に近く、AMW 上で目視確認しやすい。
- ADR-009 の「Azure Monitor SLI と Prometheus operational alerts の役割分担」と整合。

### Negative

- bucket 境界は Publisher 側に固定されているため、bucket 自体の追加・削除には Publisher 再デプロイが必要 (Plan B も同じ制約)。
- `EQ` は spec / SDK / 公式 docs に未記載の演算子。upstream issue が解決するまで Bicep の値はハードコードでマジック値となる (コード内コメントと ADR で根拠を残す)。
- 旧 SLI と新 SLI の間で短時間の no-data が生じる。
- Prometheus histogram 慣例 (`_bucket{le=..}` + `_sum` + `_count` セット) ではないため、`histogram_quantile` 等汎用関数は適用できない。

## Alternatives considered

- **Plan A (オリジナル): histogram bucket + spec 通りの `<=` filter** — preview API の仕様/実装乖離で provision 失敗。今回の Plan A' は同じアイデアを `EQ` (string 等価) で実現したもの。
- **Plan B: per-bucket good metric 名 (6 系列)** — Plan A' 採用前に短期間実装。動くが metric 名 6 個は cardinality / bookkeeping / 直感的理解 の面で過剰。`EQ` 動作判明により破棄。
- **WindowBased SLI で latency 閾値を threshold として直接書く** — 仕様未確認の領域があり、現行 request-based SLI 体系との非対称性が大きい。見送り。
- **Operational alert (Prometheus alert rule) に寄せて SLI からは Availability のみ評価** — SLO の error budget 計算が片肺になり、教材価値が落ちる。見送り。
