---
name: repository-freshness-checker
description: review-repo fullが生成したinventoryを使い、公開Markdownリンク、Docker base imageのEOL、Azure Functions extension bundleのsupport範囲をcheck-onlyで確認する。明示された全検査や鮮度確認で使用する。
---

# Repository Freshness Checker

`review-repo full`の決定論的検査結果を引き継ぎ、公開情報が必要な項目だけを確認する。通常のversion更新候補はRenovate、Renovateが安全な更新PRを完結できないツールは通常のGitHub Actions workflowが担当する。このスキルは同じ更新候補を再検出しない。

## 入力と確認範囲

入力は、隔離workspaceで実行した次のコマンドが生成するinventory JSONと検査結果JSONである。

```text
review-repo-full --inventory-json <absolute-path> --results-json <absolute-path>
```

inventoryのschema versionと対象commitを記録し、次の座標だけを処理する。

| inventory category | 確認内容 |
|---|---|
| `documentation-external-link` | 公開Markdownリンクの到達性とredirect先 |
| `docker-base-image` | 使用中のbase image系列がEOL済みか |
| `function-extension-bundle` | 宣言したmajor version rangeがMicrosoftのsupport対象か |

別のinventory生成コマンドを実行せず、入力されたJSONだけを使用する。入力JSONが欠落している場合、schemaを解釈できない場合、対象座標が不完全な場合は、影響する範囲を`unverified`とする。欠落した値を推測しない。

## check-only契約

- ファイルの編集、自動更新、commit、push、PR作成を行わない。
- Azure subscription、AKS cluster、Fleet、resource provider、feature登録状態など、認証が必要なAzure実環境を照会しない。
- 公開情報を取得できない場合は`unverified`とし、現在値を最新またはsupport対象と推定しない。
- EOL済みまたはsupport対象外を検出しても自動更新しない。影響、移行先、確認が必要な互換性を報告する。

## 責務の境界

- Python依存、GitHub Actions、Docker image tag、actionlint、kubeconform、Chaos Mesh Helm chart、Renovate validator image、Bicep CLI、uv、azdの更新候補はRenovateが検出する。
- gh-awとLefthookの更新候補、更新処理、PR作成は`.github/workflows/repository-freshness-check.yml`が担当する。
- リポジトリ内のversion契約は`check-version-pins`、Docker base imageのdigest固定は`.github/repo-health.toml`の`docker-base-digest`ルールが検証する。
- Bicep resource API versionは`bicep-api-version-updater`のcheck-onlyモードが担当する。
- 公開Markdownリンクの内容が現在の実装と一致するかは、review-repo agentが文書種別の評価基準に従って判断する。

## 情報源と判定

公式ドキュメント、公式release、公式registry metadataを優先する。Microsoft製品の文書はMicrosoft Learn MCPを使用し、利用できない場合は`mslearn` CLIを使用する。いずれも利用できない場合は対象を`unverified`とする。

Azure Functions extension bundleは、次の固定情報源でsupport範囲を確認する。

https://learn.microsoft.com/azure/azure-functions/functions-bindings-register#extension-bundles

公開Markdownリンクは重複URLごとに一度取得する。`404`と`410`は`fail`、timeout、TLS障害、rate limit、server errorは`unverified`とする。

Docker base imageは、inventoryに記録されたrepositoryとtagから公式のsupportまたはEOL情報を確認する。digest固定の有無は再検査しない。EOL済みは`fail`候補、将来のEOL日は`pass`として日付を記録し、公式情報がない場合は`unverified`とする。

Functions extension bundleは、range構文の妥当性を`check-version-pins`の結果から引き継ぐ。宣言したmajor versionがsupport対象外なら`fail`、support対象なら`pass`、公開情報を取得できなければ`unverified`とする。latestとの比較は行わない。

## 結果

各対象について入力座標数、確認済み座標数、未検証座標数、除外座標数を数える。同じURLやimageを一度だけ取得しても、coverageはinventory座標単位で数える。

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
      "subject": "<link, image, or extension bundle>",
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

全体statusは、`fail`があれば`fail`、`fail`がなく`unverified`があれば`unverified`、検査対象がすべて理由付きで対象外なら`excluded`、それ以外は`pass`とする。
