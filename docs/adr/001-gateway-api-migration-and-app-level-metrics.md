# ADR-001: マネージド NGINX から Gateway API (App Routing Istio) への移行とアプリ層メトリクスの採用

## Status

Accepted

## Context

AKS Web Application Routing のマネージド NGINX 実装は 2026/11 にサポート終了する。Gateway API ベースの新 App Routing（Istio meshless）へ移行する必要がある。移行に伴い、イングレス層の構成・NetworkPolicy・メトリクス取得方式の再設計が求められた。

## Decision

- **Bicep API `2026-01-02-preview` を使用**: `webAppRouting.gatewayAPIImplementations.appRoutingIstio` プロパティが必要。`2025-08-02-preview` では Istio 有効化プロパティが欠落し IaC 管理外の CLI 操作が必要になるため却下。
- **GatewayClass `approuting-istio` を使用**: AKS マネージドで HPA/PDB が自動設定される。自前 Istio Gateway は管理コストが大きいため却下。
- **NetworkPolicy は同一 namespace の podSelector で制御**: Envoy Pod は Gateway と同じ namespace にデプロイされるため namespaceSelector は不要。NGINX 時代の namespaceSelector + app-routing-system 方式は不適合。
- **アプリ層 Prometheus メトリクスで SLO 監視**: Envoy の proxyStatsMatcher は AKS コントローラーの定期リコンサイルでリセットされ信頼できない。FastAPI に prometheus-client を追加し `/metrics` で `app_http_requests_total` / `app_http_request_duration_seconds` を公開。meshless では `istio_requests_total` も利用不可。

## Consequences

- **利点**: マネージド NGINX のサポート終了前に後継機能へ移行できる。
- **制約**: プレビュー API のため GA 時にプロパティ変更の可能性がある。Envoy のメトリクスが AKS コントローラーのリコンサイルで維持できないため、アプリ層メトリクスへの移行を強いられる。その結果、HTTPChaos 実験は Envoy が障害をアプリ到達前に注入するため、アプリ側のメトリクスには記録されない。障害の観測には Locust など外部クライアントのメトリクスを使う。
