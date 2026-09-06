---
name: repository-freshness-checker
description: scheduled checkerの結果を入力に依存やツールのEOL、support範囲、互換性を意味評価し、review-repo fullでは公開Markdownリンクだけを確認するcheck-onlyスキル。鮮度確認や、全検査を担うreview-repoからの呼び出しで使用する。
---

# Repository Freshness Checker

入力済みの検出結果を引き継ぎ、呼び出し元に応じた公開情報の確認だけを行う。結果は呼び出し元へ返す。

## 呼び出し元と入力

| 呼び出し元 | 必須入力 | このスキルの確認範囲 |
|---|---|---|
| scheduled（週次workflow） | `inventory-repo --format json`のrepo health inventory JSONと、`freshness-checks`の機械可読JSON（`findings[]`、全体`status`/`coverage`） | 下記8対象のEOL、support範囲、互換性。Renovate appの公開活動は入力findingを採用 |
| review-repo full | `review-repo-full --inventory-json <absolute-path> --results-json <absolute-path>`が生成したinventoryと検査結果JSON | `documentation-external-link`座標の公開Markdownリンクの到達性とredirect先だけ |

渡されたinventoryのschema versionと対象commitを記録する。別のinventory生成コマンドを実行せず、既存の検査結果を使う。必須JSONの欠落、解釈できないschema、不完全な結果は、影響する範囲を`unverified`とし、何が不足したかを呼び出し元へ返す。入力全体を解釈できない場合は全体を`unverified`とする。欠落した座標や値を推測しない。

fullにscheduled結果は不要であり、その不在を`unverified`にしない。「全検査」という語だけで、このスキルが独自にリポジトリ点検やtask実行を開始しない。

## check-only契約

- ファイルの編集、自動更新、commit、push、PR作成を禁止する。
- Azure subscription、AKS cluster、Fleet、resource provider、feature登録状態など、認証が必要なAzure実環境を照会しない。
- 公開情報を取得できない場合は`unverified`とし、現在値を最新と推定しない。
- major更新、EOLを伴う移行、Helm chart更新、base image変更を自動推奨しない。互換性、移行手順、廃止日、サポート範囲、検証項目を影響確認事項として示す。

## scheduledの評価対象

次の8対象と、Renovateが担当する対象の前提であるRenovate appの公開活動を扱う。最新版候補は自分で検出せず、既存の検出結果と意味評価を区別する。

| 対象 | 更新候補の検出 |
|---|---|
| gh-aw | scheduled checker（`freshness-checks`の`gh-aw` finding） |
| Lefthook | scheduled checker（`freshness-checks`の`Lefthook` finding） |
| Renovate appの公開活動 | scheduled checker（`freshness-checks`の`Renovate app activity` finding） |
| actionlint | Renovate |
| kubeconform | Renovate |
| Chaos Mesh Helm chart | Renovate |
| Docker base imageのEOLとdigest固定状況 | Renovateがtagとdigestを更新し、digest固定の座標は`.github/repo-health.toml`の`docker-base-digest`ルールが検証する |
| azd | Renovate。`Azure/azure-dev`の安定版releaseからminimum versionの更新候補を検出する |
| Azure Functions extension bundleのsupport範囲 | latestとの比較対象外。`check-version-pins`がrange構文と定義元の座標を検証する |

gh-awとLefthookの更新候補、Renovate appの公開活動がscheduled checkerの担当である。gh-awのpinはcompilerと生成物を一体で決める`gh aw compile`、Lefthookのversionとchecksumは専用の更新taskが管理する（[docs/workarounds.md §D-12](../../../docs/workarounds.md)）。

### Renovate appの公開活動

設定の妥当性やDashboardの存在だけでは、現在の稼働を確認できない。scheduled checkerはDependency Dashboard issueの`updated_at`と、Renovate app authoredのPull Request（open/closedを含む）の`created_at`/`updated_at`の最新値を観測する。Renovateは定期的なpingを公開しないため、14日は公開活動の観測窓であり、heartbeatの間隔ではない。

入力findingは次の意味で解釈し、公開活動を再照会しない。

| 観測結果 | status | reason_code |
|---|---|---|
| 観測窓内に公開活動あり | `pass` | `renovate-activity-observed` |
| Dashboardもapp authoredのPull Requestも未観測 | `unverified` | `renovate-not-observed` |
| 公開活動はあるが観測窓より古い | `unverified` | `renovate-activity-unobserved` |
| GitHub API取得不能、rate limit、不完全な結果 | `unverified` | `evidence-unavailable` |

未観測をapp停止と断定しない。activity findingが`pass`でなければ、Renovate担当対象の通知経路は`unverified`とし、対象全体を`pass`と報告しない。取得済みの検出finding自体を書き換えず、意味評価側にこの制限を記録する。Dashboard issueを作るのはRenovate app自身であり、このスキルもtask runnerもIssueを作成しない。

### 意味評価の範囲

azdはプロジェクトが要求する下限、Azure Functions extension bundleはサポート対象のmajor versionの範囲であり、exact pinとして扱わない。azdの`requiredVersions`仕様への適合、入力済み更新候補の互換性と移行条件、Functions bundleのsupport範囲、Docker base imageのEOLを公式情報で評価する。Functions bundleはlatestとの比較対象にしない。

