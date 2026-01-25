# ノイズ判定基準（プロセスベースアプローチ）

## 基本原則

**what-if分析は「リソースタイプに依存しない体系的プロセス」で行う**

- チェックリストに頼らない（未知のリソースに対応できない）
- すべてのプロパティ削除を同じプロセスで評価する
- Bicep定義との比較を必須とする

## 変更の分類

| 分類 | 定義 | 対応 |
|------|------|------|
| **ノイズ** | 読み取り専用プロパティの差分。デプロイしても適用されない | 無視 |
| **非破壊的変更** | リソース再作成は不要だが、実際に適用される | 報告（ノイズではない） |
| **破壊的変更** | リソースの再作成が必要 | 警告 |
| **サポート外** | 動的値により事前分析不可。デプロイは正常実行される | 説明（問題ではない） |

## 体系的分析プロセス（リソースタイプ非依存）

### ステップ1: 変更のあるリソースを全て抽出

```
what-if出力から以下を抽出:
- resourceCreations: すべて
- resourceDeletions: すべて
- changesWithDeletions: すべて（propertyDeletionCount > 0）
- destructiveChanges: すべて
```

**原則**: 特定のリソースタイプやプロパティに絞らない。すべてを同等に扱う。

### ステップ2: 各リソースの詳細を確認

```yaml
リソースごとに:
  1. what-if出力で削除されるプロパティを特定
     - トップレベルプロパティ（properties.xxx）
     - ネストされたプロパティ（properties.config.xxx）
     - 配列要素（properties.subnets[1].xxx）
  
  2. 配列要素の変更に注意
     例: ~ properties.subnets: [
           ~ 1:
             - properties.networkSecurityGroup: {...}
         ]
     これは「配列インデックス1の要素から networkSecurityGroup が削除」
```

### ステップ3: プロパティごとに評価（完全に一律）

**すべてのプロパティ削除**に対して、以下のプロセスを適用：

```
各削除プロパティに対して:

❶ Bicep定義を確認（grep/viewツール使用）
   - infra/**/*.bicep で該当リソースとプロパティを検索
   - 結果:
     → 定義されている: 次のステップへ
     → 定義されていない: 🔴 乖離として記録

❷ ARMリファレンスで readOnly を確認（microsoft_docs_search使用）
   - 検索: "Azure [リソースタイプ] ARM template [プロパティ名] readOnly"
   - 結果:
     → readOnly: true: ✅ ノイズとして記録
     → readOnly: false: 🔴 実削除として記録
     → 情報なし: ⚠️ 要調査として記録

❸ 結果を記録
   - 乖離、ノイズ、実削除、要調査のいずれかに分類
```

**重要**: 
- プロパティの種類による優先順位付けをしない
- すべてのプロパティを同じプロセスで評価する
- 事前のフィルタリングや「起きやすさ」の判断をしない

### ステップ4: 結果の分類と報告

評価完了後、結果を分類：

```
🔴 Critical（即座に対応必要）:
  - Bicep定義にないプロパティの削除（乖離）
  - destructiveChanges（リソース再作成）

⚠️ Warning（確認推奨）:
  - readOnly: false だが Bicep に定義されているプロパティの削除
    （予期しない動作の可能性）

ℹ️ Info（参考情報）:
  - readOnly: true のプロパティ削除（ノイズ）

❓ Investigate（追加調査必要）:
  - readOnly 情報が取得できないプロパティ
```

## スクリプトの自動フィルタ（既知のノイズパターンのみ）

以下は**既知の読み取り専用プロパティ**として自動除外される：

| プロパティパターン | 理由 |
|-----------------|------|
| `provisioningState` | すべてのリソースで読み取り専用 |
| `etag` | リソース更新のたびに変化 |
| `resourceGuid`, `uniqueId` | Azure が動的に生成 |
| `systemData.*` | 作成日時・更新日時等のメタデータ |
| `principalId`, `clientId`, `tenantId` | Managed Identity の読み取り専用プロパティ |

**重要**: 
- これらは**パターンマッチで明らかなノイズのみ**
- これ以外のすべてのプロパティは、上記のプロセスで評価する
- このリストに含まれないプロパティを「重要でない」と判断しない

## 参考: 配列要素の表示パターン（構造理解のため）

what-if出力では、配列要素の変更が以下のように表示される：

```yaml
~ Microsoft.Network/virtualNetworks/vnet-name
  ~ properties.subnets: [
    ~ 1:
      - properties.networkSecurityGroup: {...}
  ]
```

理解すべきこと：
- これは「VNetの変更」ではなく「サブネット（配列要素1）の変更」
- 配列を持つリソースは同様に表示される（securityRules, accessPolicies等）
- **すべての配列プロパティに同じプロセスを適用**

参考例（網羅的ではない）：
- `properties.subnets[]` - VirtualNetwork のサブネット
- `properties.securityRules[]` - NetworkSecurityGroup のルール
- `properties.accessPolicies[]` - Key Vault のアクセスポリシー

## プロパティ削除の判定フロー（完全に一律）

