# ADR-007: ACNS Advanced Network Policies の L7 化と Cilium L7 HTTP 可観測性の導入

## Status

Accepted

## Context

ADR-001 で「HTTPChaos は Envoy 層で注入されるためアプリ層メトリクスでは観測不可」としてクライアント側（Locust）メトリクスでの代替観測に切替えた。ADR-006 で OTLP 経由の App Insights 連携に移行した際、chaos-app の egress から App Insights FQDN が消え、`hubble_dns_queries_total` / `hubble_drop_total` 系のデータ点が激減。データで裏取りした結果、これらは chaos-app の外部通信がなくなった副作用で、CNP 変更自体の誤設定ではないことを確認した。

同時に、Azure Monitor の AKS リソース「Dashboards with Grafana」には以下の L7 系ダッシュボードが提供されていることを確認した。
- Kubernetes / Networking / L7 Flows (Namespace)
- Kubernetes / Networking / L7 Flows (Workload)

これらは `hubble_http_requests_total` / `hubble_http_request_duration_seconds_*` を参照するが、本プロジェクトでは当時 `advancedNetworkPolicies: 'FQDN'` 固定であり ACNS の Container Network Security が L7 モードで動いていなかったため、系列が 0 で空のダッシュボードになっていた。ラボの主旨（「試して、データも見て」）と HTTPChaos 観測性の弱さを踏まえ、L7 可観測性を補強する価値が大きいと判断した。

本 ADR は、[ADR-002](002-container-network-logs.md) の `FQDN` 設定および Istio 併用時の L7 ポリシー利用不可という前提を部分変更し、App Routing Istio 構成で L7 ポリシーと Cilium Envoy 経由の L7 メトリクスを採用する。ADR-002 の CRD ベースの保存ログ採用、Log Analytics 連携、対象 Pod のフィルタは維持する。

ADR-002 の「L7 フローログは対象外」は、Log Analytics の `ContainerNetworkLogs` テーブルへの L7 フローログ保存に関する判断であり、本 ADR でも維持する。L7 メトリクスの採用は、Istio 併用時の L7 保存ログの可否を確認したことを意味しない。

## Decision

- **ACNS の `advancedNetworkPolicies` を `'FQDN'` → `'L7'` に変更**（`infra/modules/aks.bicep`、Base / Automatic 両モード）。L7 化すると FQDN フィルタも同時に有効化される（ポータル / CLI 仕様）。これにより ACNS に同梱される `ValidatingAdmissionPolicy advanced-networking-validating-policy` の判定が変わり、`CiliumNetworkPolicy` で HTTP L7 rule を使えるようになる。
- **chaos-app への ingress L7 CNP を追加**（テンプレート: `k8s/components/cilium-ingress-l7/`、app 固有 patch: `k8s/apps/chaos-app/kustomization.yaml`）。以下の peer / path に限定して HTTP ルールを適用し、Hubble L7 メトリクスを生成する。`GET /health` は chaos-app 固有の外部 health route として patch で追加し、業務 API path を持つ別アプリも同じ方式で追加する。
  - `gateway.networking.k8s.io/gateway-name: chaos-app`（App Routing Istio の Envoy pod） → `GET /`, `GET /health`
  - host / remote-node entity（kubelet probe） → `GET /livez`, `GET /readyz`
- **運用 endpoint を標準化**する。通常 API は `GET /`、外部 health / 既存互換は `GET /health`、Redis 非依存の startup/liveness は `GET /livez`、Redis 依存の readiness は `GET /readyz` とし、CNP は source ごとに必要な subset のみ許可する。外部 Gateway 経由では `/` と `/health` のみ公開し、`/livez` / `/readyz` は内部 source のみに許可する。Cilium の HTTP `path` は正規表現として扱われるため、`^/$` や `^/readyz$` のように anchor して意図しない prefix match を避ける。
- **API の metrics は標準 OTLP exporter で送信する**。[ADR-004](004-envoy-gateway-metrics-for-slo.md) で旧 Prometheus 計装を除去し、[ADR-006](006-otlp-vendor-neutral-otel.md) で標準 OTLP に移行した方針を維持する。API は scrape endpoint `/metrics` を公開せず、CNP にも kube-system から API の TCP/8000 への `GET /metrics` 許可を設けない。これにより、アプリに scrape の責務を再導入せず、未使用の通信許可を残さない。
- **広い Kubernetes NetworkPolicy は適用しない**。Gateway / kube-system から Pod への L4 allow を別途置くと、Cilium L7 の path 制限より広い許可経路になりうるため、chaos-app の ingress allowlist は CNP に集約する。
- **egress 側の L7 HTTP 化は見送り**。chaos-app の外部依存は Redis（TCP/6380、TLS）であり HTTP ではない。また OTLP は AMA node エージェント経由でノード IP を直接叩くため、L7 HTTP の可視化対象として意味がない。
- **ama-metrics の keep-list は既存の `networkobservabilityHubble = "hubble.*"` のまま変更しない**。`hubble_http_*` が自動で収集対象になるため追加設定不要。
- **ContainerNetworkLogs 側の L7 フローログ有効化は ADR-002 の方針のまま対象外**。Istio 併用時の挙動を含めた検証が別途必要で、今回のスコープ外。

## Consequences

- **利点**:
  - `hubble_http_requests_total{method, protocol, status, reporter}` と `hubble_http_request_duration_seconds_*` が Prometheus（AMW）に入り、AKS の「Dashboards with Grafana」の L7 Flows (Namespace / Workload) が埋まる。5xx 率、メソッド分布、レイテンシ p50/p95 が即座に可視化される。
  - HTTPChaos 実験中のリクエスト成功率低下をクラスタ側のメトリクスでも捉えられる（ADR-001 では Locust 側のみに依存していた領域を補完）。Envoy 層の注入なので完全補完ではないが、chaos-app pod が受信した実トラフィックの HTTP 結果は観測できる。
  - Cilium の L7 visibility は Envoy proxy 経由で実現されるため、CNP の HTTP rule に一致しないリクエストは 403 相当で拒否される。これは副次的に意図しないパスへのアクセスを防ぐセキュリティ効果も持つ。

- **制約 / トレードオフ**:
  - L7 rule に該当するトラフィックは Cilium Envoy を通るため、レイテンシと CPU/メモリに追加コストが発生する。Lab 規模では無視できる範囲だが、パス追加のたびに CNP の HTTP rule を維持する運用負荷がある。
  - path を増やすと CNP を更新する必要がある。追加時も source ごとに必要な path のみ許可する。
  - VAP `advanced-networking-validating-policy` は `FQDN` モードでは HTTP L7 rule を拒否するため、L7 有効化が対象 CNP の適用前提となる。適用順序と確認方法は[可観測性ガイドの「エンドポイントと L7 policy」](../observability.md#エンドポイントと-l7-policy)を参照する。

- **当時の導入確認**:
  - L7 有効化後に対象 CNP が VAP を通過し、既存 Pod の Ready と readiness / startup probe が維持された。LB 経由の `/` と `/health` への正常リクエストで、Hubble の HTTP request と duration の系列生成も確認した。
