# 依存パッケージとツールの更新管理

この文書は、依存パッケージと開発ツールの更新候補を誰が検出し、どの機械検査が何を保証するかを定める。環境構築とデプロイの手順は[deployment.md](deployment.md)、継続中の制約と解消条件は[workarounds.md](workarounds.md)を参照する。

## 原則

更新候補の検出と意味評価を分ける。Renovateが扱える対象は定期実行で検出し、生成物やchecksumとの一体更新が必要な対象は保守者が明示的に更新する。

- **定期検出はRenovateに限定する**: 通常のversion更新候補はRenovateが検出する。
- **複合更新は明示的に実行する**: gh-awはcompilerと生成物を、Lefthookはversionとchecksumを同じ変更で更新する。
- **レビューは重複しない**: `review-repo`のfastとfullはversion更新候補を再検出しない。fastはリポジトリ内で完結する不変条件だけを検査し、fullは決定論化できない意味評価だけを追加する。

## 更新責務

| 対象 | 更新候補の検出 | 機械検査 |
|---|---|---|
| workspaceのPython依存 | Renovate（pep621。Dependency Dashboardの承認制、公開後の保留期間つき） | `check-uv-lock`、`check-public-lock`、`check-publisher-requirements` |
| Python本体のmajor/minor | 保守者が互換性を確認して一括更新 | `check-version-pins`（明示したPython版の一致） |
| GitHub Actions | Renovate（github-actions） | `lint-workflows`、`compile-aw` |
| Docker base imageのtagとdigest | Renovate（dockerfile） | `check-uv-version`、`docker-base-digest`ルール |
| uv本体のpin | Renovate（custom manager + dockerfileを1 PRへ集約、公開後の保留期間つき） | `check-uv-version` |
| actionlint、kubeconform、Renovate validator image | Renovate（custom manager。Renovate validator imageの更新は週1回） | `check-version-pins`、`check-renovate-config` |
| Chaos Mesh Helm chart | Renovate（custom manager） | `check-version-pins`、`validate-helm-values` |
| Bicep CLI | Renovate（custom manager） | `build-bicep` |
| gh-aw | 保守者が`gh aw upgrade --no-actions`を実行 | `gh-aw-compiler-version`ルール、`compile-aw` |
| Lefthook | 保守者が`update-lefthook-pin`を実行 | `check-version-pins`、`test-hooks` |
| azd minimum version range | Renovate（custom manager、公開後の保留期間つき） | `check-version-pins`（構文と座標数） |
| Azure Functions extension bundleのsupport範囲 | `review-repo full`の意味評価 | `check-version-pins`（構文と座標数）、`repository-freshness-checker` |

定期更新の管理元は[Renovate設定](../.github/renovate.json)である。gh-awとLefthookの更新手順は、この文書の「gh-awとLefthookをRenovateに含めない理由」で定める。

## Renovate

Renovateはこのリポジトリで、通常更新と脆弱性修正のPull Requestを作成する唯一の依存更新機構である。Pull Requestは自動マージせず、更新の根拠と互換性をレビューする。

Dependabot alertsとdependency graphは、Renovateが`vulnerabilityAlerts`を読み取るため有効に保つ。一方、Dependabot version updatesとDependabot security updatesによるPull Request作成は無効にする。`.github/dependabot.yml`は存在せず、`check-version-pins`がその不在を検査する。Dependabot security updatesはGitHubのrepository settingで無効にするため、リポジトリ内のオフライン検査では状態を確認しない。

有効にするmanagerは`pep621`、`github-actions`、`dockerfile`、`custom.regex`の4つである。built-in managerが読めない座標だけをcustom managerで補い、同じ座標を2つのmanagerが抽出しないようにする。custom managerの対象、datasource、期待match数は`scripts/tasks.py`の`RENOVATE_MANAGER_EXPECTATIONS`が正本であり、`check-version-pins`が設定と実ファイルの両方に対して検査する。

automergeは有効にしない。`prHourlyLimit`は5に固定し、Renovateの既定値に依存せず、1時間に作成するPull Request数の上限を明示する。`.github/workflows/*.lock.yml`と`.github/aw/**`はgh-aw compilerの生成物なので`ignorePaths`で除外し、`github/gh-aw-actions`はpackage ruleで無効化する。

