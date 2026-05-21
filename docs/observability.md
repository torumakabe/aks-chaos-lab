# 可観測性ガイド

このドキュメントは、AKS Chaos Lab の観測シグナルと運用時の見方をまとめます。採用理由や却下した案は [ADR 一覧](adr/INDEX.md) を参照してください。

## 可観測性の構成

| 領域 | 役割 | 主なファイル / 参照 |
|------|------|---------------------|
| Application Insights | アプリの traces / metrics / logs と Function probe の dependency telemetry を受信 | `src/api/app/telemetry.py`, `src/external-sli-publisher/`, [ADR-006](adr/006-otlp-vendor-neutral-otel.md), [ADR-012](adr/012-functions-direct-external-sli-probe.md) |
| External SLI publisher | Azure Functions から `GET /` を probe し、Azure Monitor SLI 用 Prometheus good / total metrics を発行 | `src/external-sli-publisher/`, `infra/modules/functions/`, [ADR-012](adr/012-functions-direct-external-sli-probe.md) |
| Azure Monitor managed Prometheus | Platform signals、Gateway diagnostic recording rules、external SLI metrics、alerts | `infra/modules/prometheus/`, [ADR-004](adr/004-envoy-gateway-metrics-for-slo.md) |
| Azure Monitor SLI | 外形 Availability / Latency SLI definitions と burn-rate alerts | `infra/sli/`, [ADR-012](adr/012-functions-direct-external-sli-probe.md) |
| Container Insights | コンテナログ / メトリクスを Log Analytics に収集 | `k8s/observability/`, [ADR-003](adr/003-container-insights-custom-preset.md) |
| Container network logs | ACNS + Cilium の eBPF network flow logs | `k8s/observability/container-network-log.yaml`, [ADR-002](adr/002-container-network-logs.md) |
| Cilium L7 HTTP metrics | L7 policy 適用時の HTTP request metrics | `k8s/components/cilium-ingress-l7/`, [ADR-007](adr/007-acns-l7-observability.md) |

Grafana ダッシュボードは Azure Portal の対象 AKS > Monitoring > Dashboards with Grafana から参照できます。

## アプリ信頼性 signal

Azure Monitor SLI の Availability / Latency は、AKS 外で動作する Azure Functions external SLI publisher の `GET /` probe を正本にします。

Publisher は Flex Consumption 上の Timer Trigger で動作し、Gateway の public FQDN へ `GET /` を実行して、結果を Managed Prometheus に remote-write します。Function host / Timer 停止で欠落した window は過去状態を再プローブできないため、`0/1` の bad sample として扱います。Function host storage と deployment storage は managed identity 接続です。publisher storage は public access を無効化し、Function App の VNet integration と blob / queue / table Private Endpoint で到達します。

Application Insights の role name は Function host 名ではなく `external-sli-publisher` に固定します。Functions host は `telemetryMode: OpenTelemetry` と `OTEL_SERVICE_NAME`、Python worker は `service.name` resource attribute で同じ role name に揃えます。個別インスタンスは role instance 側で識別します。

| metric | 意味 |
|---|---|
| `chaos_app_external_availability_good` | probe が HTTP 2xx で完了した window 数 |
| `chaos_app_external_availability_total` | probe window 数。欠損 window も total に含める |
| `chaos_app_external_latency_total` | Latency probe の window 数（Latency SLI total） |
| `chaos_app_external_latency_good` | `duration ≤ le && 2xx` を 0/1 で表す gauge。`le` ラベル (`0.1`, `0.25`, `0.5`, `1`, `2`, `5` 秒) で bucket を区別。SLI 定義は `latencyThresholdLe` (default `"1"`) に一致する `le` ラベルを `EQ` filter で選択する |
| `chaos_app_external_sli_publisher_heartbeat` | publisher が実行されたことを示す freshness signal |

Azure Monitor SLI は上記 good / total metrics を Request-based SLI として `Sum` 集計します。既定の partitioning dimensions は `environment`, `service`, `test` です。publisher 自体の停止は `ExternalSliPublisherHeartbeatMissing` で検知します。

External SLI metrics は最新の閉じた window に対する probe と、欠落 window の bad sample を合算して発行します。Azure Monitor Workspace は `OldData` として現在から 20 分より古い timestamp を拒否するため、catch-up した複数 window は publisher の実行時刻に合算します。Request-based SLI は good / total の合計で評価されるため、時間分布は圧縮されますが、rolling period 内の分子・分母は回復できます。heartbeat metric は publisher freshness を表すため、実行時刻で発行します。SLI 作成前の入力確認は Managed Prometheus の PromQL で行います。

```promql
count_over_time(chaos_app_external_availability_total{environment="<env>",service="chaos-app",test="<probe-name>"}[45m])
```

