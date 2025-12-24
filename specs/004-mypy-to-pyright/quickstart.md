# Quickstart: 型チェッカーをmypyからpyrightへ移行

**Feature**: 004-mypy-to-pyright  
**Date**: 2024-12-24

## 概要

このドキュメントは、mypyからpyrightへの型チェッカー移行を実装するためのクイックスタートガイドです。

## 前提条件

- Python 3.13
- uv パッケージマネージャー
- 既存の開発環境がセットアップ済み

## 実装手順

### Step 1: 依存関係の更新

`src/pyproject.toml`で以下を変更:

1. `mypy>=1.8.0`を削除
2. `pyright>=1.1.0`を追加
3. `[tool.mypy]`セクションを削除
4. `[tool.pyright]`セクションを追加

### Step 2: Makefileの更新

`src/Makefile`で以下を変更:

1. `typecheck`ターゲット: `mypy` → `pyright`
2. `qa`ターゲット: `mypy` → `pyright`
3. `clean`ターゲット: `.mypy_cache`を削除

### Step 3: Constitutionの更新

`.specify/memory/constitution.md`で以下を変更:

1. 「mypy エラー 0 件必須」→「pyright エラー 0 件必須」

### Step 4: 依存関係の同期

```bash
cd src
uv sync --group dev
```

### Step 5: 検証

```bash
cd src
make typecheck  # pyrightが実行されることを確認
make qa         # 全チェックがパスすることを確認
```

## 変更ファイル一覧

| ファイル | 変更内容 |
|----------|----------|
| `src/pyproject.toml` | 依存関係と設定の更新 |
| `src/Makefile` | コマンドの更新 |
| `.specify/memory/constitution.md` | 型チェッカー名の更新 |

## ロールバック手順

問題が発生した場合:

```bash
git checkout main -- src/pyproject.toml src/Makefile .specify/memory/constitution.md
cd src && uv sync --group dev
```