CIの設定検証で使う`renovate/renovate` imageの通常の更新は、Renovateが更新を適用できる日を毎週月曜日（日本時間）に限定する。ほかのDocker imageの更新間隔は変更しない。CIの`check-renovate-config`は、Pull Requestごとに固定済みのimageを使って引き続き実行する。

### 更新候補を保留する期間

Python依存の更新候補は、公開から一定期間を経てから提示する。開発端末があらかじめ満たしていなければならない版の下限（uvの`required-version`とazdの`requiredVersions`）も、同じ期間を経てから引き上げる。判断は[ADR-023](adr/023-python-release-cooldown.md)を参照。期間内の版は`minimumReleaseAge`によって候補にならず、branchもPull Requestも作られない。保留中の候補はDependency Dashboardに残るため、見落としにはならない。脆弱性修正の`vulnerabilityAlerts`はこの判定を既定で無視するため、同じ期間を明示的に指定して上書きする。

`vulnerabilityAlerts`はDependabot alertsを入力として有効にし、修正版のうち最低のversionを選ぶ。脆弱性修正はRenovateのrepository全体のPull Request上限を無視するため、専用の同時実行上限を5件に設定する。通常更新と同じく自動マージは行わない。

Renovateのこの判定は、自身が提示する直接依存にしか適用されない。`uv lock`が連れてくる推移的依存には、lock生成時のcutoffが対応する（[deployment.md](deployment.md)の「public lockfile の更新」）。期間の値は`scripts/tasks.py`の`RELEASE_COOLDOWN_DAYS`が定義元で、Renovate設定の値もそこから導出した契約として`check-version-pins`が検査する。

package単位で保留期間を短縮する仕組みは用意しない。急ぎ適用したい修正がある場合も、`uv.lock`の`[options]`やcutoffの手編集で検査を迂回せず、期間と設定値の見直しとして保守者が判断する。

### uv workspaceの制約

Python本体のmajor/minor更新は、GitHub ActionsとDockerのRenovate package ruleで無効にする。Python依存パッケージの更新は対象外で、同じminor内のPython patch更新とDocker digest更新も継続する。major/minorの移行は、保守者が依存パッケージとAzure Functionsの対応状況を確認し、関連設定を同じ変更で更新する。

Pythonの版指定は各ツールの設定に明記したままにする。`check-version-pins`は、ルート`pyproject.toml`の`requires-python`にある`>=X.Y`の下限を比較基準に、workspace memberの下限、Ruff・tyの対象、workflow（gh-awの定義と生成lockを含む）、APIのDocker image、Functions runtime、task runnerのvenv作成時の指定が一致することを検査する。一部だけを更新した場合や必要なファイルを読み取れない場合は失敗する。Docker imageのpatchとdigest、および独立したスクリプトの対応下限は比較対象に含めない。

移行時はこれらの設定を一括更新し、gh-awの生成lockとpublic lockfileを既存の手順で再生成する。新しいPython版でアプリとスクリプトのQAを実行して互換性を確認する。版指定の一致だけでは、新しい版への対応を保証しない。

`uv.lock`はpublic PyPIだけを参照し、workspace member構成と`resolution-strategy = "lowest"`を保つ必要がある。Renovateがこの3条件を1回のlock更新で維持できることを保証できないため、workspaceの通常更新はDependency Dashboardでの承認制（`dependencyDashboardApproval`）とし、Renovateは候補検出だけを行う。脆弱性修正を含め、lockの確定は[refresh-uv-lock.yml](../.github/workflows/refresh-uv-lock.yml)が担当し、生成されたartifactは`adopt-public-lock`で取り込む（[deployment.md](deployment.md)の「public lockfile の更新」）。botが生成したcutoffのない`uv.lock`をそのまま取り込まない。

uvのpinはroot `pyproject.toml`の`required-version`下限、`src/api/Dockerfile`のuv image、setup-uvが読む同じ下限の3か所で一致していなければならない。Renovateはcustom managerとdockerfile managerの結果を`uv`グループへ集約し、1つのPull Requestで両座標を更新する。このグループにもPython依存と同じ保留期間を指定し、開発端末のtool managerが最新版として提供する前のuvを下限に要求しないようにする。`required-version`は下限だけを宣言し、上限を置かない。開発端末はuvをtool managerの最新版に保つため、上限があると新しいminorの公開と同時に端末のuvが範囲外になる（[ADR-023](adr/023-python-release-cooldown.md)）。uv imageはGHCRではなく、同じdigestを配布するDocker Hubの`astral/uv`から取得する。Renovateがrelease timestampを取得できるのはDocker Hubだけで、timestampが無いと保留期間を満たせず、グループ内の2つのpinが揃って更新されないためである。下限とDocker imageの一方だけを更新したPull Requestは、CIの`check-uv-version`が失敗させる。1 PRへの集約とfail-closedの両方でこの不変条件を守る。

