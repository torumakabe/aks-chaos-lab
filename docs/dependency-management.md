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
| workspaceのPython依存 | Renovate（pep621。Dependency Dashboardの承認制） | `check-public-lock`、`check-publisher-requirements` |
| GitHub Actions | Renovate（github-actions） | `lint-workflows`、`compile-aw` |
| Docker base imageのtagとdigest | Renovate（dockerfile） | `check-uv-version`、`docker-base-digest`ルール |
| uv本体のpin | Renovate（custom manager + dockerfileを1 PRへ集約） | `check-uv-version` |
| actionlint、kubeconform、Renovate validator image | Renovate（custom manager） | `check-version-pins`、`check-renovate-config` |
| Chaos Mesh Helm chart | Renovate（custom manager） | `check-version-pins`、`validate-helm-values` |
| Bicep CLI | Renovate（custom manager） | `build-bicep` |
| gh-aw | 保守者が`gh aw upgrade --no-actions`を実行 | `gh-aw-compiler-version`ルール、`compile-aw` |
| Lefthook | 保守者が`update-lefthook-pin`を実行 | `check-version-pins`、`test-hooks` |
| azd minimum version range | Renovate（custom manager） | `check-version-pins`（構文と座標数） |
| Docker base imageのEOL | `review-repo full`の意味評価 | `repository-freshness-checker` |
| Azure Functions extension bundleのsupport範囲 | `review-repo full`の意味評価 | `check-version-pins`（構文と座標数）、`repository-freshness-checker` |

定期更新の管理元は[Renovate設定](../.github/renovate.json)である。gh-awとLefthookの更新手順は、この文書の「gh-awとLefthookをRenovateに含めない理由」で定める。

## Renovate

Renovateはこのリポジトリで唯一のscheduled version update機構である。azdは`Azure/azure-dev`の安定版releaseを参照し、`azure.yaml`のminimum versionだけを更新する。Pull Requestは自動マージせず、最低要求版を引き上げる根拠と互換性をレビューする。Dependabotのversion updateは使わない。`.github/dependabot.yml`は存在せず、`check-version-pins`がその不在を検査する。GitHubのDependabot alertsとsecurity updatesはリポジトリ設定の機能であり、この判断とは別物として有効なまま残る。

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

## AKSアップデートの取得状況

[AKS Updates analyzer](../.github/workflows/aks-updates-analyzer.md)は、Azure Updates RSSの過去7日分と、GitHub AKS releasesの最新5件に含まれる過去14日分を週次Issueで分析する。各ソースの構造化JSONにある`status`、`reason_code`、`reason`、取得件数、解析失敗件数を同じIssueへ記載する。通信失敗、応答不正、項目の部分解析失敗は`unverified`とし、有効な`items`だけを分析する。結果が欠落したソースも未確認として扱い、0件で補わない。両ソースが`pass`で対象項目が空の場合だけ、取得範囲内で「更新なし」と報告する。理由コードと報告形式はworkflow本文を参照する。

## gh-awとLefthookをRenovateに含めない理由

gh-awのcompiler pinは、生成物であるlock workflowのcompiler versionと一体で決まる。version単独の更新はcompile結果と矛盾するため、適用は`gh aw compile`が所有する。compiler versionの定義元は[Copilot setup](../.github/workflows/copilot-setup-steps.yml)であり、生成lockと同じ版を使う。

v0.88.7の編集支援ファイルは、上流の`gh aw upgrade`が生成する[agent](../.github/agents/agentic-workflows.md)と[dispatcher skill](../.github/skills/agentic-workflows/SKILL.md)である。生成template内の参照URLは上流の`main`を指すため、compilerの対応範囲を確認するときは固定したrelease tagの資料と照合する。生成lockも含めて更新した後、`compile-aw`で再生成差分がないことを確認する。通常のActions更新を含めない場合は`gh aw upgrade --no-actions`を使う。

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

`review-repo-full`はfastを一度だけ実行して結果を再利用し、隔離copyでしか実行できない検査（application QA、hook test、Bicep build、Kubernetes lint、Helm values render、workflow lint、gh-aw compile）と、文書およびAI運用資産の意味評価を追加する。version更新候補はRenovateまたは明示的な保守作業が担当するため再検出しない。Docker base imageのEOLとAzure Functions extension bundleのsupport範囲はfullで意味評価する。

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

`unverified`は`reason_code`で理由を区別する。`evidence-unavailable`はネットワーク障害やAPI制限などで根拠を取得できない状態を表す。現在versionのreleaseが存在することや、取得できた公開値が現在値と同じであることだけでは、検査範囲全体を`pass`にしない。

全体の状態は`fail`を優先し、`fail`がなく`unverified`がある場合は`unverified`とする。
