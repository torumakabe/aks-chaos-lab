# AKS Chaos Lab Constitution

## Core Principles

### I. 品質基準
- 型チェック: 0エラー必須
- リント: 0警告必須
- テスト: 全テスト合格必須
- 実装時は必ず `cd src && make qa` を実行し、全て合格を確認する

### II. ドキュメント管理
- /docs直下の主要ドキュメント（`requirements.md`、`design.md`、`api.md`、`deployment.md`）を常に最新に保つ
- 変更があれば即時更新する
- 図表はMermaid記法を使用
- 履歴的ドキュメントは/docs/history/に保存

### III. ガバナンス
- Spec Kitワークフローの各フェーズ間は承認ゲートを設ける
- 承認なしに次フェーズへ進まない
- 仕様書は必ずspec.md、plan.md、tasks.mdを経由して実装に進む

## Governance

- Constitutionはすべてのプラクティスに優先する
- 変更には文書化と承認が必要
- すべてのPR/レビューはこのConstitutionへの準拠を確認する

**Version**: 1.0.0 | **Ratified**: 2025-01-13
