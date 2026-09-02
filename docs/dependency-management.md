# 依存パッケージとツールの更新管理

この文書は、依存パッケージと開発ツールの更新候補を誰が検出し、どの機械検査が何を保証するかを定める正本である。環境構築とデプロイの手順は[deployment.md](deployment.md)、継続中の制約と解消条件は[workarounds.md](workarounds.md)を参照する。

## 原則

更新候補の検出と意味評価を分ける。検出は定期実行（scheduled）が担い、意味評価はレビューが担う。

- **scheduledが第一**: 通常のversion更新候補はRenovateが検出する。Renovateが構造上扱えない対象だけを、`freshness-checks` task targetが検出して週次Issueへ集約する。
- **レビューは重複しない**: `review-repo`のfastとfullは、scheduledが検出済みの更新候補を再検出しない。fastはリポジトリ内で完結する不変条件だけを検査し、fullは決定論化できない意味評価だけを追加する。

## 更新責務

| 対象 | 更新候補の検出 | 機械検査 |
|---|---|---|
| workspaceのPython依存 | Renovate（pep621。Dependency Dashboardの承認制） | `check-public-lock`、`check-publisher-requirements` |
| GitHub Actions | Renovate（github-actions） | `lint-workflows`、`compile-aw` |
| Docker base imageのEOLとdigest固定状況 | Renovate（dockerfile） | `check-uv-version`、`docker-base-digest`ルール |
| uv本体のpin | Renovate（custom manager + dockerfileを1 PRへ集約） | `check-uv-version` |
| actionlint、kubeconform、Renovate validator image | Renovate（custom manager） | `check-version-pins`、`check-renovate-config` |
| Chaos Mesh Helm chart | Renovate（custom manager） | `check-version-pins`、`validate-helm-values` |
| Bicep CLI | Renovate（custom manager） | `build-bicep` |
| gh-aw | scheduled checker（`freshness-checks`） | `gh-aw-compiler-version`ルール、`compile-aw` |
| Lefthook | scheduled checker（`freshness-checks`） | `check-version-pins`、`test-hooks` |
| Renovate app自体の稼働 | scheduled checker（`freshness-checks`） | なし（外部appの公開活動の観測） |
| azd minimum version range | latestとの比較対象外 | `check-version-pins`（構文と座標数） |
| Azure Functions extension bundleのsupport範囲 | latestとの比較対象外 | `check-version-pins`（構文と座標数） |

正本は[Renovate設定](../.github/renovate.json)と[Repository freshness check workflow](../.github/workflows/repository-freshness-check.md)である。

## Renovate

Renovateはこのリポジトリで唯一のscheduled version update機構である。Dependabotのversion updateは使わない。`.github/dependabot.yml`は存在せず、`check-version-pins`がその不在を検査する。GitHubのDependabot alertsとsecurity updatesはリポジトリ設定の機能であり、この判断とは別物として有効なまま残る。

有効にするmanagerは`pep621`、`github-actions`、`dockerfile`、`custom.regex`の4つである。built-in managerが読めない座標だけをcustom managerで補い、同じ座標を2つのmanagerが抽出しないようにする。custom managerの対象、datasource、期待match数は`scripts/tasks.py`の`RENOVATE_MANAGER_EXPECTATIONS`が正本であり、`check-version-pins`が設定と実ファイルの両方に対して検査する。

automergeは有効にしない。`prHourlyLimit`は5に固定し、Renovateの既定値に依存せず、1時間に作成するPull Request数の上限を明示する。`.github/workflows/*.lock.yml`と`.github/aw/**`はgh-aw compilerの生成物なので`ignorePaths`で除外し、`github/gh-aw-actions`はpackage ruleで無効化する。

### uv workspaceの制約

`uv.lock`はpublic PyPIだけを参照し、workspace member構成と`resolution-strategy = "lowest"`を保つ必要がある。Renovateがこの3条件を1回のlock更新で維持できることを保証できないため、workspaceの依存はDependency Dashboardでの承認制（`dependencyDashboardApproval`）とし、Renovateは候補検出だけを行う。lockの再生成は[refresh-uv-lock.yml](../.github/workflows/refresh-uv-lock.yml)が担当する（[deployment.md](deployment.md)の「public lockfile の更新」）。

