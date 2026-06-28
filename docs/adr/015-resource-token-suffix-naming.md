# ADR-015: Azure リソース名への resourceToken サフィックス付与ルール

## Status

Accepted

## Context

`infra/main.bicep` は Azure Cloud Adoption Framework に沿った略称 (`infra/abbreviations.json`) を使い、リソース名を二つの分類で管理している。リソースグループ内で一意で、人が読む名前が運用上有用な制御用リソースは `${appName}-${environment}` を使う。グローバル一意性や外部から到達される名前が必要なリソースは、サブスクリプション ID、アプリケーション名、環境名から毎回同じ値として作る `${resourceToken}` を使う。

一部の Azure リソースは、削除後も名前や関連状態が一定期間保持される。同じ名前を再利用すると、新しく作ったリソースと過去の状態を切り分けにくくなる。

## Decision

次のいずれかを満たす Azure リソース名には `${resourceToken}` を付与する。
- グローバル一意性が必要である。
- DNS 名または外部到達名を持つ。
- ソフトデリートなどで名前が保持される。
- 同じ名前の再利用が、復旧や障害切り分けを難しくする。
- ほかのリソースから参照され、名前衝突の影響が大きい。
**Log Analytics ワークスペースは、名前保持と複数リソースからの参照を持つためこのクラスに分類し、名前を `${abbrs.operationalInsightsWorkspaces}${appName}-${environment}-${resourceToken}` とする。**

リソースグループ内で一意で、人が読む名前が運用上有用な制御用リソースと、親リソースに従属するリソースには、基準を満たすまで付与しない。Application Insights、Azure Monitor Workspace、Data Collection Endpoint、Data Collection Rule、Fleet、User Assigned Managed Identity は、現時点で広く監視リソースにサフィックスを付ける根拠がないため据え置く。

## Consequences

- `resourceToken` は決定論的なので環境内で安定するが、名前の可読性は下がる。検索にはタグ (`azd-env-name`) を使う。
- Log Analytics ワークスペースの名前変更は再作成を伴い、保存クエリ・アラート・診断設定の再リンクが必要になる。
- 据え置きリソースを将来サフィックス付きにする場合は、本 ADR の基準を根拠として別途 ADR または修正記録に残す。
