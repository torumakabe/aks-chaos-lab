# ADR-004: Envoy Gateway メトリクスによる SLO/SLI signal 監視

## Status

Accepted — Amends ADR-001（メトリクス取得方式の部分のみ。Gateway API 移行の判断自体は有効）

Gateway stats の取得方式は引き続き有効。SLO 評価と通知の役割分担は [ADR-009](009-azure-monitor-sli-and-prometheus-slo.md)、現在の外形 SLI 入力は [ADR-012](012-functions-direct-external-sli-probe.md)、Latency SLI の bucket 選択は [ADR-014](014-histogram-bucket-latency-sli.md) に従う。

## Context

ADR-001 では「proxyStatsMatcher が AKS コントローラーのリコンサイルでリセットされるため、Envoy ネイティブメトリクスは SLO 監視に使えない」として、アプリ層で prometheus-client によるメトリクスを計装した。本セッションの検証で、Gateway API の `infrastructure.parametersRef` を使い ConfigMap で proxyStatsMatcher を設定すれば、istiod のリコンサイルに耐えることが判明した。ConfigMap は `istio-gateway-class-defaults`（label: `gateway.istio.io/defaults-for-class: approuting-istio`）により approuting-istio GatewayClass にも適用される。

Envoy ネイティブ stats から以下が取得可能:

- `envoy_cluster_upstream_rq{response_code, cluster_name}` — ステータスコード別リクエスト数
- `envoy_cluster_external_upstream_rq_time_bucket{cluster_name}` — レイテンシ histogram（ミリ秒）
- `envoy_cluster_upstream_cx_total` — コネクション数
- `istio_requests_total` は meshless モードでは取得不可（stats filter 非稼働）

HTTPChaos 実験の影響が Envoy メトリクスで直接観測できることを検証済み（`response_code="503"` として記録される）。ADR-001 で指摘された「プロキシ層注入のためアプリ層メトリクスでは観測不可」問題が解消された。

## Decision

- アプリ層の旧メトリクス（`app_http_requests_total` / `app_http_request_duration_seconds`）の代替として採用した Envoy Gateway 層メトリクスを、現在は短期診断に使う
- `prometheus-client` 依存をアプリから除去する
- Gateway の p95 レイテンシとエラー率を `gateway:*` recording rules で保持する。Gateway 指標による高エラー率、遅延、無通信の短期アラートは廃止し、SLO 評価と通知には外形 SLI を使う
- proxyStatsMatcher の設定は `k8s/apps/chaos-app/gateway-options-configmap.yaml` で管理し、Gateway の `infrastructure.parametersRef` で参照する

## Consequences

- **利点**: アプリコードから旧 Prometheus メトリクス計装の責務を除去できる（prometheus-client 依存除去）。Gateway 層で HTTPChaos 注入を直接観測できる（検証済み）。`cluster_name` ラベルにより Service 単位で Gateway 内部のレイテンシとエラー率を診断できる。外形 SLI による SLO 評価と通知とは観測範囲と用途が異なる。
- **注意点**: Envoy メトリクス名が冗長（`envoy_cluster_external_upstream_rq_time_bucket` 等）。`cluster_name` ラベルにサービス FQDN が含まれる（`outbound|80||service.namespace.svc.cluster.local`）ため、PromQL が環境依存になる。`external_upstream_rq_time` はミリ秒単位のため、Recording rules で秒に変換が必要。AKS addon バージョンアップで Gateway 関連の仕様変更リスクがある。Gateway を `parametersRef` なしで再適用すると proxyStatsMatcher が失われるリスクがある。メトリクスの粒度は HTTPRoute 単位ではなくバックエンド Service（`cluster_name`）単位であるため、同一 Service を指す複数の HTTPRoute がある場合はメトリクスが合算される。
- **参照**: [AKS Istio Gateway API](https://learn.microsoft.com/azure/aks/istio-gateway-api)、`k8s/apps/chaos-app/gateway-options-configmap.yaml`、`infra/modules/prometheus/recording-rules.bicep`
