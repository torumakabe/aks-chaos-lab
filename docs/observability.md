# 可観測性ガイド

このドキュメントは、AKS Chaos Lab の観測シグナルと運用時の見方をまとめます。採用理由や却下した案は [ADR 一覧](adr/INDEX.md) を参照してください。

## 可観測性の構成

| 領域 | 役割 | 主なファイル / 参照 |
|------|------|---------------------|
| Application Insights | アプリの traces / metrics / logs を OTLP で受信 | `src/app/telemetry.py`, [ADR-006](adr/006-otlp-vendor-neutral-otel.md) |
| Azure Monitor managed Prometheus | Gateway / app / platform signals の集約、recording rules、alerts | `infra/modules/prometheus/`, [ADR-004](adr/004-envoy-gateway-metrics-for-slo.md) |
| Azure Monitor SLI | Availability / Latency SLI definitions と burn-rate alerts | `infra/sli/`, [ADR-009](adr/009-azure-monitor-sli-and-prometheus-slo.md) |
| Container Insights | コンテナログ / メトリクスを Log Analytics に収集 | `k8s/observability/`, [ADR-003](adr/003-container-insights-custom-preset.md) |
| Container network logs | ACNS + Cilium の eBPF network flow logs | `k8s/observability/container-network-log.yaml`, [ADR-002](adr/002-container-network-logs.md) |
| Cilium L7 HTTP metrics | L7 policy 適用時の HTTP request metrics | `k8s/components/cilium-ingress-l7/`, [ADR-007](adr/007-acns-l7-observability.md) |

Grafana ダッシュボードは Azure Portal の対象 AKS > Monitoring > Dashboards with Grafana から参照できます。

## アプリ信頼性 signal

Gateway 層の Envoy メトリクスを `gateway:chaos_app:*` recording rules に整形し、Azure Monitor SLI と短期 operational alerts の入力に使います。

- Availability SLI: `gateway:chaos_app:http_success_rate:ratio > 0.99`
- Latency SLI: `gateway:chaos_app:http_request_duration:le_1s_ratio >= 0.95`
- metric namespace: `customdefault`
- partitioning dimension: `cluster_name`

Operational alerts は `enablePrometheusAppOperationalAlerts=true` で作成されます。

- `ChaosAppRequestLatencyGoodRateLow`
- `ChaosAppRequestFailureRateHigh`
- `ChaosAppNoTraffic`

SLI / SLO 系の判断は [ADR-009](adr/009-azure-monitor-sli-and-prometheus-slo.md) を参照してください。

## エンドポイントと L7 policy

Cilium L7 policy で許可する path は以下に限定します。

| Path | 目的 | Redis 依存 | 主な利用者 |
|------|------|------------|------------|
| `GET /` | 通常 API | あり | 外部 Gateway / 合成トラフィック / 負荷テスト |
| `GET /health` | 外部 health / 既存互換 | あり | Gateway / 手動確認 |
| `GET /livez` | liveness / startup | なし | Kubernetes probe |
| `GET /readyz` | readiness | あり | Kubernetes probe |
| `GET /metrics` | Prometheus scrape | なし | Managed Prometheus |

外部 Gateway 経由では component が `GET /` を許可し、`chaos-app` 固有 patch が `GET /health` を追加します。`/livez`、`/readyz`、`/metrics` は内部 source のみに許可します。probe を追加する場合は、アプリ route、Kubernetes probe、CNP テンプレートまたは app 固有 patch を同時に更新してください。

## 合成トラフィック

`k8s/apps/chaos-app/cronjob-synthetic-traffic.yaml` が 1 分に 1 回 Gateway controller の ClusterIP 経由で `/` を叩き、`gateway:chaos_app:*` recording rule に最低限のシグナルを供給します。これにより、Azure Monitor SLI WindowBased 評価で no-data が Bad 扱いされる経路を閉じます。

一時停止する場合:

```bash
kubectl -n chaos-lab patch cronjob synthetic-traffic --type=merge -p '{"spec":{"suspend":true}}'
```

suspend 中は `ChaosAppNoTraffic` alert が発火しうる点に注意してください。

## OTLP logs

アプリは traces / metrics に加えて logs も OTLP で export します。`logging.getLogger("app")` 配下 (`app.main`, `app.telemetry`, `app.redis_client`) の log のみを export し、third-party logger や uvicorn 独自 logging は巻き込みません。

- AKS App Monitoring add-on 経由で Log Analytics の `OTelLogs` テーブルに届く
- `ServiceName=chaos-app`, `ScopeName=app.main` などで識別する
- stdout は OTLP のみで取得し、ContainerLogV2 では除外する
- stderr は ContainerLogV2 で維持し、uvicorn error / 未捕捉例外 / crash 時の証跡として残す

Python OpenTelemetry Logs SDK の成熟度リスクと依存 pin の理由は [ADR-006 §成熟度の前提とリスク](adr/006-otlp-vendor-neutral-otel.md#成熟度の前提とリスク) を参照してください。

## 運用上の注意

- ノード関連メトリクスは環境作成直後に収集されないことがあります。最大 24 時間で揃います。棚卸しは [docs/workarounds.md §D-1](workarounds.md#d-1-node-exporter-メトリクスが最大-24-時間遅延する) を参照してください。
- Application Insights Portal の `requests/duration` P95 / P99 は trace sampling の影響を受けます。SLI 判定の一次シグナルには Managed Prometheus の Envoy histogram bucket 由来の latency good-rate を使います。
- 標準 semconv の `http.server.active_requests` は Pod 再起動時ドリフトと no-traffic 時の series 欠落があるため、アラート基準にしません。in-flight request 数の観測にはアプリ独自の `chaos_app.active_requests` を使い、負荷状態の一次シグナルには `gateway:chaos_app:http_request_rate` を使います。
- no-traffic 時の NaN 回避は `infra/modules/prometheus/recording-rules.bicep` の recording rules で扱います。詳細は [docs/workarounds.md §D-5](workarounds.md#d-5-opentelemetry-updowncounter-httpserveractive_requests-の-pod-再起動時ドリフト) を参照してください。
