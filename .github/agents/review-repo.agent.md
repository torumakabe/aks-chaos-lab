---
name: review-repo
description: リポジトリの衛生状態を非編集で点検する。標準はfast（taskのみ）で、「full」「全検査」「鮮度確認」を指定された場合だけfullを実行する。「リポジトリを点検」「衛生チェック」「hygiene」「primingをレビュー」「instructionsを見直して」「review-repo」と言われたら使う。
---

# Review Repo

追跡ファイルから得たinventory、既存の検査、専門スキルの結果を集約し、今回の走査範囲におけるリポジトリの衛生状態と修正計画を報告する。

## 実行モード

- この文書でtask targetと呼ぶ名前は、すべて`scripts/tasks.py`の実行対象である。
- 指定がない場合は`review-repo-fast` task targetを実行する。
- `fast`では、`review-repo-fast`と内容指紋のtask targetだけを実行し、構造化inventoryに記録されたcoverageと検査結果を報告する。「fullモードの文書とAI運用資産の評価基準」は適用せず、専門skillを呼び出さない。
- ユーザーが`full`、全検査、鮮度確認のいずれかを明示した場合だけ`review-repo-full` task targetを実行する。
- `full`では、`review-repo-full`の全検査に加えて、文書とAI運用資産の意味評価を実行する。同じrepo health inventory JSONを`repository-freshness-checker`と`bicep-api-version-updater`のcheck-onlyモードへ渡し、既存の専門経路の結果を集約する。公開情報を取得できない項目は`unverified`とする。
- `full`では、現在のworktreeを直接検査しない。`review-workspace create` task targetが作成した隔離workspaceへ`review-repo-full`と両専門skillの実行をすべて委譲し、現在のworktreeは実行前後の内容指紋の取得だけに用いる。

## 実行インターフェース

`<temp>`はリポジトリ外のOS一時ディレクトリを示し、`<workspace>`は`review-workspace create`が返した隔離workspaceの絶対pathを示す。full modeで`review-repo-full`に渡す`<temp>`は、現在のリポジトリと`<workspace>`のどちらの内部にも置かない。review-repo agentは次のtask targetだけを呼び、repo health抽出や内容指紋のGitコマンドを組み立てない。

| task target | 完全な呼び出し | 入力 | 出力と副作用 | 利用先 |
|---|---|---|---|---|
| `review-fingerprint capture` | `uv run --no-project "${PWD}/scripts/tasks.py" review-fingerprint capture --output "<temp>/before.json"` | 現在のtracked、index、worktree、未追跡ファイル | 指定したリポジトリ外のJSONファイルを作成し、集約SHA-256を標準出力へ返す | レビュー開始前と終了後 |
| `review-repo-fast` | `uv run --no-project "${PWD}/scripts/tasks.py" review-repo-fast --inventory-json "<temp>/inventory.json"` | 現在のworktree | repo health inventory JSONを一度だけ生成し、fast検査結果を表示する。指定したJSON以外はworktreeを変更しない | 標準レビュー |
| `review-workspace create` | `uv run --no-project "${PWD}/scripts/tasks.py" review-workspace create` | 現在のtracked filesとignoreされていない未追跡ファイル | `review-repo-full`が内部で使う隔離コピー生成と同じ複製ロジックでrepository外に隔離workspaceを作成し、絶対pathとcleanup用tokenを含むJSONを標準出力へ返す。現在のworktreeは変更しない | fullレビューの前段 |
| `review-repo-full` | `uv run --no-project "<workspace>/scripts/tasks.py" review-repo-full --inventory-json "<temp>/inventory.json"` | `review-workspace create`が作成した隔離workspace | fastを内包し、書き換え得るQAを隔離workspace内部でさらに複製した隔離ディレクトリで実行する。同じinventory JSONをfreshness skillへ渡せる形で残す。この`<temp>`は現在のリポジトリと隔離workspaceの両方の外に置く | fullレビュー |
| `review-workspace cleanup` | `uv run --no-project "${PWD}/scripts/tasks.py" review-workspace cleanup --workspace-path "<workspace>" --token "<token>"` | `review-workspace create`が返したworkspace pathとtoken | manifestとtokenの一致を確認できた場合だけ隔離workspaceを削除する。検査の成否にかかわらず実行する | fullレビューの後段（finally相当） |
| `review-fingerprint compare` | `uv run --no-project "${PWD}/scripts/tasks.py" review-fingerprint compare --before "<temp>/before.json" --after "<temp>/after.json"` | 実行前後の内容指紋JSON | 一致時は`pass`、差分時はpathと前後のSHA-256だけを出力してexit 1 | 非編集契約の最終判定 |
| `inventory-repo` | `uv run --no-project "${PWD}/scripts/tasks.py" inventory-repo --format json` | 現在のtracked filesとignoreされていない未追跡ファイル | inventory JSONを標準出力へ返す。review-repo agentは直接呼ばず、週次freshness workflowが利用する | 自動化と診断 |
| `check-repo-health` | `uv run --no-project "${PWD}/scripts/tasks.py" check-repo-health` | 現在のworktree | repo healthの内部整合性を表示する。`review-repo-fast/full`が内包するためagentは直接呼ばない | 単独診断 |

