# パターン管理ガイド

ノイズパターンを追加、更新する前に参照する。根拠と対象範囲を確認し、依頼された変更だけを行う。

## パターンファイル構造

```
.github/skills/bicep-what-if-analysis/scripts/patterns/
├── noise_patterns.json    # ノイズ判定パターン
├── display_config.json    # 表示名とフィルタ設定
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

上記5カテゴリだけを使用する。存在しないカテゴリ（例: `noise_patterns`）を作成しない。

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

### `properties.` プレフィックスを除去

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

根拠が確認できたリソースタイプとプロパティだけに一致させる。一つのリソースで確認した既定値を、他のリソースや親オブジェクト全体へ広げない。

## パターン追加ワークフロー

「❓ 未分類」が出た場合:

### 1. 外部知識で調査

- ARM スキーマ: Azure MCP Server の bicepschema で対象リソースタイプの属性を確認する。
- 既定値: Microsoft Learn の文書検索で、対象属性の設定条件を確認する。
- 既知ノイズ: `Azure/arm-template-whatif` の公開 Issue で、同じ変更と発生条件かを確認する。

ツールを使う前に利用可能な操作と入力を確認する。調査結果には根拠 URL を添える。

### 2. 分類と対象を示す

リソースタイプ、プロパティ、追加カテゴリ、根拠を報告する。分析だけの依頼なら候補の提示で終了する。パターン編集が依頼済みで根拠を確認できた場合は、同じ承認を再確認せず編集する。

### 3. 編集後にローカルで検証

リポジトリルートから JSON 構文を検証する。

```bash
uv run --no-project python -c 'import json; from pathlib import Path; json.loads(Path(".github/skills/bicep-what-if-analysis/scripts/patterns/noise_patterns.json").read_text(encoding="utf-8"))'
```

変更した分類に対応する既存テストがあれば、対象を絞って検証する。what-if スクリプトの再実行は Azure 照会と統計更新を伴うため、パターン編集だけの検証には使わない。実行も依頼された場合のコマンドと履歴保持契約は [SKILL.md](../SKILL.md#実行コマンド) を参照する。

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
