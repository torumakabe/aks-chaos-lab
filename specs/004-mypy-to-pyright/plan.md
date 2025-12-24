# Implementation Plan: 型チェッカーをmypyからpyrightへ移行

**Branch**: `004-mypy-to-pyright` | **Date**: 2024-12-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-mypy-to-pyright/spec.md`

## Summary

Pythonの型チェッカーをmypyからpyrightに移行する。pyproject.tomlの依存関係と設定を更新し、Makefileのtypecheckコマンドを変更する。既存の全Pythonコードがpyrightの型チェックをエラーなしでパスすることを確認する。

## Technical Context

**Language/Version**: Python 3.13  
**Primary Dependencies**: pyright (新規追加), ruff, pytest, FastAPI, uvicorn  
**Storage**: N/A  
**Testing**: pytest, make qa  
**Target Platform**: Linux server (AKS)  
**Project Type**: Single project  
**Performance Goals**: N/A（開発ツールの変更のみ）  
**Constraints**: 既存のmypy設定と同等の厳格さを維持  
**Scale/Scope**: src/app/ 配下の7つのPythonファイル

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 状態 | 備考 |
|------|------|------|
| I. コード品質基準（Python） | ⚠️ 更新必要 | 「mypy エラー 0 件必須」→「pyright エラー 0 件必須」に更新が必要 |
| II. インフラストラクチャ品質基準（Bicep） | ✅ 該当なし | インフラ変更なし |
| III. ドキュメント管理 | ✅ 準拠 | 本計画で更新 |
| IV. Spec Kit ワークフロー | ✅ 準拠 | 本ワークフローに従っている |
| V. テスト方針 | ✅ 該当なし | テストコード変更なし |

**アクション**: Constitution の I. コード品質基準（Python）セクションを、移行完了後に「pyright エラー 0 件必須」に更新する。

## Project Structure

### Documentation (this feature)

```text
specs/004-mypy-to-pyright/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
src/
├── app/                 # アプリケーションコード（7ファイル）
│   ├── __init__.py
│   ├── azd_env.py
│   ├── config.py
│   ├── main.py
│   ├── models.py
│   ├── redis_client.py
│   └── telemetry.py
├── tests/               # テストコード
│   ├── unit/
│   └── integration/
├── pyproject.toml       # 設定ファイル（変更対象）
├── Makefile             # ビルドコマンド（変更対象）
└── uv.lock              # 依存関係ロック（自動更新）
```

**Structure Decision**: 既存の単一プロジェクト構造を維持。変更対象はpyproject.tomlとMakefileのみ。

## Complexity Tracking

> 該当する違反なし。シンプルな設定変更のみ。
