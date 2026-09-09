# Load Testing with Auto Endpoint Detection

## 概要
このディレクトリには、AKS Chaos LabアプリケーションのためのLocustベース負荷テストスクリプトが含まれています。

## 主な機能
- **自動エンドポイント検出**: azdの`AZURE_INGRESS_FQDN`優先（http）、未設定時はGateway/Ingressから検出
- **複数負荷プロファイル**: smoke/baseline/stress/spike プロファイル対応
- **環境変数による柔軟な設定**
- **uv統合**: Python依存関係をuvで自動管理

## 使用方法

以下のコマンドはリポジトリ直下で実行します。

### 基本的な使用（自動検出）
```bash
# BASE_URLを自動検出してbaseline負荷テスト実行
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline

# smoke（軽量・既定のクイック検証）
uv run --no-project "${PWD}/scripts/tasks.py" load-smoke

# stress負荷テスト実行
uv run --no-project "${PWD}/scripts/tasks.py" load-stress

# spike負荷テスト実行
uv run --no-project "${PWD}/scripts/tasks.py" load-spike
```

### 手動でBASE_URL指定
```bash
export BASE_URL=https://myapp.example.com
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline
```

PowerShell:

```powershell
$env:BASE_URL = "https://myapp.example.com"
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline
```

### カスタム設定
```bash
# 異なるGatewayを対象にする場合
export GATEWAY_NAME=my-gateway
export GATEWAY_NS=my-namespace
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline

# 負荷パラメータをカスタマイズ
export USERS=100
export SPAWN_RATE=10
export DURATION=300
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline
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

利用する package index を確認し、[ローカル開発の同期手順](../../../../docs/deployment.md#ローカル開発)に従って workspace の依存関係を同期します。組織承認済みの package index を使う場合は、同文書の[専用手順](../../../../docs/deployment.md#組織承認済み-package-index-を使う環境)を使います。通常環境の同期コマンドは次のとおりです。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" sync-dev
```

### 依存関係について

Locust はリポジトリ直下の [pyproject.toml](../../../../pyproject.toml) の `dependency-groups.dev` で定義しています。uv workspace の仮想環境を共有し、`load-*` タスクはその環境の Locust で `src/api/tests/load/locustfile.py` を実行します。

## 前提条件
- kubectl がインストール済みでクラスタにアクセス可能
- uv (Python package manager) がインストール済み
- workspace の開発依存を同期済み

## 自動検出の仕組み
BASE_URL が未設定の場合、以下の優先順で自動検出します：

1) azd 環境変数からの検出
- `AZURE_INGRESS_FQDN` を参照して `http://$AZURE_INGRESS_FQDN` を組み立てます（スキームは http 固定）。
- すでにシェルで `AZURE_INGRESS_FQDN` が設定されていない場合は、`azd env get-value AZURE_INGRESS_FQDN` の結果を読み込みます。

例：
```bash
# BASE_URL 自動検出で baseline を実行
uv run --no-project "${PWD}/scripts/tasks.py" load-baseline
```

2) Kubernetes Gateway からの検出
- `kubectl` で Gateway の LoadBalancer IP を取得し、`http://` を組み立てます。

## Gateway diagnostic metrics

負荷テスト時の短期診断は [ADR-004](../../../../docs/adr/004-envoy-gateway-metrics-for-slo.md) と [ADR-009](../../../../docs/adr/009-azure-monitor-sli-and-prometheus-slo.md) に基づき、Gateway 層 Envoy メトリクスを使います。Azure Monitor SLI の Availability / Latency は、[ADR-012](../../../../docs/adr/012-functions-direct-external-sli-probe.md) と [ADR-014](../../../../docs/adr/014-histogram-bucket-latency-sli.md) に従い、Azure Functions external SLI publisher の direct HTTP probe を入力にします。

Locust の成功率と応答時間はクライアント側の負荷試験結果、Gateway 指標は Gateway からバックエンドへの通信の診断に使います。Locust のリクエスト数は SLI の分母に直接入りませんが、負荷による遅延や障害は外形 probe の結果を通じて SLI に影響します。

**メトリクス**:
- `envoy_cluster_upstream_rq{response_code, cluster_name}` — ステータスコード別 HTTP リクエスト数
- `envoy_cluster_upstream_rq_completed{cluster_name}` — 完了 HTTP リクエスト数
- `envoy_cluster_external_upstream_rq_time_bucket{cluster_name}` — Gateway からバックエンド Service へのレイテンシヒストグラム

`cluster_name` が `outbound|80||chaos-app.*` に一致する Gateway → chaos-app のトラフィックが診断対象です。

### Recording Rules

| ルール名 | 説明 |
|---|---|
| `gateway:chaos_app:http_request_duration:p95` | P95 レイテンシ (秒) |
| `gateway:chaos_app:http_error_rate:ratio` | 5xx エラー率 |

SLI 用 metrics は `chaos_app_external_availability_*` / `chaos_app_external_latency_*` です。詳細は [docs/observability.md](../../../../docs/observability.md) を参照してください。