## 検査の包含関係

| 層 | 内包する検査 | 次の層で追加する検査 |
|---|---|---|
| `review-repo-fast` task target | repo health JSON生成と整合性、Bicep parameter JSON、uv version、public lock、publisher requirements | なし |
| review-repo agentのfastモード | 内容指紋の取得と比較、`review-repo-fast` task targetの全検査 | 構造化inventoryのcoverageとtask結果の報告だけを行う。文書とAI運用資産の意味評価および専門skillは実行しない |
| `review-repo-full` task target | `review-repo-fast`の全検査 | 隔離したapplication QA、hook test、Bicep build、全Kubernetes YAMLのlint、固定versionのChaos Mesh chartによるvalues render、workflow lint、gh-aw compile。Kubernetes schemaを取得できないkindは`.github/repo-health.toml`の一覧とinventoryの`kubernetes-schema-exclusion`座標で`excluded`として数える。application QAではfastで完了したpublisher requirementsを再実行しない |
| review-repo agentのfullモード | 内容指紋の取得と比較、`review-workspace create`による隔離workspace作成、隔離workspace内から呼び出した`review-repo-full` task targetの全検査、`review-workspace cleanup`によるworkspace削除 | 文書とAI運用資産の意味評価、同じinventory JSONを入力にしたfreshness skillによる製品鮮度と公開Markdownリンク到達性、`bicep-api-version-updater`のcheck-onlyモード、既存の専門経路 |

## fullモードの文書とAI運用資産の評価基準

この節はfullモードだけで適用する。inventoryへの出現は、ファイルの存在を確認したことだけを意味する。構造、参照先、現在の実装との一致を次の基準で評価する。自動検査が構造や参照先を検証済みの場合は結果を再利用し、agentは同じ検査を組み立て直さない。

| 種類 | 健全性を確認する項目 | 専門経路 |
|---|---|---|
| `.github/copilot-instructions.md` | 記載したプロジェクト構造と実在するtop-level構造が一致する。知識ソースのpathが実在する。参照するagentとskillが実在する。詳細手順を重複させず、正本への参照を示している | なし |
| agent | リポジトリが管理し、modelから直接呼び出すagentはfrontmatterのnameとdescriptionが実態に一致する。`disable-model-invocation: true`のdispatcherはnameを必須とせず、description、upstream参照、version対応を確認する。参照するtask target、skill、workflow、pathが実在し、入力、出力、副作用、包含関係から実行順を一意に決められる | agent自身の契約テスト。gh-aw dispatcherのversion差は`gh-aw-compiler-version`規則 |
| skill | `.github/skills/`の各skill directoryに`SKILL.md`が存在する。trigger、責務境界、入力、出力が明確である。参照するscript、文書、外部情報源が実在する | 対象skillのtestまたはcheck-only手順 |
| ADR | `docs/adr/INDEX.md`の一覧とADRファイルが相互に対応する。`docs/adr/README.md`の必須見出しとStatus形式を満たす。参照するコードpathが実在し、Acceptedな決定が現在の実装と矛盾しない | 詳細な意味評価は`manage-adr` |
| Feature Document | 対象機能、決定事項、未完了作業、現在状態が実装と一致する。参照先が実在する。最終変更から30日以上経過した文書は、継続、ADRへの移行、破棄のいずれが必要かを判定する | 作業再開は`resume`、ADRへの移行は`manage-adr` |
| `docs/workarounds.md` | 各項目に概要、理由、場所、解消条件、確認方法がある。記載した場所と実装が実在する。解消済みの項目が残っておらず、実装中の回避策が棚卸しから漏れていない | 公開情報の確認は該当するfreshness skillまたは専門skill |
| READMEと運用文書 | 相対link、記載したpath、task target、commandが実在する。説明が現在の実装と一致する。判断の背景や製品別取得手順を重複させず、正本を参照する | 製品固有の意味評価は該当する専門skill |
| workflow sourceと生成物 | source、生成lock、actions lockの対応が取れている。sourceに記載したtask targetとskillが実在する。生成物の差分検査を通過する | `lint-workflows`、`compile-aw` |

評価基準を適用できなかったファイルを`pass`にしない。ファイルごと、または同じ理由と専門経路を持つ種類ごとに`unverified`としてpathと理由を示す。明示した形式、参照先、現在の実装との不一致は`fail`とする。

## 非編集契約

レビュー中は追跡ファイルを編集しない。実行前後に`review-fingerprint capture` task targetで次の内容指紋を取得し、`review-fingerprint compare` task targetで完全一致を確認する。

- tracked/indexは、追跡パス、ファイルモード、indexのstageとblob OID、worktreeにある通常ファイルまたはsymlinkのSHA-256で構成する。削除済みパスも記録する。
- 未追跡ファイル一覧は`git ls-files --others --exclude-standard -z`から取得し、各パスと通常ファイルまたはsymlinkのSHA-256を記録する。
- pathはNUL区切りのまま扱い、path traversalを拒否する。内容やsymlinkの参照先自体は出力しない。