### 設定の検査

リポジトリ内で完結する静的契約は`check-version-pins`が検査する。公式`renovate-config-validator`によるschema検証と、Renovate自身のRE2抽出照合（`--platform=local --dry-run=extract`を`--network=none`で実行）は、pin済みRenovate imageのpullが必要なため`check-renovate-config`が担当し、CIの専用jobで実行する。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" check-renovate-config
```

## AKSアップデートの取得状況

[AKS Updates analyzer](../.github/workflows/aks-updates-analyzer.md)は、Azure Updates RSSの過去7日分と、GitHub AKS releasesの最新5件に含まれる過去14日分を週次Issueで分析する。各ソースの構造化JSONにある`status`、`reason_code`、`reason`、取得件数、解析失敗件数を同じIssueへ記載する。通信失敗、応答不正、項目の部分解析失敗は`unverified`とし、有効な`items`だけを分析する。結果が欠落したソースも未確認として扱い、0件で補わない。両ソースが`pass`で対象項目が空の場合だけ、取得範囲内で「更新なし」と報告する。理由コードと報告形式はworkflow本文を参照する。

## Bicep確認の手動移行PoC

GitHub cloud Copilot Automationへの移行は検証中で、Automationは`enabled=false`のまま手動で試す。[bicep_api_audit.py](../scripts/bicep_api_audit.py)は標準ライブラリだけを使い、宣言別の確認状態と公開根拠を保存する。これは本番移行の完了やAPI仕様照合の完了を示すものではない。既存の週次workflowの説明と運用は変更しない。

`init`で対象checkoutの完全なcommit SHAとREST仕様のcommit SHAを固定する。`next --state <state.json> --output <assessment.json>`は次のpairの宣言とusageを未処理の雛形へ複写し、大きなJSONの再入力を避ける。`record`はpair別の`results`と`evidence`を持つJSONを読み、公開根拠の取得、宣言の一致、全usageの`source_id`、`path`、`expression`、`analysis`を検査して状態を保存する。`Schemas`は外部参照と`allOf`を展開するが、互換性の判断自体は実行者が公開仕様と使用属性を照合して記録する。HTTP成功や状態JSONの形式検査だけで判断を代替しない。

`prepare --state <state.json> --title <title> --directory <output>`は全pair、全宣言、使用属性、判断、根拠と完全なハッシュを`evidence.json`へ保存し、`report.md`も生成する。未処理pairがあれば生成を拒否する。公開根拠不足を`block`で記録した場合だけ`--allow-blocked`で未完了の報告を生成でき、その宣言は`unverified`として残る。この段階ではIssue用payloadを生成しない。

`block`の入力JSONには、任意の`partial_results`と`partial_evidence`を追加できる。`partial_results`を渡す場合は、そのpairの全宣言の`file`、`line`、`name`と確認済みの`findings`を過不足なく記録する。`partial_evidence`は確認できた`current`、`ga`、`breaking`だけを持ち、各項目に`analysis`と公開根拠の`urls`、版の根拠には`version`も記録する。current schemaが不足していても既知のGA候補と指摘はJSON、レポート、Issueの件数に残るが、GAの根拠でcurrentの確認を代替せず、宣言は常に`unverified`とする。

実行ごとの検証専用ブランチへ両ファイルをcommit/pushした後、同じ`prepare`に`--evidence-url`と`--report-url`を追加する。両URLは同じrepositoryと実在する完全なcommit SHAの`https://github.com/<owner>/<repo>/blob/<commit>/<path>`を指定する。スクリプトは`gh api`で両ファイルを再取得し、ローカル出力とバイト単位で一致する場合だけ短い`issue-payload.json`と本文のSHA-256を生成する。Issue本文は不変のファイルリンク、件数、未確認状態とハッシュに限定し、圧縮JSONを埋め込まない。

