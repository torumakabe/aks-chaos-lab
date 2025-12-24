# Tasks: 型チェッカーをmypyからpyrightへ移行

**Input**: Design documents from `/specs/004-mypy-to-pyright/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, quickstart.md

**Tests**: テスト作成は不要（既存テストで検証可能）

**Organization**: タスクはユーザーストーリーごとにグループ化

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (依存関係の更新)

**Purpose**: pyrightの追加とmypyの削除

- [x] T001 pyproject.tomlでmypy依存関係を削除し、pyrightを追加する in `src/pyproject.toml`
- [x] T002 pyproject.tomlで`[tool.mypy]`セクションを削除する in `src/pyproject.toml`
- [x] T003 pyproject.tomlに`[tool.pyright]`セクションを追加する in `src/pyproject.toml`
- [x] T004 `uv sync --group dev`を実行して依存関係を更新

---

## Phase 2: User Story 1 & 2 - 型チェック・QAワークフローの実行 (Priority: P1) 🎯 MVP

**Goal**: `make typecheck`と`make qa`がpyrightを使用して正常に動作する

**Independent Test**: `cd src && make typecheck && make qa`

### Implementation

- [x] T005 [US1] Makefileの`typecheck`ターゲットを`mypy`から`pyright`に変更 in `src/Makefile`
- [x] T006 [US2] Makefileの`qa`ターゲットを`mypy`から`pyright`に変更 in `src/Makefile`
- [x] T007 [US1] 型チェックを実行して全コードがパスすることを確認: `cd src && make typecheck`
- [x] T008 [US2] QAワークフローを実行して全チェックがパスすることを確認: `cd src && make qa`

**Checkpoint**: `make typecheck`と`make qa`がpyrightで正常動作することを確認

---

## Phase 3: User Story 3 - クリーンアップの実行 (Priority: P2)

**Goal**: `make clean`がpyrightキャッシュを削除する

**Independent Test**: `cd src && make clean && ls -la`でキャッシュが削除されていることを確認

### Implementation

- [x] T009 [US3] Makefileの`clean`ターゲットから`.mypy_cache`を削除 in `src/Makefile`

**Checkpoint**: `make clean`が正常動作することを確認

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: ドキュメント更新と最終検証

- [x] T010 [P] Constitutionの型チェッカー記述を「mypy」から「pyright」に更新 in `.specify/memory/constitution.md`
- [x] T011 最終検証: `cd src && make qa`を実行して全チェックがパスすることを確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies - 最初に実行
- **Phase 2 (US1 & US2)**: Phase 1の完了後に実行
- **Phase 3 (US3)**: Phase 2と並行可能
- **Phase 4 (Polish)**: Phase 2, 3の完了後に実行

### Within Each Phase

- T001 → T002 → T003 → T004: 順次実行（同一ファイル）
- T005 → T007: US1の実装と検証
- T006 → T008: US2の実装と検証
- T010 は他と並行可能

---

## Parallel Example: Phase 2

```bash
# US1とUS2のMakefile変更は同じファイルのため順次実行
Task T005: typecheck ターゲット更新
Task T006: qa ターゲット更新
# 検証は順次
Task T007: make typecheck 確認
Task T008: make qa 確認
```

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 2)

1. Phase 1を完了: pyproject.toml更新
2. Phase 2を完了: Makefile更新と検証
3. **STOP and VALIDATE**: `make typecheck && make qa`

### Full Implementation

1. Phase 1 + 2: 基本移行完了
2. Phase 3: クリーンアップ対応
3. Phase 4: ドキュメント更新と最終検証

---

## Notes

- 全タスクは同一ファイルへの変更が多いため、基本的に順次実行
- T010（Constitution更新）は他と並行可能
- 各Phaseのチェックポイントで検証を実施
- 問題発生時は`git checkout`でロールバック可能
