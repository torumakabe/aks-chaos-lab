# Quickstart: Spec Kit移行

**Date**: 2025-12-08  
**Feature**: 001-speckit-migration

## 概要

このドキュメントは、Spec Kit移行後のワークフローを簡潔に説明します。

## 前提条件

- VS Code + GitHub Copilot拡張機能
- Spec Kitがインストール済み（`.specify/` ディレクトリが存在）

## 新しいワークフロー

### 1. 新機能の仕様作成

```
/speckit.specify [機能の説明]
```

**結果**:
- 新しい機能ブランチが作成される（例: `002-feature-name`）
- スペックファイルが作成される（`specs/002-feature-name/spec.md`）

### 2. 仕様の明確化（オプション）

```
/speckit.clarify
```

**結果**: 仕様の曖昧な部分を対話的に明確化

### 3. 実装計画の作成

```
/speckit.plan
```

**結果**:
- 計画ファイルが作成される（`specs/002-feature-name/plan.md`）
- 調査結果（`research.md`）
- データモデル（`data-model.md`）

### 4. タスク分解（オプション）

```
/speckit.tasks
```

**結果**: 計画がタスクに分解される（`specs/002-feature-name/tasks.md`）

### 5. 実装の実行

```
/speckit.implement
```

**結果**: 計画・タスクに基づいた実装が支援される

## 設定ファイルの構造

| ファイル | 役割 |
|---------|------|
| `.github/copilot-instructions.md` | AIへの指示（応答スタイル、ファイル管理） |
| `.specify/memory/constitution.md` | プロジェクトのコア原則（品質基準、ガバナンス） |

## 品質チェック

すべての実装で以下を実行:

```bash
# src ディレクトリで実行
cd src && make qa
```

または個別に:

```bash
# 型チェック
uv run mypy app

# リント
uv run ruff check app --fix

# テスト
uv run pytest -q
```

**必須基準**:
- 型チェック: 0エラー
- リント: 0警告
- テスト: 全合格

## ドキュメント管理

| ディレクトリ | 用途 |
|------------|------|
| `/docs/` | 主要ドキュメント（常に最新に保つ） |
| `/docs/history/` | 履歴的ドキュメント |
| `/specs/` | 機能仕様と実装計画 |

## トラブルシューティング

### Q: Spec Kitコマンドが動作しない

A: VS Codeを再起動し、GitHub Copilot拡張機能が有効か確認してください。

### Q: 旧ワークフローファイルはどこに？

A: 旧ファイル（spec-driven-workflow-v1.md）は削除されました。Gitの履歴から参照可能です。

### Q: constitution.mdとcopilot-instructions.mdの違いは？

A: 
- `copilot-instructions.md`: AIへの指示のみ
- `constitution.md`: プロジェクト全体のルール（品質基準、ガバナンス）
