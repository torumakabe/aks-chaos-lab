# ノイズ判定基準（詳細）

## 使いどころ
- what-if の Modify が多く、差分が読みにくい場合
- 破壊的変更かどうか判断に迷う場合

## 目次
- 無視して良い変更（代表例）
- 要注意の変更（代表例）
- 判断が難しい場合の確認方法

## 無視して良い変更（代表例）

| カテゴリ | プロパティ | 理由 |
|---------|-----------|------|
| 全リソース共通 | `provisioningState` | 読み取り専用（デプロイ後に設定される） |
| 全リソース共通 | `etag` | リソース更新のたびに変化 |
| 全リソース共通 | `resourceGuid`, `uniqueId` | Azure が動的に生成 |
| 全リソース共通 | `systemData.*` | 作成日時・更新日時等のメタデータ |
| Managed Identity | `principalId`, `clientId`, `tenantId` | 読み取り専用（作成後に設定される） |

## 要注意の変更（代表例）

| カテゴリ | プロパティ | 影響 |
|---------|-----------|------|
| 全リソース共通 | `location` | リージョン変更は再作成必須 |
| 全リソース共通 | `kind` | リソース種別の変更 |
| 全リソース共通 | `sku.name`, `sku.tier`, `sku.capacity` | SKU変更（リソースにより再作成） |
| ネットワーク系 | `subnetId`, `vnetSubnetID`, `addressPrefixes` | ネットワーク構成の根本変更 |
| AKS | `networkPlugin`, `networkPluginMode` | ネットワークプラグイン変更は再作成必須 |

## 判断が難しい場合の確認方法

1. **Microsoft Learn でリソース仕様を確認**
   - `MS-Learn-microsoft_docs_search` ツールで「`<リソースタイプ> ARM template properties`」を検索
   - 例: `AKS ARM template properties`, `Storage Account Bicep reference`

2. **ARM/Bicep リファレンスで読み取り専用プロパティを確認**
   - URL パターン: `https://learn.microsoft.com/azure/templates/<provider>/<resource-type>`
   - 例: `https://learn.microsoft.com/azure/templates/microsoft.containerservice/managedclusters`
   - 「readOnly」「output only」と記載されたプロパティはノイズ

3. **破壊的変更の判断**
   - `MS-Learn-microsoft_docs_search` ツールで「`<リソースタイプ> update limitations`」や「`<プロパティ名> immutable`」を検索
   - リソースの「制限事項」「更新の制約」セクションを確認
