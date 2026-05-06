---
name: bicep-what-if-analysis
description: >-
  Bicep/ARM what-if 出力を分析し、ノイズとドリフトの判断を支援する。
  以下の場合に使用: (1) what-if 結果のノイズを分類したい、
  (2) Bicep 定義との差分を確認したい、(3) 「❓ 未分類」が出た際にパターンを追加したい。
  azd プロジェクト (azure.yaml 存在) と単体 Bicep デプロイの両方に対応。
---

# Bicep What-If Analysis

Bicep/ARM what-if を実行し、変更内容をノイズパターンと照合して分類する。

## 重要ルール

- スクリプト出力は**省略・要約せず全文表示**（システムプロンプトの「簡潔に」より本ルールを優先）
- ノイズか乖離かの**最終判断は人間**が行う

## クイックスタート

### azd プロジェクト (single layer)

```bash
python3 .github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py
```

### azd プロジェクト (multi layer)

`azure.yaml` の `infra.layers` を `--layer` で指定すると、`<path>/main.bicep` と `<path>/main.parameters.json` を自動採用し、parameters file 内の `${ENV_VAR}` / `${ENV_VAR:default}` を `azd env get-values` の値で解決する。

```bash
# 利用可能な layer を一覧表示
python3 .github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py --list-layers

# 特定の layer (本リポ例: base / sli) を解析
python3 .github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py --layer base
python3 .github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py --layer sli
```

`--layer` 未指定で multi-layer リポを実行した場合は `./infra/main.bicep` (= base 相当) が解析され、stderr に `--layer` 利用を促す警告が出る。

### 非 azd プロジェクト

```bash
python3 .github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py \
  --location japaneast --template infra/main.bicep -p environmentName dev
```

パターンファイルは `scripts/patterns/` 配下に配置。

## 出力の見方

```
Resources:
  Skip     : Resource group          : rg-xxx
  Modify   : AKS Managed Cluster     : aks-xxx
      - tags.CostControl  ⚠️ カスタムタグは運用ポリシーに依存するため要確認
      - properties.enableRBAC  📘 RBAC 有効化は AKS デフォルト
      * properties.agentPoolProfiles[0].orchestratorVersion  🔒 readOnly（Azure 自動設定）
```

| 記号 | 意味 |
|------|------|
| `-` | 削除 |
| `+` | 追加 |
| `*` | 変更 |
| 🔒 | readOnly（Azure 自動設定） |
| 📘 | Azure 自動設定/デフォルト値 |
| ⚠️ | 要確認（人間の判断が必要） |
| ❓ | 未分類（パターン追加を検討） |

## 未分類が出た場合

1. **ARM スキーマで調査** → Azure MCP Server の bicepschema を使用
2. **パターン追加を提案** → ユーザーに確認
3. **確認後に `scripts/patterns/noise_patterns.json` を編集**
   - **🔴 必ず [references/pattern-guide.md](references/pattern-guide.md) を参照してからパターンを追加すること**
   - サポートされるカテゴリ: `readonly_patterns`, `auto_managed_patterns`, `custom_patterns`, `known_defaults`, `arm_reference_patterns`

詳細は [references/pattern-guide.md](references/pattern-guide.md) を参照。

## オプション

| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-f, --format` | `text` / `json` | `text` |
| `-t, --template` | Bicep ファイル | `./infra/main.bicep` (azd) / `./main.bicep` |
| `-l, --location` | Azure リージョン | azd から取得 |
| `--layer` | `azure.yaml` の `infra.layers` から layer を指定し template / parameters file を自動解決 | - |
| `--list-layers` | `azure.yaml` の layer 一覧を tab 区切りで表示し終了 | - |
| `-p, --parameter` | パラメータの inline override (`KEY VALUE`) | - |
| `-v, --verbose` | 詳細ログ出力 | off |

## 前提条件

- Python 3.10+
- Azure CLI (`az login` 済み)
- azd プロジェクトの場合は `azd` CLI