uvのpinはroot `pyproject.toml`の`required-version`下限、`src/api/Dockerfile`のuv image、setup-uvが読む同じ下限の3か所で一致していなければならない。Renovateはcustom managerとdockerfile managerの結果を`uv`グループへ集約し、1つのPull Requestで両座標を更新する。上限（`<X.Y+1.0`）はRenovateが書き換えられないため、CIの`check-uv-version`が不整合なPull Requestを失敗させる。1 PRへの集約とfail-closedの両方でこの不変条件を守る。

### 設定の検査

リポジトリ内で完結する静的契約は`check-version-pins`が検査する。公式`renovate-config-validator`によるschema検証と、Renovate自身のRE2抽出照合（`--platform=local --dry-run=extract`を`--network=none`で実行）は、pin済みRenovate imageのpullが必要なため`check-renovate-config`が担当し、CIの専用jobで実行する。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" check-renovate-config
```

Renovate appが停止すると、設定が正しいままRenovate担当対象の通知だけが静かに止まる。`check-renovate-activity`はDependency Dashboard issueとRenovate app authoredのPull Requestの更新日時を観測し、14日の観測窓と比べる。Renovateは定期的なpingを公開しないため、この窓はheartbeatの間隔ではない。観測が窓より古い場合はapp停止と断定せず、最近の公開活動から稼働を確認できないと記録する。

## scheduled checker

`freshness-checks`は、Renovateが構造上扱えない3対象だけを検出する。gh-awのpinと公式latest releaseの比較、Lefthookのpin versionと公式latest releaseの比較およびpin versionの公式checksum照合、Renovate appの公開活動の観測である。Renovateが担当する対象のlatestを、このcheckerが再取得することはない。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" freshness-checks
```

findingが`fail`または`unverified`でもJSONを出力できるように、コマンド自体は終了コード0で終了する。週次workflowは標準出力の自然言語ではなく、各findingの`status`と`reason_code`を解釈する。

### gh-awとLefthookをRenovateに載せない理由

gh-awのcompiler pinは、生成物であるlock workflowのcompiler versionと一体で決まる。version単独の更新はcompile結果と矛盾するため、適用は`gh aw compile`が所有する。Lefthookはversionと配布物のSHA256を対で固定する。Renovateはchecksumを計算できないため、versionだけを更新するPull Requestは必ずCIで失敗する（[workarounds.md](workarounds.md)のD-12）。更新は次のtaskがversionとchecksumを一体で書き換える。

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

`review-repo-full`はfastを一度だけ実行して結果を再利用し、隔離copyでしか実行できない検査（application QA、hook test、Bicep build、Kubernetes lint、Helm values render、workflow lint、gh-aw compile）と、文書およびAI運用資産の意味評価を追加する。version候補、EOL、support範囲、互換性はscheduled workflowが担当するため、fullは再評価しない。

```bash
uv run --no-project "${PWD}/scripts/tasks.py" review-repo-fast --results-json <path>
```

両targetは`--results-json`で検査結果を機械可読JSONとして書き出す。後続の意味評価は、標準出力の自然言語ではなくこのJSONの`status`と`reason_code`を読む。

## 結果の状態

| 状態 | 意味 |
|---|---|
| `pass` | 必要な根拠を取得し、対象の契約を満たした |
| `fail` | checksum不一致、座標数の異常、schema違反など、リポジトリの契約に違反した |
| `unverified` | 更新候補の互換性判断が必要、または検証根拠を取得できなかった |
| `excluded` | 理由を記録したうえで検査対象から除外した |

`unverified`は`reason_code`で理由を区別する。`update-available`は更新候補を検出済みだが保守者の判断を待つ状態、`evidence-unavailable`はネットワーク障害やAPI制限などで根拠を取得できない状態を表す。現在versionのreleaseが存在することや、取得できた公開値が現在値と同じであることだけでは、検査範囲全体を`pass`にしない。

全体の状態は`fail`を優先し、`fail`がなく`unverified`がある場合は`unverified`とする。
