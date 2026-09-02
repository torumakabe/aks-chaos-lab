---
name: repository-freshness-checker
description: scheduled checkerの結果を入力に依存やツールのEOL、support範囲、互換性を意味評価し、review-repo fullでは公開Markdownリンクだけを確認するcheck-onlyスキル。全検査、鮮度確認で使用する。
---

# Repository Freshness Checker

週次workflowでは、`inventory-repo --format json`が出力するrepo health inventory JSONに記録された座標と、`freshness-checks` task targetが出力する機械可読JSONを公式公開情報と比較する。`freshness-checks`の各findingの`status`、`reason_code`、`current`、`published`、`evidence`をそのまま採用し、自然言語の標準出力解釈だけに依存しない。review-repo fullでは、`review-repo-full --inventory-json <absolute-path> --results-json <absolute-path>`が生成した二つのJSONファイルを入力に、公開Markdownリンクだけを確認する。結果は呼び出し元へ返し、このスキルはファイルを変更しない。

## 適用範囲

週次workflowでは、次の8対象と、Renovateが担当する対象の前提であるRenovate appの公開活動を確認する。このスキルはどの対象についても最新版候補を自分では検出しない。Renovateまたはscheduled checkerが決定的に検出した結果を集約し、EOL、support範囲、互換性だけを意味評価する。

| 対象 | 更新候補の検出 |
|---|---|
| gh-aw | scheduled checker（`freshness-checks`の`gh-aw` finding） |
| Lefthook | scheduled checker（`freshness-checks`の`Lefthook` finding） |
| Renovate appの公開活動 | scheduled checker（`freshness-checks`の`Renovate app activity` finding） |
| actionlint | Renovate。Renovate appが稼働している場合にだけ有効 |
| kubeconform | Renovate。Renovate appが稼働している場合にだけ有効 |
| Chaos Mesh Helm chart | Renovate。Renovate appが稼働している場合にだけ有効 |
| Docker base imageのEOLとdigest固定状況 | Renovateがtagとdigestを更新し、digest固定の座標は`.github/repo-health.toml`の`docker-base-digest`ルールが検証する |
| azd | Renovate。`Azure/azure-dev`の安定版releaseからminimum versionの更新候補を検出する |
| Azure Functions extension bundleのsupport範囲 | latestとの比較対象外。`check-version-pins`がrange構文と正本座標を検証する |

gh-awとLefthookはRenovateが扱わない。gh-awのpinはcompilerと生成物のversionを一体で決めるため`gh aw compile`が所有し、Lefthookはversionとchecksumを一体更新する必要があるため専用の更新taskが所有する（[docs/workarounds.md §D-12](../../../docs/workarounds.md)）。この2対象と、Renovate自身の稼働確認だけがscheduled checkerの担当である。

Renovate custom managerの設定が契約どおりでschema上も妥当であることは、Renovate appがこのリポジトリで実際に動いている証拠にならない。appが未導入、無効化、権限喪失のいずれかになると、Renovateが担当する全対象の更新通知は設定が正しいまま静かに止まる。

Renovateは定期的なpingを公開しない。Dependency Dashboard issueが書き換わるのはRenovateに更新すべきことがあるときだけであり、`updated_at`は活動の下限であってheartbeatではない。そのためscheduled checkerは、GitHub上で観測できる事実を組み合わせる。Dependency Dashboard issueの`updated_at`と、Renovate app authoredのPull Request（open/closedを含む）の`created_at`/`updated_at`を機械可読APIで取得し、その最新値を直近の公開活動とみなす。14日は公開活動の観測窓であり、heartbeatの間隔ではない。

観測窓の内側に活動があれば`pass` + `reason_code: renovate-activity-observed`。Dashboardもapp authoredのPull Requestも一度も観測できなければ`unverified` + `reason_code: renovate-not-observed`。観測はあるが観測窓より古い場合は、app停止と断定せず`unverified` + `reason_code: renovate-activity-unobserved`とし、最近の公開活動から現在の稼働を確認できないと記述する。GitHub APIを照会できない、rate limitに達した、結果が不完全な場合は`unverified` + `reason_code: evidence-unavailable`になる。Dashboardが存在することだけを根拠に現在稼働していると断定しない。このスキルはRenovate appの公開活動が観測できていない状態で、Renovateが担当する対象を`pass`と報告しない。Dashboard issueを作るのはRenovate app自身であり、このスキルもtask runnerもIssueを作成しない。

azdとAzure Functions extension bundleは、exact pinではなく意図的な範囲（azdはプロジェクトが要求する下限バージョン、Functions bundleはサポート対象のmajor versionの範囲）である。azdの下限はRenovateが`Azure/azure-dev`の安定版releaseと比較して更新候補を検出し、`check-version-pins`がrange構文と正本座標の数を検証する。Functions bundleはlatestと比較して更新する対象ではない。このスキルは、azdのrangeが現行schemaの`requiredVersions`仕様に従っているか、azd更新候補の互換性と移行条件、およびFunctions bundleのrangeがサポート対象かを固定の公式情報源で意味評価する。

inventoryに存在しない対象を推測で補わない。各対象について、入力座標、確認済み座標、未検証座標、除外座標を数える。

