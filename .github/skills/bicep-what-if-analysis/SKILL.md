---
name: bicep-what-if-analysis
description: >-
  Bicep/ARM what-if 出力の分析、適用前の差分確認、明示された what-if 実行、ノイズパターンの編集を支援する。
  「適用前の差分を確認」「what-if を実行」「ノイズを分類」「Bicep 定義との差分を確認」「未分類のパターンを追加」で使用する。
  実行時は Azure を照会してローカルの使用統計を更新する。提供済み出力の分析では照会や編集を行わない。
  azd プロジェクト (azure.yaml 存在) と単体 Bicep デプロイの両方に対応。
---

# Bicep What-If Analysis

Bicep/ARM what-if の変更内容をノイズパターンと照合し、判断根拠を示す。

## 依頼に応じた操作

| 依頼 | 操作と副作用 |
|------|--------------|
| 提供済みの what-if 出力を分析する | 出力と必要な Bicep、パターン定義を読む。スクリプトを起動せず、Azure 照会、統計更新、パターン編集を行わない |
| what-if を実行する | 対象を特定してスクリプトを実行する。通常は `az deployment sub what-if`、Stacks 設定のある layer は `az stack-whatif sub create` を使う。後者は保持期間1時間の Azure what-if 結果リソースを作成する。正常終了時に `pattern_stats.json` を更新する。対象リソースの配備や削除は行わない |
| ノイズパターンを追加、修正する | [パターン管理ガイド](references/pattern-guide.md) を読み、根拠と適用範囲を確認して依頼されたパターンだけを編集する |

適用前の差分確認として what-if が依頼され、subscription、layer または template などの対象が決まっている場合は、このスキルのスクリプトを実行する。Azure what-if の照会と解析履歴の更新を伴う。対象が曖昧な場合だけ、必要な情報を確認する。既存の `azd provision --preview` 出力をこのスクリプトの入力として扱わない。

azd プロジェクトでは、実行前に対象環境が選択されていることを確認する。`azure.yaml` に複数の `infra.layers` がある場合は `--layer` で対象を明示し、既定 layer への暗黙のフォールバックを使わない。`azd env get-values` の `AZURE_SUBSCRIPTION_ID` と `AZURE_LOCATION` が対象環境と一致することも確認する。what-if はスクリプトが受け取った Bicep とパラメーターを評価するものであり、azd hooks の処理は評価しない。

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

先に対象環境を選択します。

```bash
azd env select "<environment>"
```

`azure.yaml` の `infra.layers` を `--layer` で指定すると、`<path>/main.bicep` と `<path>/main.parameters.json` を自動採用し、parameters file 内の `${ENV_VAR}` / `${ENV_VAR=default}` を選択中の `azd env get-values` の値で解決する。`=default` は未設定または空の値に適用する。既存の `${ENV_VAR:default}` も従来どおり解決する。実行前に選択中環境の subscription と location が対象と一致することを確認する。

```bash
# 特定の layer (本リポ例: base / sli) を解析
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer sli
```

Stacks 設定のあるプロジェクトでは `--layer` が必須で、省略すると Azure 照会前に停止する。layer の `deploymentStacks` 設定から Stack 用 what-if を選択し、選択中環境の `azd-stack-<env>-<layer>` を評価する。初回作成前の Stack も指定できる。通常の ARM what-if や azd の preview へフォールバックしない。

Stack 用 what-if は、本リポジトリの `deleteAll`、deny mode `none` と同じ条件を使い、layer 設定が異なる場合は停止する。`create --no-pretty-print` はプロパティ変更を含む結果を取得する。JSON と text に結果 ID、削除予測、管理状態の変更、予測の確度、deny 設定の変更、diagnostics を残す。結果リソースは Azure が1時間後に削除する。`azd provision --preview` は azd 1.37.1 の Stacks 対応では使えず、通常の ARM what-if は Stack の管理除外による削除を評価しない。

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

Create は誤検知候補も含めてリソース名を表示し、候補には要確認の注記を付ける。型の一致や状態情報の不足だけでは、正当な新規作成と誤検知を区別できない。JSON の `likelyFalsePositive` と `createFalsePositives` は候補のフラグと件数であり、作成不要と確定した結果ではない。

適用前確認では、対象環境、subscription、layer と what-if の完了を確認してから、Create / Modify / Delete を予定した変更に照合する。Delete、RBAC / identity、ネットワーク、セキュリティ設定、共有リソースは個別に確認する。`⚠️`、`❓`、`pending` は必要に応じて Bicep、既存リソースの状態、ARM schema と照合する。`noise_confirmed`、`📘`、`notDefined` も許可判定ではなく、個別の差分と根拠を確認するための情報として扱う。差分が省略された警告がある場合は JSON 出力を使う。未説明の差分、予期しない削除、必要な差分の欠落、対象外の hook 操作が残る場合は適用を止める。判断根拠を説明できる差分だけになった後、適用可否は人間が決める。azd layer の適用前手順は [deployment guide](../../../docs/deployment.md#bicep-の適用前差分確認) を参照する。

```
Resources:
  Skip     : Resource group          : rg-xxx
  Modify   : AKS Managed Cluster     : aks-xxx
      - tags.CostControl  ⚠️ カスタムタグは運用ポリシーに依存するため要確認
      - properties.enableRBAC  📘 変更前が既定値: RBAC 有効化は AKS デフォルト。差分は要確認
      * properties.provisioningState  🔒 readOnly（Azure 自動設定）
```

| 記号 | 意味 |
|------|------|
| `-` | 削除 |
| `+` | 追加 |
| `*` | 変更 |
| 🔒 | readOnly（Azure 自動設定） |
| 📘 | 変更前後のどちらが既定値に一致したかを示す情報 |
| ⚠️ | 要確認（自動設定の条件やARM参照式の解決結果を含む） |
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
| `--stack-name` | subscription Stack の名前を明示する。`deleteAll`、deny mode `none` で保持期間1時間の what-if 結果を作成する | Stack layer では azd 環境名と layer から生成 |
| `--list-layers` | `azure.yaml` の layer 一覧を tab 区切りで表示し終了 | - |
| `-p, --parameter` | パラメータの inline override (`KEY VALUE`) | - |
| `-v, --verbose` | 詳細ログ出力 | off |

## 前提条件

- スクリプト実行には uv と Python 3.14+。スクリプトは標準ライブラリのみを使用する。
- what-if 実行には Azure CLI と対象 subscription の認証、権限。
- azd 環境の値を使う場合は `azd` CLI。