SLI 作成後の destination metric は、SLI ARM resource の `destinationMetrics` に出る metric name を使って AMW の Prometheus query endpoint と Azure Metrics API の両方で確認します。`azd provision sli` の layer `preprovision` hook は入力 metric の出現を待ち、`postprovision` hook は `uv run ../../scripts/wait-for-external-sli-signals.py --skip-source --require-sli-destination` を実行し、入力 metric だけで成功扱いにしません。

Gateway Envoy 由来の `gateway:chaos_app:http_request_duration:p95` と `gateway:chaos_app:http_error_rate:ratio` は、短期診断用の recording rule として残します。Azure Monitor SLI の error budget 判定には使いません。

SLI / SLO 系の判断は [ADR-012](adr/012-functions-direct-external-sli-probe.md) と [ADR-013](adr/013-histogram-bucket-latency-sli.md) を参照してください。Latency SLI のしきい値は SLI 定義 (`infra/modules/azmonitor/sli-definitions.bicep`) の `latencyThresholdLe` パラメータで決定し、publisher は単一 metric `chaos_app_external_latency_good` を `le` ラベル付きで bucket 別に emit します。

## エンドポイントと L7 policy

Cilium L7 policy で許可する path は以下に限定します。

| Path | 目的 | Redis 依存 | 主な利用者 |
|------|------|------------|------------|
| `GET /` | 通常 API | あり | 外部 Gateway / 負荷テスト |
| `GET /health` | 外部 health / 手動確認 | あり | 手動確認 |
| `GET /livez` | liveness / startup | なし | Kubernetes probe |
| `GET /readyz` | readiness | あり | Kubernetes probe |
| `GET /metrics` | Prometheus scrape | なし | Managed Prometheus |

外部 Gateway 経由では component が `GET /` を許可し、`chaos-app` 固有 patch が `GET /health` を追加します。Azure Functions external SLI publisher は通常 API の `GET /` を probe し、trace context を伝搬して Application Map で Function App から chaos-app への依存関係を表示します。`/livez`、`/readyz`、`/metrics` は内部 source のみに許可します。probe を追加する場合は、アプリ route、Kubernetes probe、CNP テンプレートまたは app 固有 patch を同時に更新してください。

## 外形 SLI publisher

SLI 用の人工トラフィックは AKS 内 CronJob ではなく、Azure Functions Timer Trigger の external SLI publisher が AKS 外から生成します。publisher は probe 結果を SLI 用 Prometheus metrics に変換し、Application Insights には Function App から chaos app への HTTP dependency telemetry も送ります。

主な設定は `infra/main.bicep` の `externalSli*` parameters で管理します。

- `externalSliProbeScheme`: probe URL の scheme (`http` / `https`)
- `externalSliProbePath`: probe path
- `externalSliProbeName`: Prometheus label `test` に入る probe 名
- `externalSliProbeTimeoutSeconds`: probe timeout
- `externalSliPublisherWindowSeconds`: publisher の集計 window
- `externalSliLatencyThresholdMs`: Latency SLI の good 判定しきい値

既存環境に残る AKS 内 synthetic traffic などは `uv run scripts/cleanup-legacy-sli-sources.py` で dry-run 確認し、必要に応じて `--execute` を付けて削除します。

## OTLP logs

アプリは traces / metrics に加えて logs も OTLP で export します。`logging.getLogger("app")` 配下 (`app.main`, `app.telemetry`, `app.redis_client`) の log のみを export し、third-party logger や uvicorn 独自 logging は巻き込みません。

- AKS App Monitoring add-on 経由で Log Analytics の `OTelLogs` テーブルに届く
- `ServiceName=chaos-app`, `ScopeName=app.main` などで識別する
- stdout は OTLP のみで取得し、ContainerLogV2 では除外する
- stderr は ContainerLogV2 で維持し、uvicorn error / 未捕捉例外 / crash 時の証跡として残す

Python OpenTelemetry Logs SDK の成熟度リスクと依存 pin の理由は [ADR-006 §成熟度の前提とリスク](adr/006-otlp-vendor-neutral-otel.md#成熟度の前提とリスク) を参照してください。

## 運用上の注意

- Azure Monitor SLI の入力は publisher が Managed Prometheus に remote-write した good / total metrics です。Application Insights dependency telemetry は Application Map と診断用であり、SLI の正本ではありません。
- `Instrumentation/chaos-app-otel` は `k8s/apps/chaos-app/instrumentation/` で app-specific に管理し、`azd deploy api-instrumentation` で `Deployment/chaos-app` より先に適用します。API deploy hook は Pod に `OTEL_EXPORTER_OTLP_*` が注入されたことを確認し、未注入なら失敗します。通常運用で `kubectl rollout restart` に依存しません。
- 標準 semconv の `http.server.active_requests` は Pod 再起動時ドリフトと no-traffic 時の series 欠落があるため、アラート基準にしません。in-flight request 数の観測にはアプリ独自の `chaos_app.active_requests` を使います。
