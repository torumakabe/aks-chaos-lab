# Tasks: uv への依存パッケージ管理一本化

**Input**: Design documents from `/specs/002-uv-consolidation/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md

**Tests**: テストは明示的に要求されていないため、既存の `make qa` で検証します。

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: 環境確認と準備

- [X] T001 uv.lock ファイルが存在することを確認（`src/uv.lock`）
- [X] T002 [P] .dockerignore に .venv が含まれていることを確認

---

## Phase 2: User Story 1 - コンテナビルドでuvを使用 (Priority: P1) 🎯 MVP

**Goal**: Dockerfile を uv sync ベースに移行し、Docker ビルドが正常に動作することを確認

**Independent Test**: `docker build` と `docker run` でヘルスチェックが成功

### Implementation for User Story 1

- [X] T003 [US1] Dockerfile を uv ベースに書き換え in `src/Dockerfile`
  - マルチステージビルドで uv バイナリをコピー（`FROM ghcr.io/astral-sh/uv:0.9.16 AS uv`）
  - pyproject.toml と uv.lock のコピー
  - `uv sync --locked --no-install-project --compile-bytecode`
  - アプリケーションコードのコピー
  - PATH に .venv/bin を追加
- [X] T004 [US1] Docker ビルドを実行し成功を確認
- [X] T005 [US1] コンテナを起動し、ヘルスチェック（`curl http://localhost:8000/health`）が 200 を返すことを確認

**Checkpoint**: User Story 1 完了 - Docker ビルドが uv ベースで動作

---

## Phase 3: User Story 2 - requirements.txt ファイルの廃止 (Priority: P2)

**Goal**: requirements.txt を削除し、pyproject.toml のみで依存関係を管理

**Independent Test**: requirements.txt 削除後も Docker ビルドとローカル開発が動作

### Implementation for User Story 2

- [X] T006 [US2] requirements.txt を削除 `rm src/requirements.txt`
- [X] T007 [US2] Docker ビルドが引き続き成功することを確認
- [X] T008 [US2] ローカルで `uv sync` が正常に動作することを確認

**Checkpoint**: User Story 2 完了 - requirements.txt が廃止され、依存管理が一元化

---

## Phase 4: User Story 3 - Makefile の更新 (Priority: P3)

**Goal**: Makefile から不要なターゲットを削除し、新しいターゲットを追加

**Independent Test**: `make help` で requirements が表示されず、`make qa` が成功

### Implementation for User Story 3

- [X] T009 [US3] Makefile から `requirements` ターゲットを削除 in `src/Makefile`
- [X] T010 [US3] `.PHONY` 宣言から `requirements` を削除 in `src/Makefile`
- [X] T011 [P] [US3] Makefile に `check-uv-version` ターゲットを追加 in `src/Makefile`
- [X] T012 [US3] `make help` で requirements が表示されないことを確認
- [X] T013 [US3] `make qa` が正常に動作することを確認

**Checkpoint**: User Story 3 完了 - Makefile が更新され、uv バージョン検証が可能

---

## Phase 5: User Story 4 - CI ワークフローの更新 (Priority: P4)

**Goal**: CI ワークフローで公式 Action を使用し、uv バージョンを `pyproject.toml` と一致させる

**Independent Test**: GitHub Actions ワークフローが成功する

### Implementation for User Story 4

- [X] T017 [US4] CI ワークフローで `astral-sh/setup-uv@v7` を使用 in `.github/workflows/ci.yml`
  - 手動キャッシュ設定を削除（setup-uv が自動管理）
  - `version-file: "src/pyproject.toml"` でバージョンを指定（`[tool.uv] required-version`）
- [X] T018 [US4] `uv sync --group dev` を `uv sync --group dev --locked` に変更
- [X] T019 [US4] `setup-python` Action を削除（setup-uv が Python も管理可能）
- [X] T020 [US4] ローカルで CI ワークフローの YAML 構文を検証
- [X] T021 [US4] GitHub にプッシュして CI が成功することを確認

**Checkpoint**: User Story 4 完了 - CI が公式 Action を使用し、バージョン一貫性が確保

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 最終検証とドキュメント更新

- [X] T014 [P] `make check-uv-version` が正常に動作することを確認
- [X] T022 全成功基準（SC-001〜SC-007）の最終確認
- [X] T023 spec.md の Status を In Progress から Complete に更新

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - 即時開始可能
- **User Story 1 (Phase 2)**: Setup 完了後に開始
- **User Story 2 (Phase 3)**: User Story 1 完了後に開始（Dockerfile が先に変更されている必要あり）
- **User Story 3 (Phase 4)**: Setup 完了後に開始可能（User Story 1/2 と並行可能だが、検証は後で実施）
- **User Story 4 (Phase 5)**: `.uv-version` 作成後（Phase 4 完了後）に開始
- **Polish (Phase 6)**: すべての User Story 完了後

### Parallel Opportunities

- T001 と T002 は並行実行可能
- T009, T010, T011 は同一ファイルだが論理的に独立（T011 は別セクション追加のため [P] 可能）

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup 完了
2. Phase 2: User Story 1 完了
3. **STOP and VALIDATE**: Docker ビルドとヘルスチェック確認
4. この時点で MVP として動作可能

### Incremental Delivery

1. User Story 1 完了 → Docker ビルドが uv ベースで動作
2. User Story 2 完了 → requirements.txt 削除完了
3. User Story 3 完了 → Makefile 更新完了、運用ツール追加

---

## Success Criteria Mapping

| 成功基準 | 対応タスク |
|---------|-----------|
| SC-001: Docker ビルド成功 | T004 |
| SC-002: ヘルスチェック 200 | T005 |
| SC-003: make qa 成功 | T013 |
| SC-004: requirements.txt 削除 | T006 |
| SC-005: requirements ターゲット削除 | T009, T012 |
| SC-006: CI で setup-uv 使用 | T017, T019 |
| SC-007: CI ワークフロー成功 | T021 |

---

## Notes

- テストは既存の `make qa` で検証（新規テスト作成は不要）
- uv バージョンは 0.9.16（ローカルと一致）
- Constitution IV により、今後の uv バージョン更新は Spec Kit ワークフロー不要
