# Feature: Gateway API (App Routing Istio) 移行

## 概要

AKS Web Application Routing アドオンのマネージド NGINX 実装（2026/11 サポート終了）から、
Gateway API ベースの新 App Routing（Istio meshless）への移行。

## 決定事項

| 決定 | 理由 | 却下した代替案 |
|------|------|----------------|
| Bicep API `2026-01-02-preview` を使用 | `webAppRouting.gatewayAPIImplementations.appRoutingIstio` が必要。`2025-08-02-preview` では `gatewayAPI.installation` のみで Istio 有効化プロパティが欠落 | `2025-08-02-preview` + CLI 直接有効化（IaC 管理外になる） |
| GatewayClass `approuting-istio` を使用 | AKS マネージドで自動プロビジョニング。HPA/PDB も自動設定される | 自前の Istio Gateway（管理コスト大） |
| NetworkPolicy は同一 namespace の podSelector で制御 | Envoy Pod は Gateway と同じ namespace にデプロイされるため、namespaceSelector は不要 | namespaceSelector + app-routing-system（NGINX 時代の方式） |
| アプリ層 Prometheus メトリクスで SLO 監視を維持 | Envoy の proxyStatsMatcher は AKS コントローラーの定期リコンサイルで元に戻されるため信頼できない。FastAPI アプリに prometheus-client を追加し /metrics エンドポイントで公開 | Envoy メトリクス（proxyStatsMatcher が AKS に定期リセットされる）、istio_requests_total（meshless では不可） |

## PoC 調査結果

### Bicep API プロパティ構造（2026-01-02-preview）

```
ingressProfile:
  gatewayAPI:
    installation: 'Standard'        # Gateway API CRD のマネージドインストール
  webAppRouting:
    enabled: true
    gatewayAPIImplementations:
      appRoutingIstio:
        mode: 'Enabled'             # Istio meshless Gateway の有効化
```

### Gateway 自動プロビジョニング

Gateway リソースを作成すると以下が自動生成される:
- **Deployment**: `{gateway-name}-approuting-istio`（Envoy proxy）
- **Service**: `{gateway-name}-approuting-istio`（type: LoadBalancer）
- **HPA**: min 2 / max 5 / CPU 80%
- **PDB**: minAvailable 1

Gateway の annotation は自動生成される Service に伝播する:
- `service.beta.kubernetes.io/azure-pip-name` ✅
- `service.beta.kubernetes.io/azure-load-balancer-resource-group` ✅

### Envoy Pod の特性

- **配置**: Gateway リソースと同じ namespace
- **ラベル**:
  ```yaml
  gateway.networking.k8s.io/gateway-name: <gateway-name>
  gateway.networking.k8s.io/gateway-class-name: approuting-istio
  azureservicemesh/istio.component: gateway-api-ingress
  gateway.istio.io/managed: istio.io-gateway-controller
  ```
- **コンテナイメージ**: distroless（シェルコマンドなし）

### メトリクス

meshless Istio モードではゲートウェイレベルの HTTP メトリクス取得に制約がある:

- `istio.stats` フィルター未ロード → `istio_requests_total` 等の Istio 標準メトリクスは不可
- `proxyStatsMatcher` で Envoy ネイティブメトリクスを有効化できるが、**AKS コントローラーが
  mesh config（`aks-istio-system/istio` ConfigMap）を定期リコンサイルで元に戻す**ため
  永続的に維持できない

→ **アプリ層で Prometheus メトリクスを直接公開する方式を採用**。

#### アプリ層メトリクス（採用）

FastAPI アプリに `prometheus-client` を追加し、`/metrics` エンドポイントで公開:

| メトリクス | 型 | ラベル | 除外パス |
|---|---|---|---|
| `app_http_requests_total` | Counter | `method`, `status` | `/health`, `/metrics` |
| `app_http_request_duration_seconds` | Histogram | `method` | `/health`, `/metrics` |