```
プロパティ削除を検出（どのプロパティでも同じフロー）
  ↓
❶ 配列要素の変更か？
   例: properties.subnets[1].networkSecurityGroup
   → インデックスとプロパティを特定
  ↓
❶ Bicep定義を確認（grep/viewツール）
   コマンド例: grep -r "プロパティ名" infra/**/*.bicep
   → 該当リソースのBicep定義にプロパティが存在する？
     YES: ❷へ
     NO:  🔴 乖離（Bicep定義にない）→ 報告
  ↓
❷ ARMリファレンスで readOnly を確認（microsoft_docs_search）
   検索例: "Microsoft.Network/virtualNetworks ARM template subnets readOnly"
   → readOnly: true?
     YES: ✅ ノイズ（読み取り専用）→ 報告
     NO:  ⚠️ 要調査（予期しない削除）→ 報告
     不明: ❓ 追加調査必要 → 報告
```

**重要な原則**:
- このフローは**すべてのプロパティ削除**に適用
- プロパティの種類による分岐なし
- リソースタイプによる分岐なし
- 「重要そう」「起きやすそう」という事前判断なし

## 破壊的変更（要注意）

| プロパティ | 影響 |
|-----------|------|
| `location` | リージョン変更は再作成必須 |
| `sku.name`, `sku.tier`, `sku.capacity` | リソースにより再作成 |
| `networkPlugin`, `networkPluginMode` | AKS は再作成必須 |
| `subnetId`, `vnetSubnetID`, `addressPrefixes` | ネットワーク構成変更 |

## よくある判断ミス

❌ `tags` の変更を「破壊的ではないからノイズ」と判断
✅ `tags` はユーザー設定可能。変更は実際に適用される

❌ ユーザー設定可能なプロパティの削除を「意図的な変更」と判断（Bicep定義を確認せず）
✅ **必ずBicep定義を確認する**。定義にないプロパティは「Bicepと実リソースの乖離」

❌ 「重要そうなプロパティ」「起きやすいプロパティ」を優先的に確認
✅ **すべてのプロパティ削除に同じプロセスを一律適用**。事前フィルタリングしない

❌ 同一リソースの変更をまとめて「すべてノイズ」と判断
✅ 各プロパティを個別に評価する

❌ `destructiveChanges`に含まれないリソースの詳細確認をスキップ
✅ `propertyDeletionCount > 0`のリソースは**すべて**詳細を確認

❌ 配列要素の変更（`properties.subnets[1]`等）を見逃す
✅ 配列要素の変更は親リソースの変更として表示されることを理解

❌ 特定のリソースタイプやプロパティのチェックリストに依存
✅ **プロセスベースアプローチ**で未知のリソースにも対応

❌ 「このプロパティは重要でないから後回し」と判断
✅ **すべてのプロパティを同等に扱い、プロセスの結果で判断**

❌ `potentiallyDestructive: false` のリソースを無視
✅ `changesWithDeletions` も確認する

## サポート外リソース（Unsupported）

what-if 分析で「サポート外」と判定されるリソースは、**デプロイ実行前に値を確定できない**ものです。

### よくあるパターン

| パターン | 例 |
|---------|-----|
| 動的リソースID | `guid()`, `uniqueString()` で生成 |
| 実行時参照 | `reference()`, `listKeys()` で取得 |
| 複雑な依存関係 | 他リソースのプロパティから構築 |

### 重要な注意点

✅ **これは問題ではありません**
- 分析できないだけで、デプロイは正常に実行されます
- 実際のデプロイ時には正しくリソースが作成/更新されます
- 既存リソースと同一であれば、変更は発生しません

⚠️ **報告時の注意**
- サマリーに「サポート外: N件」と記載したら、必ず詳細説明を含める
- 「サポート外」という言葉だけでは不安を与えるため、「分析対象外（問題なし）」と併記する
- 対象リソースが何で、なぜ分析できないのかを説明する

参考: https://aka.ms/WhatIfUnidentifiableResource

## 判断が難しい場合

1. **まずBicep定義を確認する**（特にタグ、SKU、ネットワーク設定などのユーザー設定可能なプロパティ）
2. `microsoft_docs_search` で「`<リソースタイプ> ARM template properties`」を検索
3. ARM リファレンス（`https://learn.microsoft.com/azure/templates/...`）で readOnly か確認
4. 「update limitations」「immutable」で検索

## プロパティ削除の報告テンプレート

```markdown
### プロパティ削除の分析

#### ✅ ノイズ（無視可能）
- `properties.provisioningState` - 読み取り専用
- `properties.resourceGuid` - Azure自動生成

#### ⚠️ Bicepと実リソースの乖離（要対応）
- `tags.CostControl: "Ignore"` - **Bicep定義に存在せず、手動追加されたもの**
  - 対応: 必要ならBicep定義に追加、不要ならデプロイ時に削除される
- `sku.capacity: 2` - **Bicep定義は1だが、実リソースは手動で2に変更**
  - 対応: Bicep定義を更新するか、デプロイ時に1に戻る

#### 🔍 要確認（readOnlyか不明）
- `properties.someProperty` - ARMリファレンス確認が必要
```
