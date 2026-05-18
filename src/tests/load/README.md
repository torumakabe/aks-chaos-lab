# Load Testing with Auto Endpoint Detection

## 概要
このディレクトリには、AKS Chaos LabアプリケーションのためのLocustベース負荷テストスクリプトが含まれています。

## 主な機能
- **自動エンドポイント検出**: azdの`AZURE_INGRESS_FQDN`優先（http）、未設定時はGateway/Ingressから検出
- **複数負荷プロファイル**: smoke/baseline/stress/spike プロファイル対応
- **環境変数による柔軟な設定**
- **uv統合**: Python依存関係をuvで自動管理

## 使用方法

### 基本的な使用（自動検出）
```bash
# BASE_URLを自動検出してbaseline負荷テスト実行
uv run scripts/tasks.py load-baseline

# smoke（軽量・既定のクイック検証）
uv run scripts/tasks.py load-smoke

# stress負荷テスト実行
uv run scripts/tasks.py load-stress

# spike負荷テスト実行
uv run scripts/tasks.py load-spike
```

### 手動でBASE_URL指定
```bash
export BASE_URL=https://myapp.example.com
uv run scripts/tasks.py load-baseline
```

PowerShell:

```powershell
$env:BASE_URL = "https://myapp.example.com"
uv run scripts/tasks.py load-baseline
```

### カスタム設定
```bash
# 異なるGatewayを対象にする場合
export GATEWAY_NAME=my-gateway
export GATEWAY_NS=my-namespace
uv run scripts/tasks.py load-baseline

# 負荷パラメータをカスタマイズ
export USERS=100
export SPAWN_RATE=10
export DURATION=300
uv run scripts/tasks.py load-baseline
```

## 負荷プロファイル

### smoke（推奨: クイック検証/CI）
- USERS: 5
- SPAWN_RATE: 2/秒
- DURATION: 30秒

### baseline (デフォルト)
- USERS: 50
- SPAWN_RATE: 5/秒  
- DURATION: 120秒

### stress
- USERS: 200
- SPAWN_RATE: 20/秒
- DURATION: 300秒

### spike  
- USERS: 300
- SPAWN_RATE: 100/秒
- DURATION: 120秒

## セットアップ

### 初回実行前の準備
```bash
uv run scripts/tasks.py sync-dev
```

### 依存関係について
- locustはsrc/pyproject.tomlのdev dependenciesで定義
- uvが自動的に仮想環境を管理  
- `uv run scripts/tasks.py load-*` は `src/` の dev dependencies を使って Locust を実行

## 前提条件
- kubectl がインストール済みでクラスタにアクセス可能
- uv (Python package manager) がインストール済み
- src/pyproject.toml に locust が dev dependency として定義済み

## 自動検出の仕組み
BASE_URL が未設定の場合、以下の優先順で自動検出します：

1) azd 環境変数からの検出
- `AZURE_INGRESS_FQDN` を参照して `http://$AZURE_INGRESS_FQDN` を組み立てます（スキームは http 固定）。
- すでにシェルで `AZURE_INGRESS_FQDN` が設定されていない場合は、`azd env get-value AZURE_INGRESS_FQDN` の結果を読み込みます。

例：
```bash
# BASE_URL 自動検出で baseline を実行
uv run scripts/tasks.py load-baseline
```

2) Kubernetes Gateway からの検出
- `kubectl` で Gateway の LoadBalancer IP を取得し、`http://` を組み立てます。

## SLIメトリクスとOperational Alerts

SLI 計測と短期 operational alerts は ADR-004 / ADR-009 に基づき、FastAPI アプリ内ではなく Gateway 層 Envoy メトリクスで行います。`ama-metrics` の pod annotation scraping が Envoy の `envoy_cluster_*` メトリクスを AMW に収集し、Prometheus recording rules が `gateway:chaos_app:*` に整形します。

**メトリクス**:
- `envoy_cluster_upstream_rq{response_code, cluster_name}` — ステータスコード別 HTTP リクエスト数
- `envoy_cluster_upstream_rq_completed{cluster_name}` — 完了 HTTP リクエスト数
- `envoy_cluster_external_upstream_rq_time_bucket{cluster_name}` — Gateway からバックエンド Service へのレイテンシヒストグラム

`cluster_name` が `outbound|80||chaos-app.*` に一致する Gateway → chaos-app のトラフィックが SLI 計測対象です。

### Recording Rules

| ルール名 | 説明 |
|---|---|
| `gateway:chaos_app:http_request_duration:p95` | P95 レイテンシ (秒) |
| `gateway:chaos_app:http_error_rate:ratio` | 5xx エラー率 |
| `gateway:chaos_app:http_success_rate:ratio` | 成功率 |
| `gateway:chaos_app:http_request_rate` | リクエストレート (req/s) |
| `gateway:chaos_app:http_success_total` | 成功リクエスト累計 |
| `gateway:chaos_app:http_request_total` | リクエスト累計 |

### Operational Alerts

| アラート | しきい値 |
|---|---|
| `ChaosAppRequestLatencyP95High` | P95 > 1s が 5分持続 |
| `ChaosAppRequestFailureRateHigh` | エラー率 > 1% が 5分持続 |

これらは SLO/error budget アラートではなく、Chaos 実験や即時トラブルシュート向けの短期 operational alerts です。Azure Monitor SLI を有効化する場合も、上記 recording rules は SLI 入力候補として維持します。Prometheus operational alerts を無効化する場合は `enablePrometheusAppOperationalAlerts=false` を指定します。
