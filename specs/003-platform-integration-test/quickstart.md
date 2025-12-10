# Quickstart: プラットフォーム統合テストパイプライン

**Feature**: 003-platform-integration-test
**Date**: 2024-12-10

## Prerequisites

### Azure Setup

1. **Federated Identity Credential の作成**

   GitHub ActionsからAzureへのOIDC認証を設定する：

   ```bash
   # 変数設定
   APP_NAME="aks-chaos-lab-github-actions"
   GITHUB_ORG="torumakabe"
   GITHUB_REPO="aks-chaos-lab"
   
   # App Registration作成（既存の場合はスキップ）
   az ad app create --display-name $APP_NAME
   APP_ID=$(az ad app list --display-name $APP_NAME --query "[0].appId" -o tsv)
   
   # Service Principal作成
   az ad sp create --id $APP_ID
   
   # Federated Credential追加
   az ad app federated-credential create \
     --id $APP_ID \
     --parameters '{
       "name": "github-actions-integration-test",
       "issuer": "https://token.actions.githubusercontent.com",
       "subject": "repo:'"$GITHUB_ORG/$GITHUB_REPO"':ref:refs/heads/main",
       "audiences": ["api://AzureADTokenExchange"]
     }'
   
   # workflow_dispatch用の追加Credential（任意のブランチから実行可能に）
   az ad app federated-credential create \
     --id $APP_ID \
     --parameters '{
       "name": "github-actions-workflow-dispatch",
       "issuer": "https://token.actions.githubusercontent.com",
       "subject": "repo:'"$GITHUB_ORG/$GITHUB_REPO"':environment:integration-test",
       "audiences": ["api://AzureADTokenExchange"]
     }'
   ```

2. **サブスクリプションへの権限付与**

   ```bash
   SUBSCRIPTION_ID=$(az account show --query id -o tsv)
   SP_ID=$(az ad sp list --display-name $APP_NAME --query "[0].id" -o tsv)
   
   # Contributorロール付与
   az role assignment create \
     --assignee $SP_ID \
     --role Contributor \
     --scope /subscriptions/$SUBSCRIPTION_ID
   ```

3. **GitHub Secretsの設定**

   GitHub リポジトリの Settings > Secrets and variables > Actions で以下を設定：

   | Secret Name | Value |
   |-------------|-------|
   | `AZURE_CLIENT_ID` | App Registration の Client ID |
   | `AZURE_TENANT_ID` | Azure AD Tenant ID |
   | `AZURE_SUBSCRIPTION_ID` | Azure Subscription ID |

### GitHub Environment（推奨）

GitHub リポジトリで `integration-test` 環境を作成：

1. Settings > Environments > New environment
2. 名前: `integration-test`
3. 保護ルール（任意）: 手動承認、ブランチ制限など

## Usage

### 手動実行

1. GitHub リポジトリの **Actions** タブに移動
2. 左サイドバーから **Integration Test** を選択
3. **Run workflow** ボタンをクリック
4. パラメータを設定：
   - **Branch**: テスト対象ブランチ（デフォルト: main）
   - **Test Scope**: full / infra-only / app-only
   - **AKS SKU**: Base / Automatic
5. **Run workflow** をクリックして実行開始

### パラメータ説明

| Parameter | Options | Description |
|-----------|---------|-------------|
| `test_scope` | `full` | Bicep検証 + プロビジョニング + デプロイ + テスト |
| | `infra-only` | Bicep検証のみ（環境構築なし） |
| | `app-only` | 既存環境へのデプロイ + テスト（将来対応） |
| `aks_sku` | `Base` | 従来のAKSモード（デフォルト） |
| | `Automatic` | 自動化されたAKSモード |

## Workflow Structure

```
Integration Test Pipeline
├── validate (15min)
│   ├── Checkout
│   ├── Bicep build
│   └── Bicep what-if (optional)
│
├── provision (25min) [depends: validate]
│   ├── Azure Login (OIDC)
│   ├── Setup azd
│   ├── Create environment
│   └── azd provision
│
├── deploy (10min) [depends: provision]
│   ├── Azure Login (OIDC)
│   └── azd deploy
│
├── test (10min) [depends: deploy]
│   ├── Health check (/health)
│   ├── Redis integration test (/)
│   └── Report results
│
└── cleanup (15min) [always]
    ├── Azure Login (OIDC)
    ├── Delete resource group
    └── Delete azd environment
```

## Troubleshooting

### よくある問題

1. **OIDC認証エラー**
   - Federated Credentialのsubjectが正しいか確認
   - GitHubのワークフローで正しいpermissionsが設定されているか確認

2. **クォータエラー**
   - AKSノードのvCPUクォータを確認
   - 別のリージョンでの実行を検討

3. **タイムアウト**
   - AKS Automaticはプロビジョニングに時間がかかる場合あり
   - Baseモードでの実行を推奨

4. **クリーンアップ失敗**
   - Azure Portalで手動リソースグループ削除
   - `az group delete --name rg-inttest-{run_id} --yes`

## Local Development

統合テストをローカルで実行する場合：

```bash
# 環境変数設定
export INTEGRATION_TEST_URL="https://your-app.japaneast.cloudapp.azure.com"

# テスト実行
cd src
uv run pytest tests/integration/test_platform.py -v
```

## Cost Considerations

統合テスト環境の概算コスト（1回あたり）：

| Resource | Duration | Estimated Cost |
|----------|----------|----------------|
| AKS (Base, 2 nodes) | ~45min | ~$1.50 |
| Azure Managed Redis | ~45min | ~$0.50 |
| Container Registry | ~45min | ~$0.10 |
| その他 | ~45min | ~$0.20 |
| **合計** | | **~$2.30/回** |

※ 正確なコストは使用リソースとリージョンにより異なります