review-repo fullから呼び出された場合は、`documentation-external-link`座標にある公開Markdownリンクの到達性だけを確認する。週次workflowが担当するversion候補、EOL、support範囲、互換性を再評価せず、scheduled結果が入力されていないことを`unverified`として報告しない。週次workflowは固定した製品情報源だけへ通信を許可するため、リポジトリ内の任意の公開リンクは週次対象に含めない。

## check-only契約

- ファイルの編集、自動更新、commit、push、PR作成を禁止する。
- Azure subscription、AKS cluster、Fleet、resource provider、feature登録状態など、認証が必要なAzure実環境を照会しない。
- 公開情報を取得できない場合は`unverified`とし、現在値を最新と推定しない。
- major更新、EOLを伴う移行、Helm chart更新、base image変更を自動推奨しない。互換性、移行手順、廃止日、サポート範囲、検証項目を影響確認事項として示す。

## 既存経路との境界

- 通常のversion更新候補はすべてRenovateが検出する。Python依存、GitHub Actions、Docker image、actionlint、kubeconform、Chaos Mesh Helm chart、Renovate validator image、Bicep CLI、uv、azdが対象である。このスキルは同じ更新候補を列挙しない。
- gh-awとLefthookの更新候補は`scripts/tasks.py`の`freshness-checks`が検出する。このスキルは同じ比較をやり直さず、findingの`status`と`reason_code`をそのまま採用する。
- リポジトリ内のversion契約（RenovateのprHourlyLimit、座標とmatch数、Dependabot version updateの停止、Lefthookの座標とchecksum形式、azdとFunctions bundleのrange構文）は`check-version-pins`が検証する。Renovate configの公式schema検証とRE2抽出照合は`check-renovate-config`が検証する。このスキルはこれらを再実行しない。
- Docker base imageのdigest固定座標とcoverageは`.github/repo-health.toml`の`docker-base-digest`ルール（`check-repo-health`）が検証する。このスキルはEOLだけを意味評価する。
- 公開Markdownリンクは到達性とredirect先を確認する。参照内容の意味が現在の実装と一致するかは、review-repo agentが文書種別の評価基準に従って判断する。

## 情報源

製品の公式ドキュメント、公式release、公式registry metadataを優先する。GitHub Releasesなど一般の公開情報には、利用可能な`gh api`または`curl`を使う。認証情報を要求する非公開endpointは使わない。

Microsoft製品の文書は、Microsoft Learn MCPが利用できる場合は文書検索ツールを使う。利用できない場合は`mslearn` CLIの検索機能を使う。週次workflowのFunctions extension bundle確認では、許可された`learn.microsoft.com`をPython標準ライブラリで取得できる。いずれの取得経路も利用できない場合は対象を`unverified`とする。

azdのrange意味評価は`https://learn.microsoft.com/azure/developer/azure-developer-cli/azd-schema`の`requiredVersions`節を固定の情報源とする。Azure Functions extension bundleのsupport範囲意味評価は`https://learn.microsoft.com/azure/azure-functions/functions-bindings-register#extension-bundles`を固定の情報源とする。どちらも取得できない場合は対象を`unverified`とする。

SKILL.mdには判断規則だけを置く。変化するversion値、EOL日、digestは実行時に取得し、根拠URLと確認時刻を結果へ記録する。

## 判断規則

1. 渡されたinventory JSONのschema versionと対象commitを記録する。review-repo fullでは別のinventory生成コマンドを実行せず、渡された検査結果JSONの`checks[].status`/`reason_code`をそのまま採用する。解釈できないschemaは全体を`unverified`とする。
2. 週次workflowでは、`freshness-checks`の機械可読JSON（`findings[]`と全体`status`/`coverage`）を入力にし、各対象の決定的な検出・検証の主体（上表）が既に出した`status`と`reason_code`をそのまま採用する。同じ検出・検証をこのスキルがやり直さない。`reason_code: update-available`は更新候補が確定しているが互換性レビュー待ち（`unverified`）、`reason_code: evidence-unavailable`は公開情報を取得できなかった（`unverified`）ことを表し、両者を区別する。
3. 週次workflowでは、Renovateが担当する対象（actionlint、kubeconform、Chaos Mesh Helm chart、Docker base image tag）は、`Renovate app activity` findingが`pass`のときにだけ通知経路が生きているとみなす。findingが`renovate-not-observed`、`renovate-activity-unobserved`、`evidence-unavailable`のいずれかの場合、これらの対象を`pass`とせず`unverified`とし、公開活動の未観測を理由として記録する。`renovate-activity-unobserved`はapp停止の証拠ではないため、停止と断定して記述しない。
4. 週次workflowでは、Renovateが検出したazd更新候補の互換性と移行条件、Azure Functions extension bundleのsupport範囲、Docker base imageのEOLなど、決定的な検出・検証の対象外の意味評価だけを固定の公式情報源から確認する。
5. 週次workflowでは、現在値と公開情報を比較して状態と根拠を記録し、更新候補がある場合は影響確認事項を示す。
6. review-repo fullでは、公開Markdownリンクを重複URLごとに一度取得する。`404`と`410`は`fail`、timeout、TLS障害、rate limit、server errorは`unverified`とし、取得できない内容を有効と推定しない。

最新版との差だけでは`fail`にしない。リポジトリの明示規則違反、EOL済み、存在しないversion、digest固定規則違反、support対象外のFunctions extension bundleは`fail`候補とし、根拠を示す。取得不能、公式情報間の不一致、正本未決定は`unverified`とする。

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