Deployment に `prometheus.io/scrape`, `prometheus.io/port: "8000"`, `prometheus.io/path: "/metrics"` アノテーションを付与。
AMA の `podannotations` keep-list で `app_http_requests_total\|app_http_request_duration_seconds.*` をフィルタリング。

#### PromQL マッピング (NGINX → アプリ層)

| 旧 (NGINX) | 新 (アプリ層) |
|---|---|
| `histogram_quantile(0.95, sum by (le) (rate(nginx_ingress_controller_request_duration_seconds_bucket[5m])))` | `histogram_quantile(0.95, sum by (le) (rate(app_http_request_duration_seconds_bucket[5m])))` |
| `sum(rate(...{status=~"5.."}[5m])) / total` | `sum(rate(app_http_requests_total{status=~"5.."}[5m])) / sum(rate(app_http_requests_total[5m]))` |
| `sum(rate(nginx_ingress_controller_request_duration_seconds_count[5m]))` | `sum(rate(app_http_requests_total[5m]))` |

#### Envoy メトリクス調査結果（参考、不採用）

`proxyStatsMatcher` を mesh config に設定すると `envoy_cluster_upstream_rq{response_code}`,
`envoy_cluster_upstream_rq_time_bucket` 等が取得可能になることを PoC で確認。
ただし AKS コントローラーが ConfigMap を定期リコンサイルするため実用不可。

### NetworkPolicy

Envoy → アプリへの通信を許可する NetworkPolicy が必要:
```yaml
ingress:
  - from:
    - podSelector:
        matchLabels:
          gateway.networking.k8s.io/gateway-name: <gateway-name>
```

## 制約

- プレビュー機能（GA 時期未定）。GA 時に API プロパティが変わる可能性あり
- Envoy ゲートウェイレベルの HTTP メトリクスは AKS コントローラーの mesh config リコンサイルにより安定取得不可。アプリ層メトリクスで代替
- `istio_requests_total` 等の Istio 標準メトリクスは meshless では利用不可
- `istio-gateway-class-defaults` ConfigMap で変更できるのは HPA/PDB 設定のみ

### カオス実験への影響

メトリクス取得ポイントを NGINX イングレス層からアプリ層（FastAPI middleware）に移したことで、**Envoy プロキシ層で障害注入される HTTPChaos 実験がアプリ層メトリクスでは観測できない**。

| 実験 | 障害注入レイヤー | アプリ層で観測可能か | 備考 |
|------|------------------|---------------------|------|
| PodChaos (pod-failure) | Pod | ⚠️ 部分的 | graceful shutdown 中の in-flight リクエストは記録されうるが、Pod 停止後の Envoy 502/503 は不可視 |
| NetworkChaos (delay) | Network | ✅ | レイテンシ増加は `app_http_request_duration_seconds` に反映 |
| NetworkChaos (loss 100%) | Network (outbound) | ✅ | Redis 接続失敗 → アプリが 503 を返す経路で観測可能 |
| StressChaos | Process | ✅ | CPU 競合によるレイテンシ増加を観測可能 |
| **HTTPChaos (abort)** | **Envoy proxy** | **❌ 不可** | **Envoy フィルターがリクエストを中断するためアプリに到達しない。NGINX 時代はイングレス層で 5xx として観測できた** |
| DNSChaos | DNS | ✅ | Redis FQDN 解決失敗 → アプリが 503 を返す経路で観測可能 |

HTTPChaos の結果を観測するには、負荷テストツール（Locust）のクライアント側メトリクスを用いる必要がある。

## 進捗

- [x] PoC フェーズ完了（フィーチャーフラグ、Bicep API 調査、Gateway テスト、メトリクス調査、NetworkPolicy 調査）
- [x] Bicep インフラ更新
- [x] K8s マニフェスト移行
- [x] Prometheus ルール移行（Envoy メトリクスベースで再構築）
- [x] CI/CD・ドキュメント更新
- [ ] 検証
