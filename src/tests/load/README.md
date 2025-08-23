# Load Testing with Auto Ingress Detection

## 概要
このディレクトリには、AKS Chaos LabアプリケーションのためのLocustベース負荷テストスクリプトが含まれています。

## 主な機能
- **自動エンドポイント検出**: azdの`AZURE_INGRESS_FQDN`優先（http）、未設定時はIngressから検出
- **複数負荷プロファイル**: smoke/baseline/stress/spike プロファイル対応
- **環境変数による柔軟な設定**
- **uv統合**: Python依存関係をuvで自動管理

## 使用方法

### 基本的な使用（自動検出）
```bash
# BASE_URLを自動検出してbaseline負荷テスト実行
./run-load-tests.sh baseline

# smoke（軽量・既定のクイック検証）
./run-load-tests.sh smoke

# stress負荷テスト実行
./run-load-tests.sh stress

# spike負荷テスト実行  
./run-load-tests.sh spike
```

### 手動でBASE_URL指定
```bash
# 手動でエンドポイント指定
BASE_URL=https://myapp.example.com ./run-load-tests.sh baseline
```

### カスタム設定
```bash
# 異なるIngressを対象にする場合
INGRESS_NAME=my-app INGRESS_NS=my-namespace ./run-load-tests.sh baseline

# 負荷パラメータをカスタマイズ
USERS=100 SPAWN_RATE=10 DURATION=300 ./run-load-tests.sh baseline
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
# src/ ディレクトリでdev dependenciesをインストール
cd ../../
uv sync --group dev
```

### 依存関係について
- locustはsrc/pyproject.tomlのdev dependenciesで定義
- uvが自動的に仮想環境を管理  
- run-load-tests.shは`uv run --group dev locust`で実行

## 前提条件
- kubectl がインストール済みでクラスタにアクセス可能
- uv (Python package manager) がインストール済み
- src/pyproject.toml に locust が dev dependency として定義済み

## 自動検出の仕組み
BASE_URL が未設定の場合、以下の優先順で自動検出します：

1) azd 環境変数からの検出
- `AZURE_INGRESS_FQDN` を参照して `http://$AZURE_INGRESS_FQDN` を組み立てます（スキームは http 固定）。
- すでにシェルで `AZURE_INGRESS_FQDN` がエクスポートされていない場合は、`azd env get-values` の結果を読み込みます。

例：
```bash
# azd 環境変数をカレントシェルに読み込む
eval "$(azd env get-values)"

# BASE_URL 自動検出で baseline を実行
./run-load-tests.sh baseline
```

2) Kubernetes Ingress からの検出
- `kubectl` で Ingress の LoadBalancer IP を取得し、TLS の有無に応じて `http://` or `https://` を組み立てます。

## SLOメトリクス生成
負荷テスト実行により、Web Application Routing nginx から以下のメトリクスが生成されPrometheus/Grafanaで観測可能になります：

- `nginx_ingress_controller_requests` - リクエスト総数・レスポンスコード別（ホスト名ラベル付き）
- `nginx_ingress_controller_request_duration_seconds` - レスポンス時間分布（ホスト名ラベル付き）

これらのメトリクスは設定済みのRecording RulesによりSLO計測用メトリクスに集約されます：
- `app:nginx_ingress_request:p95` - p95レイテンシ
- `app:nginx_ingress_error_rate:ratio` - 5xxエラー率
