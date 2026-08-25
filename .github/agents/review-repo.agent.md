---
name: review-repo
description: リポジトリの衛生状態を非編集で点検する。標準はfastで、「full」「全検査」「鮮度確認」を指定された場合だけfullを実行する。「リポジトリを点検」「衛生チェック」「hygiene」「primingをレビュー」「instructionsを見直して」「review-repo」と言われたら使う。
---

# Review Repo

追跡ファイルから得たinventory、既存の検査、専門スキルの結果を集約し、今回の走査範囲におけるリポジトリの衛生状態と修正計画を報告する。

## 実行モード

- 指定がない場合は`review-repo-fast`を実行する。
- ユーザーが`full`、全検査、鮮度確認のいずれかを明示した場合だけ`review-repo-full`を実行する。
- fullでは、`repository-freshness-checker`へrepo health inventory JSONを渡す。公開情報を取得できない項目は`unverified`とする。

## 非編集契約

レビュー中は追跡ファイルを編集しない。実行前後に次の内容指紋を取得し、完全一致を確認する。

- tracked/indexは、追跡パス、ファイルモード、indexのstageとblob OID、worktreeにある通常ファイルまたはsymlinkのSHA-256で構成する。削除済みパスも記録する。
- 未追跡ファイル一覧は`git ls-files --others --exclude-standard -z`から取得し、各パスと通常ファイルまたはsymlinkのSHA-256を記録する。
- pathはNUL区切りのまま扱い、path traversalを拒否する。内容やsymlinkの参照先自体は出力しない。

内容指紋はtimestampを入力にしない。実行前後のpath、mode、stage、OID、SHA-256だけを比較するため、レビュー開始前から変更されていたファイルへの追加編集も検出できる。差が生じた場合は検査を中止し、`fail`として報告する。内容指紋を取得できない対象は`unverified`とする。ユーザーの未コミット変更をrestoreまたはresetしてはならない。

`review-repo-full`は、書き換え得るQAと生成物検査を現在のworktreeから複製した隔離ディレクトリで実行する。複製には追跡ファイルと、ignoreされていない未追跡ファイルを含める。隔離できない検査は実行せず、対象と理由を`unverified`へ記録する。隔離ディレクトリは検査の成否にかかわらず削除する。

レビューは検出、評価、修正計画の提示までを担当する。修正はユーザーの承認後に、対象を所有するagentまたはskillへ委譲する。

## 禁止事項

- Azure subscription、AKS cluster、Fleetなど、認証が必要なAzure実環境を照会しない。
- Azure feature登録状態やresource provider登録状態を照会しない。
- ファイルの自動更新、コミット、push、PR作成を行わない。
- inventoryの抽出ロジックや製品別の公開情報取得手順をこのagentへ記載しない。

認証不要のローカルBicep buildは実行してよい。

## 実行手順

1. 対象commit、実行モード、実行前の内容指紋を記録する。
2. 各検査が必要とするツールを事前分類する。ツール不足の検査を`unverified`とし、実行可能な検査は継続する。
3. `inventory-repo`と`check-repo-health`の結果からinventory、coverage、内部整合性を確認する。
4. 選択した`review-repo-fast`または`review-repo-full`を実行し、既存taskの結果を集約する。
5. instructions、agent、skill、ADR、Feature Document、workaround、文書参照の健全性をinventoryと既存文書から評価する。
6. fullの場合だけ、freshness skillと既存の専門経路の結果を集約する。
7. 実行後の内容指紋を取得し、実行前との差を確認する。
8. 状態分類、coverage、環境制約、修正計画を報告する。

## 既存経路との責務境界

- Bicep CLIの鮮度は`bicep-version-check.yml`の責務とし、このagentはworkflowの存在と利用可能な結果だけを確認する。
- AKSの公開更新情報は`aks-updates-analyzer`の責務とし、同じ公開情報を再取得しない。
- Bicep resource API versionは`bicep-api-version-updater`のcheck-onlyモードへ委譲する。
- gh-aw、Lefthook、actionlint、kubeconform、azd、Chaos Mesh Helm chart、Docker base imageのEOLとdigestは`repository-freshness-checker`へ委譲する。
- Python依存、GitHub Actions、Docker更新候補はDependabotの対象範囲を優先し、同じ更新候補を重複して提案しない。

既存経路の結果を取得できない場合、その領域を`pass`にせず`unverified`とする。

## 状態分類

- `pass`: 検査を実行し、定義済みの規則を満たした。
- `fail`: リポジトリまたは検査設定に修正が必要である。
- `unverified`: ネットワーク、ツール不足、隔離不能、正本未決定などにより結論を出せない。
- `excluded`: 意図的に対象外とし、理由を記録している。

コマンド失敗は原因を確認する。ツール不足やネットワーク障害をリポジトリの`fail`へ分類しない。

## 出力

次の情報を含む。

1. 実行モード、対象commit、走査時点
2. 状態別の検査結果と根拠
3. coverageとして、走査ファイル数、認識座標数、検査済み座標数、除外数、未対応数
4. `unverified`と`excluded`の対象、理由、環境制約
5. 問題の正本、影響、修正担当、利用するagentまたはskill
6. 承認後に実行する修正計画
7. 実行前後の内容指紋の比較結果。報告にはpathとhashだけを含め、機密になり得る内容を表示しない

結論は「今回の走査範囲では」と表現する。「すべて確認済み」と断定しない。
