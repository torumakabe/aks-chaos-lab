---
name: bicep-what-if-analysis
description: >-
  Bicep/ARM what-if 出力の分析、明示された what-if 実行、ノイズパターンの編集を支援する。
  「what-if を実行」「ノイズを分類」「Bicep 定義との差分を確認」「未分類のパターンを追加」で使用する。
  実行時は Azure を照会してローカルの使用統計を更新する。提供済み出力の分析では照会や編集を行わない。
  azd プロジェクト (azure.yaml 存在) と単体 Bicep デプロイの両方に対応。
---

# Bicep What-If Analysis

Bicep/ARM what-if の変更内容をノイズパターンと照合し、判断根拠を示す。

## 依頼に応じた操作

| 依頼 | 操作と副作用 |
|------|--------------|
| 提供済みの what-if 出力を分析する | 出力と必要な Bicep、パターン定義を読む。スクリプトを起動せず、Azure 照会、統計更新、パターン編集を行わない |
| what-if を実行する | 対象を特定してスクリプトを実行する。`az deployment sub what-if` による Azure 照会と、正常終了時の `scripts/patterns/pattern_stats.json` 更新が発生する。デプロイは行わない |
| ノイズパターンを追加、修正する | [パターン管理ガイド](references/pattern-guide.md) を読み、根拠と適用範囲を確認して依頼されたパターンだけを編集する |

what-if 実行を明示され、subscription、layer または template などの対象が決まっている場合は、同じ実行承認を再確認しない。対象が曖昧な場合だけ、必要な情報を確認する。

正常終了時の `pattern_stats.json` は解析履歴として保持する。`lastRun` と、マッチしたパターンの `lastMatched` / `matchCount` が未使用パターン警告に使われるため、生成差分を副作用として復元しない。既存出力の分析に、この更新契約を適用しない。

## 実行コマンド

以下はリポジトリルートから実行する。`--help` と `--list-layers` はローカル情報の表示だけで終了し、Azure 照会も統計更新も行わない。

```bash
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --help
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --list-layers
```

### azd プロジェクト (single layer)

```bash
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py"
```

### azd プロジェクト (multi layer)

`azure.yaml` の `infra.layers` を `--layer` で指定すると、`<path>/main.bicep` と `<path>/main.parameters.json` を自動採用し、parameters file 内の `${ENV_VAR}` / `${ENV_VAR:default}` を `azd env get-values` の値で解決する。

```bash
# 特定の layer (本リポ例: base / sli) を解析
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer sli
```

`--layer` 未指定で multi-layer リポを実行した場合は `./infra/main.bicep` (= base 相当) が解析され、stderr に `--layer` 利用を促す警告が出る。

### 非 azd プロジェクト

```bash
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" \
  --no-azd --subscription <subscription-id> \
  --location japaneast --template infra/main.bicep -p environmentName dev
```

パターンファイルは本スキルの `scripts/patterns/` 配下にある。

## 分析と報告

変更の要点、要確認または未分類のプロパティ、判断根拠を、依頼された詳しさで報告する。全文は求められた場合に示す。ノイズか乖離かの最終判断は人間が行う。

分類は安全性の保証ではない。text 出力に非表示の変更を知らせる警告がある場合は、その制限も伝える。提供された出力に必要な詳細がなければ未確認とし、分析依頼だけで再実行しない。

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

1. 対象の ARM スキーマを Azure MCP Server の bicepschema などで確認し、既定値や既知の what-if 制限は公開情報で調べる。
2. 根拠、対象リソース、プロパティ、分類を示す。分析だけの依頼なら、追加候補の報告で終了する。
3. パターン変更を依頼されている場合は、[パターン管理ガイド](references/pattern-guide.md) に従って `scripts/patterns/noise_patterns.json` を編集し、ローカルで検証する。確認済みの変更について承認を繰り返し求めない。

## オプション

| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `-f, --format` | `text` / `json` | `text` |
| `-t, --template` | Bicep ファイル | `./infra/main.bicep` (azd) / `./main.bicep` |
| `-l, --location` | Azure リージョン | azd から取得 |
| `-s, --subscription` | Azure subscription ID | azd の値または Azure CLI の既定値 |
| `--no-azd` | azd 自動検出を無効化 | off |
| `--layer` | `azure.yaml` の `infra.layers` から layer を指定し template / parameters file を自動解決 | - |
| `--list-layers` | `azure.yaml` の layer 一覧を tab 区切りで表示し終了 | - |
| `-p, --parameter` | パラメータの inline override (`KEY VALUE`) | - |
| `-v, --verbose` | 詳細ログ出力 | off |

## 前提条件

- スクリプト実行には uv と Python 3.14+。スクリプトは標準ライブラリのみを使用する。
- what-if 実行には Azure CLI と対象 subscription の認証、権限。
- azd 環境の値を使う場合は `azd` CLI。
