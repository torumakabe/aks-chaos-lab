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
| what-if を実行する | 対象環境と layer を確認し、このスキルの分析器を実行する。Azureに問い合わせるが、対象リソースの適用や削除はしない |
| 提供済みの what-if 結果を分析する | 提供された結果と必要な Bicep を読む。分析器を実行せず、Azure照会やファイル編集をしない |
| ノイズパターンを編集する | ユーザーが明示的に依頼した場合だけ、[パターン管理ガイド](references/pattern-guide.md)に従う |

## what-if の実行

リポジトリルートで実行する。Stacks 設定がある場合、分析器は `az stack-whatif sub create` を使い、通常の ARM what-if や `azd provision --preview` に切り替えない。Stack what-if 結果は Azure に1時間保持される。

azd の対象環境を選び、subscription と location を確認する。複数 layer があるプロジェクトでは対象を `--layer` で明示する。結果や診断には機微情報が含まれ得るため、標準出力と標準エラーをアクセス制限したファイルに保存する。認証情報や接続文字列の実値は報告に含めない。対象を識別するリソース ID は報告に使える。

以下の例はリポジトリ内の `tmp/` を使う。委譲する場合は、Azure 実行とファイル保存を担当するエージェントが、その操作を許可されているか先に確認する。保存を禁止されている担当には繰り返し保存を指示せず、操作が許可されている実行担当が保存し、分析担当にはファイルの参照先と結果 ID を渡す。実行できる担当がいない場合は、実際の制約、未実施の操作と必要な対応を報告する。権限の拒否を別の担当や保存先で回避しない。

調査に必要な間は保存ファイルを保持する。報告本文に必要な差分、診断と結果 ID が揃っていることを確認してから、今回作成した不要なファイルだけを削除する。報告本文と結果 ID は削除後も参照できる形で残す。

```bash
azd env select "<environment>"
```

```bash
set -eu
umask 077
mkdir -p tmp
output_dir="$(mktemp -d tmp/what-if.XXXXXXXX)"
printf '一時結果: %s\n' "$output_dir"
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" \
  --layer base --format json >"$output_dir/analysis.json" 2>"$output_dir/analyzer.log"
```

対象の layer に応じて `base` を `sli` などへ置き換える。`--help` と `--list-layers` はローカル情報だけを表示し、Azureへ問い合わせない。
Stacks 設定があるプロジェクトでは、layer 数にかかわらず `--layer` を指定する。省略時に別の layer や通常の ARM what-if へ切り替えない。

非 azd プロジェクトでは template、subscription、location を明示する。

```bash
set -eu
umask 077
mkdir -p tmp
output_dir="$(mktemp -d tmp/what-if.XXXXXXXX)"
printf '一時結果: %s\n' "$output_dir"
uv run --no-project "${PWD}/.github/skills/bicep-what-if-analysis/scripts/what_if_analyzer.py" \
  --no-azd --subscription <subscription-id> \
  --location japaneast --template infra/main.bicep -p environmentName dev \
  --format json >"$output_dir/analysis.json" 2>"$output_dir/analyzer.log"
```

## 結果の読み方と報告

出力先頭の操作件数、Delete の予測確度、Stack 診断を確認する。その後 Create、Modify、Delete、Detach、Unsupported の対象リソースを確認する。Modify の各差分は、リソース名、プロパティパス、変更前値、変更後値を示す。Modify が多い場合も先頭 N 件の抜粋で打ち切らず、分析器 JSON の `modifySummary` を確認して全 Modify リソースを把握する。特に、プロパティの `Delete` 差分を含む Modify と、ネットワーク・権限・セキュリティに関わるプロパティ差分（サブネットの `networkSecurityGroup` 紐付け削除など）は全件を省略せず報告に含める。Stack の管理状態や deny 設定の変更は、リソースプロパティと分けて説明する。一部を省略する場合は、省略したリソース名または件数と同じ結果の詳細を確認する方法を伝える。

Stack what-if では、分析器 JSON の `stack.resultId` を最終報告に含める。同じ結果は、報告した `resultId` を指定して `az stack-whatif sub show --id <resultId> --with-property-changes true --no-pretty-print --output json` で取得できる。保持期間は1時間である。取得する場合も標準出力と標準エラーをアクセス制限したファイルに保存する。`resultId` を取得できない場合や結果の保持期間が過ぎた場合は、その旨と確認できない詳細を報告する。件数や診断が一致しても、別の結果を同じ結果として扱わない。分析器を `--format json` で再実行すると別の what-if が作成され、結果が異なる場合がある。

差分を説明するときは、次の順で根拠を確認する。

1. 選択中の azd 環境、subscription、layer、Stack の設定、parameters file と明示的な parameter override を確認する。機微値は出力、ログ、共有文面に含めない。
2. `potential Delete` は Stack 診断と管理状態を確認し、リソース ID を Bicep の宣言や参照先と照合する。実効値を解決できない場合は未確認として残す。ID が一致しても、適用時に削除されないとは結論づけない。
3. 確認した事実、Azure の予測、ローカル分類候補、推測、未解決事項を分けて報告する。未解決事項は件数だけで済ませず、対象、差分、確認できない理由と解消に必要な確認や改善案を示す。調査で答えられる点は先に調べ、実施判断が必要な操作だけをユーザーに確認する。

`ShortCircuitedResourceId`、`potential Delete` と `Unsupported` の `guid()` 再計算照合、同一 ID への Modify と Delete の併存、`NoEffect` と `readOnly` の根拠確認、`bicepSearch.status` の解釈が必要な場合は、[詳細解釈ガイド](references/interpretation-guide.md)に従う。noise pattern、`pending`、`noise_confirmed`、Bicep 検索結果は安全性や適用可否の結論に使わず、分類だけを理由に差分を省略しない。

各差分について、what-if の予測、Azure の診断、確認できた Bicep の根拠、ローカル分類候補、未確認事項を区別して報告する。Stack の失敗状態、エラーまたは error 診断と `resourceChanges` の両方を受け取った場合、分析器は差分と診断を出力して非ゼロで終了する。失敗終了でも出力済みの差分を捨てず、診断が示す制約を説明する。適用可否は自動判定しない。適用前に確認する項目は [deployment guide](../../../docs/deployment.md#bicep-の適用前差分確認) を参照する。

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
