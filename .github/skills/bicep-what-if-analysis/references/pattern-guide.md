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
| `readonly_patterns` | ARM readOnly プロパティ | 🔒 | `provisioningState`, `etag` |
| `arm_reference_patterns` | ARM 参照式の検出（解決結果は要確認） | ⚠️ | `[reference(`, `[resourceId(` |
| `auto_managed_patterns` | 自動設定の候補（適用条件と値は要確認） | ⚠️ | `identityProfile`, `addonProfiles` |
| `custom_patterns` | 要確認（人間の判断が必要） | ⚠️ | `orchestratorVersion`, `networkSecurityGroup` |
| `known_defaults` | 変更前後それぞれの既定値との一致 | 📘 | `enableRBAC=true` |

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

次はリソースタイプ別の readOnly パターンの例。

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

リソースタイプ別パターンと、共通の `auto_managed_patterns`、`custom_patterns`、`known_defaults` は、先頭の `properties.` を除去してから照合する。これらのパターンにはプレフィックスを含めない。

```json
❌ "^properties\\.enableRBAC$"
✅ "^enableRBAC$"

❌ {"path": "properties.enableRBAC", "value": true}
✅ {"path": "enableRBAC", "value": true}
```

例外: `sku.tier` など `properties` 配下でないプロパティはそのまま記述。

共通の `readonly_patterns` は元のパスで照合する。`^name$` はリソース名だけに一致し、`properties.name` など設定辞書内の同名キーには一致させない。プロビジョニング状態は `^properties\.provisioningState$` と記述する。

### パターン追加先の原則

1. **原則**: `resource_types` 配下のリソース別に追加
2. **例外**: 全リソース共通のもののみ `common` に追加
3. **迷う場合**: `common` ではなく該当リソース配下に `custom_patterns` として追加

根拠が確認できたリソースタイプとプロパティだけに一致させる。一つのリソースで確認した既定値を、他のリソースや親オブジェクト全体へ広げない。

`kind` は全リソース共通の readOnly ではない。例えば Storage Account では [StorageV2 への更新](https://learn.microsoft.com/azure/storage/common/storage-account-upgrade#upgrade-an-account)に使うため、共通の `readonly_patterns` に追加しない。

Data Collection Endpoint の [`properties`](https://learn.microsoft.com/azure/templates/microsoft.insights/2024-03-11/datacollectionendpoints)には、設定可能な `networkAcls.publicNetworkAccess` などが含まれる。差分が親オブジェクト単位で返る場合もあるため、`properties` 全体を readOnly にしない。

同様に、[Fleet](https://learn.microsoft.com/azure/templates/microsoft.containerservice/fleets#resource-format) の `properties.hubProfile`、[User Assigned Identity](https://learn.microsoft.com/rest/api/managedidentity/user-assigned-identities/create-or-update?view=rest-managedidentity-2024-11-30) の `properties.isolationScope`、[Monitor Account](https://learn.microsoft.com/azure/templates/microsoft.monitor/2025-10-03-preview/accounts#resource-format) の `properties.publicNetworkAccess`、[Log Analytics Table](https://learn.microsoft.com/azure/templates/microsoft.operationalinsights/2025-07-01/workspaces/tables#resource-format) の `properties.schema.columns` は設定可能な属性を含む。これらの `properties` や `schema` 全体を readOnly にしない。

### 候補の表示と確定評価を分ける

`auto_managed_patterns` の一致はパスだけを確認する。説明文にある未指定時の補完、値の等価性、サービスの動作条件を確認した結果ではないため、出力は要確認とし、説明文を実際の差分の原因として表示しない。Bicep 照合の `notDefined` も、変数やモジュールを通じた指定がないことの証明には使わない。

`known_defaults` は正規化後のパスと値を照合し、変更前と変更後のどちらが既定値に一致したかを表示する。別の階層や似た名前への末尾一致は使わず、`null` や値の欠落から既定値への復帰を推定しない。既定値への一致だけでは差分をノイズ確定にしない。

ARM 参照式は、その存在だけでは変更前後の値が等しいと判断できない。[what-if の制限](https://learn.microsoft.com/azure/azure-resource-manager/bicep/deploy-what-if#view-results)を踏まえ、解決結果を比較できない差分は `pending` とし、要確認の注記を付ける。`NoEffect` と readOnly に該当する差分は、それぞれの根拠による `noise_confirmed` を維持する。

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
| 🔒 | **readOnly** | ARM スキーマで readOnly、ユーザー制御不可 | `provisioningState`, `etag` |
| 📘 | **既定値情報** | 変更前後のどちらが既定値に一致したかを表示 | `enableRBAC` |
| ⚠️ | **要確認** | 自動設定の条件や参照式の解決結果など、人間の判断が必要 | `identityProfile`, `orchestratorVersion`, `networkSecurityGroup` |
| ❓ | **未分類** | パターンにマッチしない、調査が必要 | - |

## 禁止事項

- 「Azure が自動作成した」と根拠なく断定しない
- セキュリティ関連リソースを安易にノイズ扱いしない
- ARMスキーマ確認なしでパターン追加しない
- JSON構文検証なしでコミットしない
