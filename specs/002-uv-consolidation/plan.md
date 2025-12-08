# Implementation Plan: uv への依存パッケージ管理一本化

**Branch**: `002-uv-consolidation` | **Date**: 2025-12-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-uv-consolidation/spec.md`

## Summary

Python アプリケーションの依存パッケージ管理を uv に一本化し、Docker ビルドで pip/requirements.txt の代わりに uv sync を使用する。これにより、ローカル開発とコンテナビルドで同一のパッケージマネージャーを使用し、環境差異によるトラブルを防止する。

## Technical Context

**Language/Version**: Python 3.13  
**Primary Dependencies**: FastAPI, uvicorn, uv 0.9.16  
**Storage**: Redis（外部サービス）  
**Testing**: pytest, ruff, mypy（`make qa` で統合実行）  
**Target Platform**: Linux コンテナ（AKS 上で実行）  
**Project Type**: single（Python Web API）  
**Performance Goals**: 既存のパフォーマンス要件を維持  
**Constraints**: Docker ビルドキャッシュ効率の維持、non-root ユーザー実行  
**Scale/Scope**: 単一アプリケーション、Dockerfile + Makefile の変更

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. コード品質基準（Python） | ✅ PASS | `make qa` で検証予定 |
| III. ドキュメント管理 | ✅ PASS | specs/ で管理 |
| IV. Spec Kit ワークフロー | ✅ PASS | 正しいフェーズ順序で実行 |
| V. テスト方針 | ✅ PASS | 既存テストでカバー |

## Project Structure

### Documentation (this feature)

```text
specs/002-uv-consolidation/
├── plan.md              # This file
├── research.md          # uv Docker best practices
├── quickstart.md        # Quick implementation guide
├── spec.md              # Feature specification
├── tasks.md             # Implementation tasks
└── checklists/
    └── requirements.md
```

### Source Code (affected files)

```text
src/
├── Dockerfile           # 変更対象: pip → uv sync
├── Makefile             # 変更対象: requirements 削除, check-uv-version 追加
├── .uv-version          # 新規: uv バージョン一元管理
├── pyproject.toml       # 既存（変更なし）
├── uv.lock              # 既存（変更なし）
├── requirements.txt     # 削除対象
└── app/                 # 変更なし
    ├── __init__.py
    ├── main.py
    └── ...

.github/workflows/
└── ci.yml               # 変更対象: pip install uv → astral-sh/setup-uv
```
```

**Structure Decision**: Single project 構造。src/ 配下の Dockerfile と Makefile のみ変更。

## Complexity Tracking

> 該当なし - Constitution 違反なし
