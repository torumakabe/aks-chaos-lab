# Research: プラットフォーム統合テストパイプライン

**Feature**: 003-platform-integration-test
**Date**: 2024-12-10

## Research Topics

### 1. GitHub Actions OIDC認証

**Question**: GitHub ActionsからAzureへのOIDC認証のベストプラクティスは？

**Decision**: Azure Login action v2 + Federated Identity Credential を使用

**Rationale**:
- シークレットの保存が不要でセキュアな認証方式
- Microsoft公式のアクション`azure/login@v2`がOIDCをサポート
- Federated Identity Credentialをワークロードアイデンティティとして構成

**Implementation**:
```yaml
permissions:
  id-token: write
  contents: read

steps:
  - uses: azure/login@v2
    with:
      client-id: ${{ secrets.AZURE_CLIENT_ID }}
      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

**Alternatives Considered**:
- Service Principal + Secret: セキュリティリスクが高い（シークレットのローテーション必要）
- Managed Identity: GitHub Actionsでは使用不可

---

### 2. azd環境分離

**Question**: 一時的なテスト環境をどのように識別・管理するか？

**Decision**: 環境名に `inttest-{run_id}` プレフィックスを使用し、専用リソースグループを作成

**Rationale**:
- GitHub Actions Run IDはグローバルに一意で衝突しない
- リソースグループ単位での削除が容易
- 既存のdev/prod環境と明確に分離できる

**Implementation**:
```bash
# 環境名の生成
ENV_NAME="inttest-${{ github.run_id }}"
RESOURCE_GROUP="rg-${ENV_NAME}"

# azd環境の初期化
azd env new $ENV_NAME
azd env set AZURE_RESOURCE_GROUP $RESOURCE_GROUP
azd env set AZURE_ENV_NAME $ENV_NAME

# クリーンアップ
az group delete --name $RESOURCE_GROUP --yes --no-wait
azd env delete --name $ENV_NAME --yes
```

**Alternatives Considered**:
- タイムスタンプベース: 衝突の可能性あり、ソートが困難
- ブランチ名ベース: 特殊文字の処理が必要、並列実行時に衝突

---

### 3. concurrency制御

**Question**: 同時実行を制限し、後続をキューイングする方法は？

**Decision**: GitHub Actions の `concurrency` 設定を使用

**Rationale**:
- ネイティブ機能で追加ツール不要
- `cancel-in-progress: false` でキューイング動作を実現
- リソースクォータ超過を防止

**Implementation**:
```yaml
concurrency:
  group: integration-test
  cancel-in-progress: false
```

**Alternatives Considered**:
- 外部ロックサービス: 複雑性が増す
- cancel-in-progress: true: 後続がキャンセルされてしまう

---

### 4. 統合テストパターン

**Question**: AKS環境でのHTTP統合テストをどのように実装するか？

**Decision**: curlによるスモークテスト + pytestによる詳細テスト

**Rationale**:
- curlは軽量で即時確認に最適
- pytestは既存のテストインフラと統合可能
- 段階的なテスト（ヘルスチェック → Redis連携）

**Implementation**:
```bash
# スモークテスト
INGRESS_URL=$(azd env get-value AZURE_INGRESS_FQDN)
curl -f "https://${INGRESS_URL}/health" || exit 1

# 詳細テスト（pytest）
cd src
INTEGRATION_TEST_URL="https://${INGRESS_URL}" uv run pytest tests/integration/test_platform.py -v
```

**Test Cases**:
1. `/health` エンドポイント - HTTP 200 + JSON応答
2. `/` エンドポイント - Redis連携確認（redis_data フィールド）

**Alternatives Considered**:
- k6/Locust: 負荷テストは本機能のスコープ外
- Postman/Newman: 依存関係が増える

---

### 5. Bicep検証戦略

**Question**: Bicepテンプレートの検証をどの程度実施するか？

**Decision**: `bicep build` + `az deployment group what-if` を実行

**Rationale**:
- `bicep build`: 構文エラーと参照エラーを検出
- `what-if`: 実際のデプロイ前に変更内容を確認
- 既存のCIでは`bicep build`のみ実施しているため、what-ifを追加

**Implementation**:
```bash
# Bicep検証
bicep build infra/main.bicep

# What-if分析（リソースグループが存在する場合）
az deployment sub what-if \
  --location japaneast \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json
```

**Alternatives Considered**:
- PSRuleなどのポリシーエンジン: 将来の拡張として検討

---

## Dependencies Summary

| Dependency | Version | Purpose |
|------------|---------|---------|
| azure/login@v2 | v2 | OIDC認証 |
| azure/cli@v2 | latest | Azure CLI操作 |
| Azure Developer CLI | latest | azd provision/deploy |
| Bicep CLI | v0.39+ | テンプレート検証 |
| curl | system | スモークテスト |
| pytest | (dev deps) | 詳細統合テスト |

## Open Questions

すべての主要な技術的疑問は解決済み。