週次workflowは固定した製品情報源だけへ通信を許可するため、リポジトリ内の任意の公開リンクを週次対象に含めない。

## 既存経路との境界

- 通常のversion更新候補はすべてRenovateが検出する。Python依存、GitHub Actions、Docker image、actionlint、kubeconform、Chaos Mesh Helm chart、Renovate validator image、Bicep CLI、uv、azdが対象である。このスキルは同じ更新候補を列挙しない。
- リポジトリ内のversion契約（RenovateのprHourlyLimit、座標とmatch数、Dependabot version updateの停止、Lefthookの座標とchecksum形式、azdとFunctions bundleのrange構文）は`check-version-pins`が検証する。Renovate configの公式schema検証とRE2抽出照合は`check-renovate-config`が検証する。このスキルはこれらを再実行しない。
- Docker base imageのdigest固定座標とcoverageは`.github/repo-health.toml`の`docker-base-digest`ルール（`check-repo-health`）が検証する。このスキルはEOLだけを意味評価する。
- 公開Markdownリンクは到達性とredirect先を確認する。参照内容の意味が現在の実装と一致するかは、review-repo agentが文書種別の評価基準に従って判断する。

## 情報源

製品の公式ドキュメント、公式release、公式registry metadataを優先する。GitHub Releasesなど一般の公開情報には、利用可能な`gh api`または`curl`を使う。認証情報を要求する非公開endpointは使わない。

Microsoft製品の文書は、Microsoft Learn MCPが利用できる場合は文書検索ツールを使う。利用できない場合は`mslearn` CLIの検索機能を使う。週次workflowのFunctions extension bundle確認では、許可された`learn.microsoft.com`をPython標準ライブラリで取得できる。いずれの取得経路も利用できない場合は対象を`unverified`とする。

azdのrange意味評価は`https://learn.microsoft.com/azure/developer/azure-developer-cli/azd-schema`の`requiredVersions`節を固定の情報源とする。Azure Functions extension bundleのsupport範囲意味評価は`https://learn.microsoft.com/azure/azure-functions/functions-bindings-register#extension-bundles`を固定の情報源とする。どちらも取得できない場合は対象を`unverified`とする。

SKILL.mdには判断規則だけを置く。変化するversion値、EOL日、digestは実行時に取得し、根拠URLと確認時刻を結果へ記録する。

## 結果の引き継ぎと判断

1. scheduledでは入力findingの`status`、`reason_code`、`current`、`published`、`evidence`と全体`status`/`coverage`を、入力の検出結果としてそのまま採用する。fullでは検査結果JSONの`checks[].status`/`reason_code`を引き継ぐ。同じ検出・検証をこのスキルがやり直さない。
2. `reason_code: update-available`は更新候補が確定しているが互換性レビュー待ち（`unverified`）、`reason_code: evidence-unavailable`は公開情報の取得不能（`unverified`）を表す。自然言語の標準出力だけで判定を置き換えない。
3. 意味評価は入力の検出結果とは別のfindingとし、`subject`と`reason`で評価対象と判断根拠を明記する。入力findingを意味評価の結果で上書きしない。
4. fullでは、公開Markdownリンクを重複URLごとに一度取得する。`404`と`410`は`fail`、timeout、TLS障害、rate limit、server errorは`unverified`とする。version候補、EOL、support範囲、互換性は再評価しない。
5. 各対象の入力座標、確認済み座標、未検証座標、除外座標を数え、根拠URLと確認時刻を返す。同じ座標の検出結果と意味評価を別findingにしても、coverageの座標数を水増ししない。出力全体のstatusは、引き継いだ結果と今回の評価を下記の規則で合成する。

最新版との差だけでは`fail`にしない。リポジトリの明示規則違反、EOL済み、存在しないversion、digest固定規則違反、support対象外のFunctions extension bundleは`fail`候補とし、根拠を示す。取得不能、公式情報間の不一致、定義元が未決定の場合は`unverified`とする。既存検査が担当する違反は、その結果を引き継ぐ。

## 出力schema

```json
{
  "schema_version": 1,
  "inventory_commit": "<commit>",
  "checked_at": "<RFC 3339>",
  "status": "pass | fail | unverified | excluded",
  "coverage": {
    "inventory_coordinates": 0,
    "checked_coordinates": 0,
    "unverified_coordinates": 0,
    "excluded_coordinates": 0
  },
  "findings": [
    {
      "subject": "<tool or dependency>",
      "coordinate": "<inventory coordinate>",
      "status": "pass | fail | unverified | excluded",
      "reason_code": "<stable machine-readable reason code>",
      "current": "<inventory value>",
      "published": "<public value or null>",
      "evidence": ["<official URL>"],
      "impact_questions": ["<question to resolve before updating>"],
      "reason": "<classification reason>"
    }
  ],
  "environment_limitations": ["<network or tool limitation>"]
}
```

全体の`status`は、`fail`があれば`fail`、`fail`がなく`unverified`があれば`unverified`、検査対象がすべて理由付きで対象外なら`excluded`、それ以外は`pass`とする。
