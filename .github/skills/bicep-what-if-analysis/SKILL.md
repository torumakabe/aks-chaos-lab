---
name: bicep-what-if-analysis
description: azd up/azd provisionの影響分析、Bicep what-if実行とノイズフィルタリング。インフラ変更・デプロイ前の影響確認時に使用。
---

# Bicep What-If 分析

インフラ変更前の影響範囲確認とノイズフィルタリングを支援します。
任意の azd プロジェクトで使用可能です。

## Tools

| Tool | Use For |
|------|---------|
| `microsoft_docs_search` | ノイズ判断が難しい場合のドキュメント検索 |
| `microsoft_docs_fetch` | 詳細なプロパティ仕様の取得 |

## ⚠️ このスキルを使うべき状況

以下のいずれかに該当する場合、**必ずこのスキルを呼び出してください**：

| ユーザーの質問・依頼 | 理由 |
|---------------------|------|
| 「azd up の影響は？」「azd provision で何が変わる？」 | 実際のAzure環境との差分が必要 |
| 「このBicep変更の影響を教えて」 | 静的分析では不十分、what-ifが必要 |
| 「デプロイしても大丈夫？」「破壊的変更はある？」 | リスク評価にwhat-if結果が必須 |
| 「インフラの変更点を確認したい」 | 現在の状態との比較が必要 |

**静的コード分析（Bicepファイルを読むだけ）では不十分です。**
What-if は Azure Resource Manager API を呼び出し、**現在デプロイ済みの状態との実際の差分**を取得します。

## クイックスタート

```bash
# フィルタ済みwhat-if分析（推奨）
# azdプロジェクトのルートディレクトリで実行
./.github/skills/bicep-what-if-analysis/scripts/whatif-analyze.sh

# 生出力が必要な場合
./.github/skills/bicep-what-if-analysis/scripts/whatif-analyze.sh --raw

# カスタムテンプレートとパラメータを指定
./.github/skills/bicep-what-if-analysis/scripts/whatif-analyze.sh \
  --template infra/custom.bicep \
  --parameters "vmSize=Standard_D2s_v3" \
  --parameters "nodeCount=3"

# 破壊的変更のみ抽出
./.github/skills/bicep-what-if-analysis/scripts/whatif-analyze.sh | jq '.changes[] | select(.changeType == "Delete")'
```

## 前提条件

- `azd env`が初期化済み（`AZURE_LOCATION`等が設定済み）
- Azure CLIでログイン済み
- azdプロジェクトのルートディレクトリで実行（`azure.yaml`が存在する場所）

## ノイズ判定基準

以下は代表的なパターンです。**判断が難しい場合は、動的に公式ドキュメントを参照してください。**

### 無視して良い変更（代表例）

| カテゴリ | プロパティ | 理由 |
|---------|-----------|------|
| 全リソース共通 | `provisioningState` | 読み取り専用（デプロイ後に設定される） |
| 全リソース共通 | `etag` | リソース更新のたびに変化 |
| 全リソース共通 | `resourceGuid`, `uniqueId` | Azure が動的に生成 |
| 全リソース共通 | `systemData.*` | 作成日時・更新日時等のメタデータ |
| Managed Identity | `principalId`, `clientId`, `tenantId` | 読み取り専用（作成後に設定される） |

### 要注意の変更（代表例）

| カテゴリ | プロパティ | 影響 |
|---------|-----------|------|
| 全リソース共通 | `location` | リージョン変更は再作成必須 |
| 全リソース共通 | `kind` | リソース種別の変更 |
| 全リソース共通 | `sku.name`, `sku.tier`, `sku.capacity` | SKU変更（リソースにより再作成） |
| ネットワーク系 | `subnetId`, `vnetSubnetID`, `addressPrefixes` | ネットワーク構成の根本変更 |
| AKS | `networkPlugin`, `networkPluginMode` | ネットワークプラグイン変更は再作成必須 |

### 🔍 判断が難しい場合の確認方法

上記リストは網羅的ではありません。不明なプロパティや変更の影響が判断できない場合は、以下を確認してください：

1. **Microsoft Learn でリソース仕様を確認**
   - `microsoft_docs_search` ツールで「`<リソースタイプ> ARM template properties`」を検索
   - 例: `AKS ARM template properties`, `Storage Account Bicep reference`

2. **ARM/Bicep リファレンスで読み取り専用プロパティを確認**
   - URL パターン: `https://learn.microsoft.com/azure/templates/<provider>/<resource-type>`
   - 例: `https://learn.microsoft.com/azure/templates/microsoft.containerservice/managedclusters`
   - 「readOnly」「output only」と記載されたプロパティはノイズ

3. **破壊的変更の判断**
   - `microsoft_docs_search` ツールで「`<リソースタイプ> update limitations`」や「`<プロパティ名> immutable`」を検索
   - リソースの「制限事項」「更新の制約」セクションを確認

## 分析フロー

```
1. what-if実行
   ↓
2. 変更タイプで分類
   - NoChange / NoEffect / Ignore → 無視
   - Create / Delete / Modify → 詳細確認
   ↓
3. Modifyの詳細確認
   - ノイズリストに該当 → 無視
   - 破壊的変更リストに該当 → 要注意
   - それ以外 → 意図した変更か確認
```

## 変更タイプの意味

| タイプ | 説明 | 対応 |
|--------|------|------|
| **Create** | 新規作成 | 設定内容を確認 |
| **Delete** | 削除 | 意図した削除か確認 |
| **Modify** | 変更あり | 破壊的変更か確認 |
| **NoChange** | 変更なし | 無視 |
| **NoEffect** | 影響なし（読み取り専用等） | 無視 |
| **Ignore** | 評価対象外 | 通常は無視 |

## トラブルシューティング

### "AZURE_LOCATION is not set" エラー

```bash
# azd環境を初期化
azd env refresh
```

### what-ifが大量の変更を報告する

- APIバージョン更新後は多くの「変更」が報告されることがあります
- `--raw`オプションで生出力を確認し、実際の差分を確認してください

### 特定リソースの詳細を確認したい

```bash
# リソースIDでフィルタ（例：AKSクラスター）
./.github/skills/bicep-what-if-analysis/scripts/whatif-analyze.sh | \
  jq '.changes[] | select(.resourceId | contains("managedClusters"))'

# リソースタイプでフィルタ（例：ストレージアカウント）
./.github/skills/bicep-what-if-analysis/scripts/whatif-analyze.sh | \
  jq '.changes[] | select(.resourceType | contains("storageAccounts"))'
```

### カスタムパラメータを使用したい

```bash
# azure.yaml から自動検出せず、明示的にテンプレートとパラメータを指定
./.github/skills/bicep-what-if-analysis/scripts/whatif-analyze.sh \
  --template infra/main.bicep \
  --parameters "environment=dev" \
  --parameters "sku=Standard"
```

## このスキルを使わない場合

以下の場合は静的分析で十分です：

- 「Bicepファイルの構文を確認して」（リンター実行）
- 「このモジュールは何を作成する？」（コードリーディング）
- 「パラメータのデフォルト値は？」（ファイル参照）
