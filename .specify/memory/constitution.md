<!--
  Sync Impact Report
  ==================
  Version change: 1.0.0 → 2.0.0 (MAJOR)
  
  Modified principles:
  - I. 品質基準 → I. コード品質基準（Python）: IaC分離のため名称変更
  
  Added sections:
  - II. インフラストラクチャ品質基準（Bicep）: IaC固有の品質基準を追加
  - IV. Spec Kit ワークフロー: 具体的なコマンドとフェーズを明示
  - V. テスト方針: テスト駆動の原則を明確化
  - Governance: バージョニングポリシー、改訂手順、コンプライアンス確認を追加
  
  Removed sections:
  - なし
  
  Templates requiring updates:
  - ✅ plan-template.md: Constitution Check セクションは既存原則と整合
  - ✅ spec-template.md: 要件定義は本 Constitution の品質基準と整合
  - ✅ tasks-template.md: Phase 構造は Spec Kit ワークフロー原則と整合
  
  Follow-up TODOs:
  - なし
-->

# AKS Chaos Lab Constitution

**Version**: 2.0.0  
**Ratified**: 2025-01-13  
**Last Amended**: 2025-12-08

---

## Core Principles

### I. コード品質基準（Python）

本プロジェクトの Python コードは以下の品質基準を満たさなければならない（MUST）。

- **型チェック**: pyright エラー 0 件必須
- **リント**: ruff 警告 0 件必須
- **テスト**: pytest 全テスト合格必須
- **品質検証コマンド**: 実装時は必ず `cd src && make qa` を実行し、全て合格を確認する

**根拠**: 型安全性とコード一貫性を保証し、レビュー負荷を軽減する。

### II. インフラストラクチャ品質基準（Bicep）

本プロジェクトの Bicep コードは以下の品質基準を満たさなければならない（MUST）。

- **構文検証**: `az bicep build` でエラー 0 件必須
- **リント**: Bicep linter 警告は可能な限り解消する（SHOULD）
- **命名規則**: `infra/abbreviations.json` に従ったリソース命名を使用する
- **モジュール化**: 再利用可能なリソースは `infra/modules/` に分離する

**根拠**: IaC の品質を担保し、インフラ変更の安全性を確保する。

### III. ドキュメント管理

プロジェクトドキュメントは以下の方針で管理しなければならない（MUST）。

- **主要ドキュメント**: `/docs/` 直下の `requirements.md`、`design.md`、`api.md`、`deployment.md` を常に最新に保つ
- **即時更新**: コード変更に伴うドキュメント更新は同一 PR 内で実施する
- **図表形式**: Mermaid 記法を使用する
- **履歴管理**: 廃止されたドキュメントは `/docs/history/` に移動する

**根拠**: ドキュメントとコードの乖離を防ぎ、オンボーディングを容易にする。

### IV. Spec Kit ワークフロー

機能開発は Spec Kit ワークフローに従わなければならない（MUST）。

| フェーズ | コマンド | 成果物 |
|---------|----------|--------|
| 1. 仕様定義 | `/speckit.specify` | `spec.md` |
| 2. 計画策定 | `/speckit.plan` | `plan.md`, `research.md`, `data-model.md`, `quickstart.md` |
| 3. タスク分解 | `/speckit.tasks` | `tasks.md` |
| 4. 実装 | `/speckit.implement` | ソースコード |

- **明示的ハンドオフ**: 各フェーズは独立したコマンドで実行され、ユーザーが次のコマンドを呼び出すまで進行しない

**適用除外**: 以下のような変更は Spec Kit ワークフローを経由せず直接実施してよい：
- API バージョンの更新（Azure API、Kubernetes API など）
- 依存パッケージのバージョン更新（セキュリティパッチ、バグ修正）
- 軽微なドキュメント修正（誤字、リンク修正）

**根拠**: 仕様駆動開発により、手戻りを最小化し、トレーサビリティを確保する。

### V. テスト方針

テストは以下の方針に従わなければならない（MUST）。

- **テストファースト**: 新機能のテストは実装前に作成し、失敗を確認してから実装する（SHOULD）
- **テスト配置**: ユニットテストは `src/tests/unit/`、統合テストは `src/tests/integration/` に配置する
- **カバレッジ**: 重要なビジネスロジックは必ずテストでカバーする

**根拠**: 回帰を防止し、リファクタリングの安全性を担保する。

---

## Governance

### 優先順位

本 Constitution はすべてのプラクティスおよびガイドラインに優先する。矛盾がある場合は本 Constitution に従う。

### 改訂手順

1. **提案**: 改訂提案は Issue または PR で文書化する
2. **レビュー**: プロジェクトオーナーによるレビューを経る
3. **承認**: 明示的な承認を得る
4. **適用**: 本ファイルを更新し、Sync Impact Report をコメントに追記する

### バージョニングポリシー

- **MAJOR**: 原則の削除、または後方互換性のない変更
- **MINOR**: 新しい原則やセクションの追加、重要な拡張
- **PATCH**: 文言の明確化、誤字修正、非意味的な変更

### コンプライアンス確認

- **PR レビュー時**: すべての PR は本 Constitution への準拠を確認する
- **定期確認**: 四半期ごとに Constitution の妥当性を見直す（SHOULD）