Issue作成は別途許可された操作で行う。作成後にGitHubから取得したtitleとbodyのJSONを`verify-issue --payload <issue-payload.json> --actual <actual.json>`へ渡し、保存済みpayloadとの一致を確認する。これらのコマンドはcommit、push、Issue作成、Azure照会、Automation設定変更を実行しない。

## gh-awとLefthookをRenovateに含めない理由

Bicep API versionの週次確認は特定のCopilot modelを固定せず、`GH_AW_MODEL_AGENT_COPILOT`、`GH_AW_DEFAULT_MODEL_COPILOT`の順でActions variableを参照し、未設定なら`auto`を使う。利用できないmodelが指定されるとagent開始時に失敗するため、variableを設定する場合は実行accountで利用可能なmodelを選ぶ。threat detectionはcompiler既定の`detection`を使う。

gh-awのcompiler pinは、生成物であるlock workflowのcompiler versionと一体で決まる。version単独の更新はcompile結果と矛盾するため、適用は`gh aw compile`が所有する。compiler versionの定義元は[Copilot setup](../.github/workflows/copilot-setup-steps.yml)であり、生成lockと同じ版を使う。

Copilot setupの`version`はRenovateの`github-actions` managerが`github/gh-aw`という依存名で検出する。`.github/renovate.json`ではこの依存名も更新対象外にし、action SHAとversion、生成lockを個別に更新するPRを防ぐ。

v0.91.12の編集支援ファイルは、上流の`gh aw upgrade`が生成する[agent](../.github/agents/agentic-workflows.md)と[dispatcher skill](../.github/skills/agentic-workflows/SKILL.md)である。生成template内の参照URLは上流の`main`を指すため、compilerの対応範囲を確認するときは固定したrelease tagの資料と照合する。生成lockも含めて更新した後、`compile-aw`で再生成差分がないことを確認する。通常のActions更新を含めない場合は`gh aw upgrade --no-actions`を使う。

更新時は対象versionのCLIを使用し、`github/gh-aw-actions`の同じrelease tagをcommit SHAへ解決する。`copilot-setup-steps.yml`のaction SHAとversionを更新した後、`gh aw upgrade --no-actions`でdispatcher、codemod、lock workflowを更新し、`compile-aw`で再生成差分がないことを確認する。

Lefthookはversionと配布物のSHA256を対で固定する。Renovateはchecksumを計算できないため、versionだけを更新するPull Requestは必ずCIで失敗する（[workarounds.md](workarounds.md)のD-12）。更新時は次のtaskでversionとchecksumを一体で書き換え、`check-version-pins`と`test-hooks`を実行する。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" update-lefthook-pin --version <lefthook-version>
```

## fastとfullの境界

`review-repo-fast`はオフラインで完結する。外部API、container registry、Docker daemonのいずれにも触れないため、結果は実行環境に依存しない。scheduledが検出する更新候補をfastは検出しない。

fastが実行するversion関連の検査は`check-version-pins`である。検査対象はすべてリポジトリ内で完結する。

- Renovate設定の静的構造（enabledManagers、prHourlyLimit、ignorePaths、packageRules、custom managerの期待座標とmatch数、manager重複禁止、automerge禁止）
- Dependabot version updateの停止
- Lefthookの座標がちょうど一組であることとchecksumの形式
- azdとAzure Functions extension bundleのrange構文と座標数
- Chaos Mesh chart versionの正本座標

uv pinの内部整合は`check-uv-version`が、gh-aw pinとlock fileの`compiler_version`の整合は`check-repo-health`の`gh-aw-compiler-version`ルールが検査する。どちらもfastが実行する。

`review-repo-full`は専用worktreeでfastを一度だけ実行し、application QA、hook test、Bicep build、Kubernetes lint、Helm values render、workflow lint、gh-awの非書き込み検証を追加する。生成lockの更新と差分検査には、手動またはCIで`compile-aw`を実行する。version更新候補はRenovateまたは明示的な保守作業が担当するため再検出しない。Azure Functions extension bundleのsupport範囲はfullで意味評価する。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" review-repo-fast --results-json <path>
```

両targetは`--results-json`でschema version 2の実行記録を書き出す。各処理の`execution`（`exited`、`not-started`、`timed-out`）、`returncode`、`reason_code`、診断の末尾`stdout_tail`と`stderr_tail`を保持する。後続のレビューはこの記録を読み、終了コードだけから対象の不具合を推定しない。診断は処理中に全体を表示し、JSONには長さを制限した末尾を保存するため、必要な根拠が末尾に残っていなければ確認済みとしない。

