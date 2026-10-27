---
name: bicep-what-if-analysis
description: >-
  Azure Bicep / ARM what-if の実行と結果分析を支援する。
  「適用前の差分を確認」「what-if を実行」「what-if 結果を分析」で使用する。
  azd の複数 layer と Deployment Stacks に対応する。
---

# Bicep What-If Analysis

what-if の結果を使い、リソースごとに予定される操作とプロパティ差分を説明する。what-if は予測であり、適用結果や適用の許可を示さない。

## 依頼に応じた操作

| 依頼 | 実施内容 |
|---|---|
| what-if を実行する | 対象環境と layer を確認し、このスキルの分析器を実行する。Azureに問い合わせ、正常終了時に解析履歴を更新する。リソースの適用や削除はしない |
| 提供済みの what-if 結果を分析する | 提供された結果と必要な Bicep を読む。分析器を実行せず、Azure照会やファイル編集をしない |
| ノイズパターンを編集する | ユーザーが明示的に依頼した場合だけ、[パターン管理ガイド](references/pattern-guide.md)に従う |

## what-if の実行

リポジトリルートで実行する。Stacks 設定がある場合、分析器は `az stack-whatif sub create` を使い、通常の ARM what-if や `azd provision --preview` に切り替えない。Stack what-if 結果は Azure に1時間保持され、正常終了時は `scripts/patterns/pattern_stats.json` が更新される。

azd の対象環境を選び、subscription と location を確認する。複数 layer があるプロジェクトでは対象を `--layer` で明示する。

```bash
azd env select "<environment>"
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" --layer base
```

対象の layer に応じて `base` を `sli` などへ置き換える。`--help` と `--list-layers` はローカル情報だけを表示し、Azureへ問い合わせない。
Stacks 設定があるプロジェクトでは、layer 数にかかわらず `--layer` を指定する。省略時に別の layer や通常の ARM what-if へ切り替えない。

非 azd プロジェクトでは template、subscription、location を明示する。

```bash
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" \
  --no-azd --subscription <subscription-id> \
  --location japaneast --template infra/main.bicep -p environmentName dev
```

## 結果の読み方と報告

出力先頭の操作件数、Delete の予測確度、Stack 診断を確認する。その後 Create、Modify、Delete、Detach、Unsupported の対象リソースを確認する。Modify の各差分は、リソース名、プロパティパス、変更前値、変更後値を示す。Stack の管理状態や deny 設定の変更は、リソースプロパティと分けて説明する。テキスト出力で一部を省略した場合は、省略件数と `--format json` で詳細を確認できることを伝える。

Stack what-if に `ShortCircuitedResourceId` がある場合、ARM は実行時参照を含むリソース ID を解決できず、確定差分のない管理対象を `potential Delete` として報告することがある。この結果だけでは、テンプレートからリソースが外れたことも、適用時に削除されることも示さない。診断、変更確度、管理状態を確認し、必要なら削除候補の ID を Stack の管理対象一覧と照合する。診断だけを根拠に Bicep の ID 生成方法を変更案として示さない。

`provisioningState` や `etag` など read-only と分類したプロパティも、変更前後の値と分類理由を表示する。分類だけを理由に差分を省略しない。read-only と確認できないプロパティは設定差分として扱う。JSON には全差分を残す。

Bicep 照合はソース検索による参考情報である。検索結果がないことを「Bicepで管理されていない」と断定しない。参照箇所を確認できない場合は、そのまま未確認と伝える。noise pattern、`pending`、`noise_confirmed`、`notDefined` は事実の説明を補助する情報であり、安全性や適用可否の結論に使わない。

各差分について、what-if の予測、確認できた Bicep の根拠、未確認事項を区別して報告する。適用可否は自動判定しない。適用前に確認する項目は [deployment guide](../../../docs/deployment.md#bicep-の適用前差分確認) を参照する。

提供された結果だけでは変更前後の値や対象リソースを確認できない場合、推測で補わず、確認できない情報を明記する。分析依頼だけを受けた場合は、what-ifを再実行しない。

## オプション

| オプション | 説明 |
|---|---|
| `-f, --format` | `text` または `json` |
| `-t, --template` | Bicep template |
| `-l, --location` | Azure region |
| `-s, --subscription` | Subscription ID |
| `--no-azd` | azd 自動検出を無効化 |
| `--layer` | `azure.yaml` の infra layer |
| `--stack-name` | Stack 名を明示 |
| `--list-layers` | azd layer 一覧を表示して終了 |
| `-p, --parameter` | inline parameter。`KEY VALUE` を指定 |

実行には uv、Python 3.14 以上、Azure CLI の認証が必要。azd 環境値を使う場合は azd CLI も必要。
