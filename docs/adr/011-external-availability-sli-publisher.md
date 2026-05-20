# ADR-011: 外形 availability test を Azure Monitor SLI の正本にする

## Status

Superseded by [ADR-012](012-functions-direct-external-sli-probe.md) — Partially superseded ADR-009

## Context

ADR-009 では Gateway Envoy metrics と AKS 内 synthetic traffic を Azure Monitor SLI の入力にした。この構成はアプリ Pod 停止や Gateway 経路の障害には反応するが、AKS クラスタ自体が停止すると synthetic traffic generator と Envoy metrics も止まる。サービスは提供できないにもかかわらず、SLI が悪化しない silent failure になる。

Application Insights Standard availability test は AKS 外の managed synthetic source として `/health` を継続的に確認できる。一方、Azure Monitor SLI の入力は Azure Monitor Workspace 上の Prometheus metrics であり、`AppAvailabilityResults` を直接 SLI input signal にはできない。availability test を使うには、Log Analytics の結果を Prometheus good / total metrics に変換するレイヤーが必要である。

## Decision

- Azure Monitor SLI の Availability / Latency の正本を Application Insights Standard availability test にする。
- Availability test は Gateway public FQDN の `GET /health` を AKS 外から実行する。
- Azure Functions Timer Trigger の external SLI publisher を追加し、Log Analytics の `AppAvailabilityResults` を閉じた window で集計して Managed Prometheus に remote-write する。Container Apps は使わない。
- Publisher は Flex Consumption で実行し、`azd deploy external-sli-publisher` でデプロイする。Function host storage と deployment storage は managed identity 接続にし、storage account key / connection string には依存しない。publisher storage は public access を無効化し、Function App の VNet integration と blob / queue / table Private Endpoint で到達させる。デプロイ実行 principal には、公式 azd sample と同じく deployment package upload 用の Storage Blob Data Owner を付与する。
- Log Analytics API は `https://api.loganalytics.io` audience / endpoint を使う。`https://api.loganalytics.azure.com` は availability query endpoint としては利用できるが、この tenant では managed identity token resource として解決できないため使わない。
- Publisher は `now - externalSliPublisherLatenessSeconds` より前の window だけを処理し、Log Analytics ingestion latency による false bad を避ける。Timer 実行や Function host 再起動で window が欠落した場合は、`externalSliPublisherMaxCatchupWindows` を上限として古い閉じた window から順に catch-up する。
- 初回デプロイ時は `externalSliSignalNotBeforeUtc` 以降の window だけを発行し、availability test 作成前の期間を bad backfill しない。
- 分母は観測件数ではなく、`availabilityTestLocations * window 内の期待実行回数` とする。欠損した availability result は bad として total に含める。
- Availability good は `Success == true`、Latency good は `Success == true and DurationMs <= externalSliLatencyThresholdMs` とする。
- Publisher は次の per-window gauge metrics を `customdefault` namespace に出す。

| metric | 用途 |
|---|---|
| `chaos_app_external_availability_good` | Availability SLI good signal |
| `chaos_app_external_availability_total` | Availability SLI total signal |
| `chaos_app_external_latency_good` | Latency SLI good signal |
| `chaos_app_external_latency_total` | Latency SLI total signal |
| `chaos_app_external_sli_publisher_heartbeat` | publisher freshness guardrail |

- Azure Monitor SLI definitions は WindowBased ratio ではなく RequestBased good / total signal を使う。partitioning dimensions は `environment`, `service`, `test` とする。
- Metrics の値は遅延バッファ後の閉じた window から算出し、sample timestamp は publisher の実行時刻にする。Azure Monitor Workspace は `OldData` として現在から 20 分より古い timestamp を拒否するため、catch-up した複数 window は 1 回の実行時刻に合算して発行する。RequestBased SLI は good / total の合計で評価されるため、時間分布は圧縮されるが rolling period 内の分子・分母は回復できる。heartbeat metric も publisher freshness を表すため、実行時刻で発行する。
- Publisher の停止を silent failure にしないため、Managed Prometheus alert `ExternalSliPublisherHeartbeatMissing` を作成する。
- AKS 内 synthetic traffic CronJob、`ChaosAppNoTraffic`、SLI 専用 Gateway success / total / request-rate recording rules、warm-up traffic hook は廃止する。
- 既存環境に残る legacy resources は `scripts/cleanup-legacy-sli-sources.py` で dry-run 確認後に削除する。既存 SLI resources を RequestBased として再作成する必要がある場合のみ `--delete-sli-resources` を使う。

## Consequences

- **利点**: AKS クラスタ停止時も AKS 外の availability test が失敗し、サービス停止が SLI に記録される。
- **利点**: SLI 用の人工トラフィックを AKS 内で生成しないため、クラスタ停止と同時に観測 source が止まる自己参照構造を解消できる。
- **利点**: Availability と Latency の SLI が同じ外形観測点を使うため、ユーザー視点のサービスレベルに近い。
- **制約**: Availability test は public endpoint 前提である。将来 private ingress 化する場合は、別の外形実行基盤を再設計する必要がある。
- **制約**: Latency SLI は Microsoft test location から Gateway / app / Redis までの外形時間を含む。Gateway 内部の upstream latency とは意味が異なる。
- **制約**: Publisher が止まると SLI signal が stale になるため、heartbeat alert と state blob の監視が必要である。
- **制約**: Azure Monitor SLI は Prometheus metrics を入力にするため、Application Insights availability test を直接指定できない。変換レイヤーは当面維持する。

## 代替案

### 案 A: AKS 内 synthetic traffic を継続し、no-traffic alert を強化する

- 不採用理由: クラスタ停止時に traffic generator と Gateway metrics も停止する根本問題が残る。

### 案 B: App Insights availability test を Azure Monitor SLI に直接指定する

- 不採用理由: Azure Monitor SLI の input signal は Managed Prometheus metrics であり、`AppAvailabilityResults` は直接入力にできない。

### 案 C: Azure Container Apps で publisher を動かす

- 不採用理由: 本変更では承認された実行基盤ではない。既存の Python アプリ資産と azd の function host を使えるため、Azure Functions Timer Trigger を採用する。