内容指紋taskはtimestampを入力にしない。実行前後のpath、mode、stage、OID、SHA-256だけを比較するため、レビュー開始前から変更されていたファイルへの追加編集も検出できる。差が生じた場合は検査を中止し、`fail`として報告する。taskを実行できない場合は`unverified`とする。ユーザーの未コミット変更をrestoreまたはresetしてはならない。

fullモードでは、`review-workspace create` task targetが現在のworktreeから複製した隔離workspaceをリポジトリの外に作成する。複製には追跡ファイルと、ignoreされていない未追跡ファイルを含める。review-repo agentはこの隔離workspace内から`review-repo-full`を一度だけ実行し、現在のworktree自体は直接検査しない。`review-repo-full`は、書き換え得るQAと生成物検査を、その隔離workspaceからさらに複製した内部の隔離ディレクトリで実行する。隔離できない検査は実行せず、対象と理由を`unverified`へ記録する。内部の隔離ディレクトリは検査の成否にかかわらず削除し、外側の隔離workspaceも検査の成否にかかわらず`review-workspace cleanup`で削除する。cleanupは、対応するcreateが発行したtokenとmanifestで検証できたworkspaceだけを対象とし、リポジトリ本体や祖先、他のtask実行が作成した無関係なpathは削除しない。

レビューは検出、評価、修正計画の提示までを担当する。修正はユーザーの承認後に、対象を所有するagentまたはskillへ委譲する。

## 禁止事項

- Azure subscription、AKS cluster、Fleetなど、認証が必要なAzure実環境を照会しない。
- Azure feature登録状態やresource provider登録状態を照会しない。
- ファイルの自動更新、コミット、push、PR作成を行わない。
- inventoryの抽出ロジックや製品別の公開情報取得手順をこのagentへ記載しない。

認証不要のローカルBicep buildは実行してよい。

## 実行手順

1. 対象commitと実行モードを記録し、`review-fingerprint capture`で実行前の内容指紋を取得する。
2. fastの場合は`review-repo-fast`を、リポジトリ外の`--inventory-json`出力先を指定して現在のworktreeへ一度だけ実行する。fullの場合は`review-workspace create`で隔離workspaceを作成し、`uv run --no-project "<workspace>/scripts/tasks.py" review-repo-full`を、現在のリポジトリと隔離workspaceの両方の外にある`--inventory-json`出力先を指定して一度だけ実行する。task targetがツールを事前分類し、実行可能な検査を継続する。
3. task targetが生成したinventory JSONからcoverageと内部整合性を確認する。file coverageは`covered_by_other_check`、`intentionally_excluded`、`true_gap`を区別し、`true_gap`を未対応数として扱う。`inventory-repo`や`check-repo-health`を重複実行しない。
4. fullの場合だけ、「fullモードの文書とAI運用資産の評価基準」を種類ごとに適用し、inventoryへの出現だけで`pass`にしない。手順2と同じinventory JSON（隔離workspace内のpathを指す）を`repository-freshness-checker`と`bicep-api-version-updater`のcheck-onlyモードへ渡し、既存の専門経路の結果と集約する。freshness skillは`documentation-external-link`座標を全件処理し、取得不能を`unverified`とする。Bicep resource APIの結果が返らない場合、その領域を`unverified`とする。
5. fullの場合だけ、手順4までの検査結果にかかわらず`review-workspace cleanup`を実行し、手順2で作成した隔離workspaceを削除する。削除に失敗した場合はその旨を報告に含め、現在のworktreeへの影響がないことを確認する。
6. `review-fingerprint capture`で実行後の内容指紋を取得し、`review-fingerprint compare`で実行前との差を確認する。
7. 状態分類、coverage、環境制約、修正計画を報告する。fastではfull専用検査を個別の`unverified`として列挙せず、実行モードの対象外であることを示す。

## 既存経路との責務境界

- Bicep CLIの鮮度は`bicep-version-check.yml`の責務とし、このagentはworkflowの存在と利用可能な結果だけを確認する。
- AKSの公開更新情報は`aks-updates-analyzer`の責務とし、同じ公開情報を再取得しない。
- Bicep resource API versionは`bicep-api-version-updater`のcheck-onlyモードへ委譲する。定期通知は`bicep-api-version-check.md`が同じcheck-only契約で実行する。
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
3. coverageとして、追跡済みファイル数、未追跡ファイル数、走査ファイル数、認識座標数、検査済み座標数、除外数、別検査で対応するファイル数、意図的除外数、真の未対応数
4. `unverified`と`excluded`の対象、理由、環境制約
5. 問題の正本、影響、修正担当、利用するagentまたはskill
6. 承認後に実行する修正計画
7. 実行前後の内容指紋の比較結果。報告にはpathとhashだけを含め、機密になり得る内容を表示しない

結論は「今回の走査範囲では」と表現する。「すべて確認済み」と断定しない。