JSONの`command_exit_code`は既存のtask終了規則による値で、レビューの評価を表さない。明確なテスト不合格などや、原因を分類できない非ゼロ終了では1とする。起動不能、timeout、既知の環境障害だけの場合は従来どおり0とするが、その実行記録は必ず残す。終了コード0を全対象の確認完了と扱わない。各テスト、lint、buildなどの終了コードの意味も変更しない。

### 専門スキルの利用

専門スキルは対象ファイルを直接読み、確認、完了判定、結果の返却までを行う。事前のfull実行や汎用inventoryを必要としない。

| スキル | 入力と確認範囲 |
|---|---|
| `repository-freshness-checker` | リポジトリまたは指定ファイルの公開MarkdownリンクとFunctionsの`host.json`。到達性とextension bundleのsupport範囲を確認する |
| `bicep-api-version-updater`のcheck-only | リポジトリまたは指定したBicep。resource宣言を読み、公開API仕様、最新GA版、使用属性の互換性を確認する |

`review-repo`はfullでスキルを選び、対象リポジトリを渡し、返された確認範囲、根拠、完了状況をレビューへ組み込む。スキルの専門確認を組み立て直さない。文書本文と実装の一致など、スキルの確認範囲外の意味評価はレビュー側で行う。

Bicepの週次workflowも同じcheck-onlyを利用する。対象inventoryをpre-agent stepで生成せず、checkoutのBicepを直接確認する。許可ツール、通信先、時間制限とIssue出力はworkflowが定める。

`inventory-repo`と`--inventory-json`は提供しない。`check-repo-health`はローカルの整合性検査だけを行い、Docker digest検査はDockerfileを読む。Kubernetesのschema検証から除外した文書は、既存のkind除外設定に基づいて報告する。Kubernetes文書の読取失敗はファイルごとの`fail`として記録し、他の検査結果と読めた文書の除外情報を保持する。専門スキル用の入力は生成しない。

未処理、対象の読取や解釈の失敗、公開根拠の取得不能、仕様判断の未完了は区別する。未読ファイルや未検証対象が残る場合は確認未完了として報告し、結果の形式検査やworkflowのcompile成功を公開仕様照合の成功とは扱わない。

## 実行結果、確認状況と指摘

taskの実行結果と、スキルやレビューが確認できた内容は分けて記録する。テスト不合格やbuildエラーは元の結果と診断を保持し、実行エラーだけから対象に不具合があると断定しない。`check-repo-health`など個別の検証が返す`pass`や`fail`も、その検証の結果として保持し、レビュー全体の合否へ変換しない。

| 確認状況 | 表示 | 意味 |
|---|---|---|
| `verified` | 確認済み | 必要な確認と根拠が揃った。問題の有無は別に記録する |
| `unverified` | 未確認 | 未処理、対象の読取や解釈の失敗、根拠の取得不能、または判断未完了により必要な確認が終わっていない |
| `excluded` | 理由付き対象外 | 理由を記録したうえで確認対象から除外した |

指摘（`issue`）と更新候補（`update`）は確認状況から独立して列挙する。同じ対象に両方があってよく、未確認の事項が残っていても確認できた内容は保持する。新しいGA版があることだけを問題としない。確認状況別の対象件数と指摘や更新候補の件数を足し合わせない。

`unverified`は理由を区別する。`not-processed`は未処理、`target-unresolved`は読取や解釈の失敗、`evidence-unavailable`はネットワーク障害やAPI制限などによる根拠取得不能、`assessment-incomplete`は判断未完了を表す。現在versionのreleaseが存在することや、取得できた公開値が現在値と同じであることだけでは、対象を確認済みにしない。

スキルとレビューには全体statusや合否を付けない。未読ファイル、未確認対象、未確定の対象範囲があれば確認未完了とし、それ以外は確認完了とする。指摘や更新候補の存在は確認完了と両立する。

レビューでは確認範囲、指摘、更新候補、未確認対象と理由を報告する。「問題なし」は必要な確認を終え、指摘がない範囲についてだけ述べる。実行エラーがある場合は、その理由と評価できなかった範囲を別に示す。
