---
name: repository-freshness-checker
description: repo health inventory JSONを入力に、依存やツールの最新版、EOL、GAを公式公開情報で確認するcheck-onlyスキル。review-repo full、全検査、鮮度確認で使用する。
---

# Repository Freshness Checker

repo health inventory JSONに記録された座標を、公式公開情報と比較する。結果は`review-repo`へ返し、このスキルはファイルを変更しない。

## 適用範囲

次の対象だけを確認する。

- gh-aw
- Lefthook
- actionlint
- kubeconform
- azd
- Chaos Mesh Helm chart
- Docker base imageのEOLとdigest固定状況

inventoryに存在しない対象を推測で補わない。各対象について、入力座標、確認済み座標、未検証座標、除外座標を数える。

## check-only契約

- ファイルの編集、自動更新、commit、push、PR作成を禁止する。
- Azure subscription、AKS cluster、Fleet、resource provider、feature登録状態など、認証が必要なAzure実環境を照会しない。
- 公開情報を取得できない場合は`unverified`とし、現在値を最新と推定しない。
- major更新、EOLを伴う移行、Helm chart更新、base image変更を自動推奨しない。互換性、移行手順、廃止日、サポート範囲、検証項目を影響確認事項として示す。

## 既存経路との境界

- Bicep CLIは`bicep-version-check.yml`が確認する。
- AKS更新情報は`aks-updates-analyzer`が確認する。
- Bicep resource API versionは`bicep-api-version-updater`のcheck-onlyモードが確認する。
- Python依存、GitHub Actions、Dockerの更新候補はDependabotが扱う。このスキルは同じ更新候補を列挙しない。
- Dockerについて、このスキルはbase imageのEOLとdigest固定状況だけを扱う。

## 情報源

製品の公式ドキュメント、公式release、公式registry metadataを優先する。GitHub Releasesなど一般の公開情報には、利用可能な`gh api`または`curl`を使う。認証情報を要求する非公開endpointは使わない。

Microsoft製品の文書は、Microsoft Learn MCPが利用できる場合は文書検索ツールを使う。利用できない場合は`mslearn` CLIの検索機能を使う。どちらも利用できない場合は対象を`unverified`とする。

SKILL.mdには判断規則だけを置く。変化するversion値、EOL日、digestは実行時に取得し、根拠URLと確認時刻を結果へ記録する。

## 判断規則

1. inventory JSONのschema versionと対象commitを記録する。解釈できないschemaは全体を`unverified`とする。
2. 対象ごとに現在値と正本座標をinventoryから取得する。
3. 公式公開情報から安定版、サポート状態、EOL、digestを確認する。
4. 現在値と公開情報を比較し、状態と根拠を記録する。
5. 更新候補がある場合は、差分だけでなく影響確認事項を示す。

最新版との差だけでは`fail`にしない。リポジトリの明示規則違反、EOL済み、存在しないversion、digest固定規則違反は`fail`候補とし、根拠を示す。取得不能、公式情報間の不一致、正本未決定は`unverified`とする。

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
