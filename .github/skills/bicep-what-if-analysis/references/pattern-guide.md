# パターン管理ガイド

ノイズパターンの追加・更新時に参照するガイド。

## パターンファイル構造

```
scripts/patterns/
├── noise_patterns.json    # ノイズ判定パターン
├── display_config.json    # 表示名・フィルタ設定
└── pattern_stats.json     # 使用統計（自動生成）
```

## パターンカテゴリ

| カテゴリ | 用途 | 出力記号 | 例 |
|---------|------|---------|-----|
| `readonly_patterns` | ARM readOnly プロパティ | 🔒 | `provisioningState`, `etag`, `kind` |
| `arm_reference_patterns` | ARM 参照式 | 🔒 | `[reference(`, `[resourceId(` |
| `auto_managed_patterns` | Azure 自動管理 | 📘 | `identityProfile`, `addonProfiles` |
| `custom_patterns` | 要確認（人間の判断が必要） | ⚠️ | `orchestratorVersion`, `networkSecurityGroup` |
| `known_defaults` | 既知のデフォルト値 | 📘 | `enableRBAC=true` |

**🔴 上記5カテゴリのみサポート。存在しないカテゴリ（例: `noise_patterns`）を作成しないこと。**

## JSONスキーマ

### 全体構造

```json
{
    "common": {
        // 全リソース共通のパターン
    },
    "resource_types": {
        "Microsoft.ContainerService/managedClusters": {
            // AKS固有のパターン
        },
        "Microsoft.Network/virtualNetworks": {
            // VNet固有のパターン
        }
    }
}
```

### カテゴリ別データ形式

各カテゴリでデータ形式が異なる。**形式を間違えるとスクリプトがエラーになる。**

#### `readonly_patterns`, `arm_reference_patterns`: 文字列の配列

```json
"readonly_patterns": [
    "^provisioningState$",
    "^etag$"
]
```

#### `auto_managed_patterns`, `custom_patterns`: オブジェクトの配列（pattern + description 必須）

```json
"auto_managed_patterns": [
    {"pattern": "^identityProfile$", "description": "マネージドID情報は自動生成"},
    {"pattern": "^addonProfiles$", "description": "アドオンはBicep設定に基づきAzureが自動構成"}
]
```

#### `known_defaults`: オブジェクトの配列（path + value + description 必須）

```json
"known_defaults": [
    {"path": "enableRBAC", "value": true, "description": "RBAC有効化はAKSデフォルト"},
    {"path": "networkProfile.ipFamilies", "value": ["IPv4"], "description": "IPv4がデフォルト"}
]
```

## パターン記述ルール

### 🔴 重要: `properties.` プレフィックスを除去

スクリプトは内部で `properties.` を除去してからマッチング。パターンには含めない。

```json
❌ "^properties\\.enableRBAC$"
✅ "^enableRBAC$"

❌ {"path": "properties.enableRBAC", "value": true}
✅ {"path": "enableRBAC", "value": true}
```

例外: `sku.tier` など `properties` 配下でないプロパティはそのまま記述。

### パターン追加先の原則

1. **原則**: `resource_types` 配下のリソース別に追加
2. **例外**: 全リソース共通のもののみ `common` に追加
3. **迷う場合**: `common` ではなく該当リソース配下に `custom_patterns` として追加

## パターン追加ワークフロー

「❓ 未分類」が出た場合:

### 1. 外部知識で調査

```
# ARM スキーマ確認（Azure MCP Server）
bicepschema get:
  resource-type: "Microsoft.ContainerService/managedClusters"

# MS Learn で既定値調査
microsoft_docs_search:
  query: "AKS managed cluster default properties"

# GitHub で既知ノイズ確認（GitHub MCP Server）
search_issues:
  owner: "Azure"
  repo: "arm-template-whatif"
  query: "noise"
```

### 2. パターン追加を提案

```
以下のパターン追加を提案します：

【readonly_patterns に追加】
- kind: ARMスキーマでReadOnly確認済み

【auto_managed_patterns に追加】
- aadProfile.tenantID: Azure ADテナントIDは自動設定

【custom_patterns に追加】
- addonProfiles: 環境依存、手動確認推奨
```

### 3. ユーザー確認後に実行

```bash
# JSON構文検証
python3 -c 'import json; json.load(open("scripts/patterns/noise_patterns.json", encoding="utf-8"))'

# 再実行して確認
python3 scripts/what_if_analyzer.py
```

## 分類基準

| 出力記号 | 分類 | 基準 | 例 |
|---------|-----|-----|-----|
| 🔒 | **readOnly** | ARM スキーマで readOnly、ユーザー制御不可 | `provisioningState`, `kind` |
| 📘 | **自動設定/デフォルト** | Azure が自動設定またはデフォルト値 | `identityProfile`, `enableRBAC` |
| ⚠️ | **要確認** | 人間の判断が必要、ドリフトの可能性 | `orchestratorVersion`, `networkSecurityGroup` |
| ❓ | **未分類** | パターンにマッチしない、調査が必要 | - |

## 禁止事項

- 「Azure が自動作成した」と根拠なく断定しない
- セキュリティ関連リソースを安易にノイズ扱いしない
- ARMスキーマ確認なしでパターン追加しない
- JSON構文検証なしでコミットしない
