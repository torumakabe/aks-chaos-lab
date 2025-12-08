# Tasks: Spec Kit移行

**Input**: Design documents from `/specs/001-speckit-migration/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, quickstart.md ✅

**Tests**: テストタスクは含まない（手動検証のみ）

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

この機能はドキュメント・設定ファイルの移行であり、ソースコード変更は含まない。

- `.github/copilot-instructions.md` - 更新
- `.specify/memory/constitution.md` - 更新
- `.github/prompts/spec-driven-workflow-v1.md` - 削除
- `.github/chatmodes/` - 削除

---

## Phase 1: Setup (事前確認)

**Purpose**: 移行の前提条件確認

- [x] T001 Spec Kit構造の確認（`.specify/` ディレクトリの存在確認）
- [x] T002 現在の設定ファイル状態をバックアップ（Git履歴で十分だが念のため確認）
- [x] T003 [P] 移行対象ファイルの一覧確認

**Checkpoint**: ✅ 前提条件確認完了

---

## Phase 2: Foundational (ブロッキング前提条件)

**Purpose**: すべてのユーザーストーリーに先立って完了する必要があるタスク

**⚠️ CRITICAL**: この機能では Foundational Phase は不要（ファイル移行のみのため）

**Checkpoint**: Foundation ready - ユーザーストーリー実装開始可能

---

## Phase 3: User Story 1 - Spec Kit ワークフローへの完全移行 (Priority: P1) 🎯 MVP

**Goal**: Spec Kitのコマンドが正常に動作することを確認

**Independent Test**: `/speckit.specify`、`/speckit.plan`、`/speckit.tasks`、`/speckit.implement` コマンドを使用して、新しい機能の仕様から実装までのフローが正常に動作することを確認

### Implementation for User Story 1

- [x] T004 [US1] `.specify/` ディレクトリ構造が正しいことを確認
- [x] T005 [US1] Spec Kitスクリプトに実行権限があることを確認（`.specify/scripts/bash/*.sh`）
- [x] T006 [US1] `/speckit.specify` コマンドの動作確認（別ブランチでテスト可能）

**Checkpoint**: ✅ Spec Kitコマンドが動作することを確認

---

## Phase 4: User Story 2 - 旧ワークフローからの円滑な移行 (Priority: P2)

**Goal**: 既存の良い習慣が維持されることを確認

**Independent Test**: `/docs/` 構造と品質チェックが機能することを確認

### Implementation for User Story 2

- [x] T007 [US2] `/docs/` ディレクトリ構造の確認（requirements.md、design.md、api.md、deployment.md）
- [x] T008 [US2] 品質チェックコマンドの動作確認（`cd src && make qa`）

**Checkpoint**: ✅ 既存のベストプラクティスが維持されることを確認

---

## Phase 5: User Story 3 - copilot-instructions.mdの更新 (Priority: P3)

**Goal**: copilot-instructions.mdがSpec Kitワークフローを正しく参照する

**Independent Test**: copilot-instructions.mdがSpec Kitワークフローへの参照を含むことを確認

### Implementation for User Story 3

- [x] T009 [US3] `.github/copilot-instructions.md` を更新: 仕様駆動ワークフローセクションをSpec Kit参照に変更

**Checkpoint**: ✅ copilot-instructions.mdがSpec Kitを参照

---

## Phase 6: User Story 4 - 設定ファイルの役割整理 (Priority: P4)

**Goal**: copilot-instructions.mdとconstitution.mdの役割分離

**Independent Test**: 両ファイルに重複がなく、役割が明確に分離されていることを確認

### Implementation for User Story 4

- [x] T010 [P] [US4] `.github/copilot-instructions.md` をAIへの指示のみに簡素化
- [x] T011 [P] [US4] `.specify/memory/constitution.md` にプロジェクトのコア原則を定義
- [x] T012 [US4] 両ファイル間の重複がないことを確認

**Checkpoint**: ✅ 設定ファイルの役割分離完了

---

## Phase 7: User Story 5 - chatmodesディレクトリの廃止 (Priority: P5)

**Goal**: chatmodesディレクトリを削除

**Independent Test**: `.github/chatmodes/` ディレクトリが存在しないことを確認

### Implementation for User Story 5

- [x] T013 [US5] `.github/chatmodes/` ディレクトリを削除
- [x] T014 [US5] chatmodesへの参照がプロジェクト内にないことを確認

**Checkpoint**: ✅ chatmodesディレクトリ廃止完了

---

## Phase 8: User Story 6 - 旧ワークフローファイルの削除 (Priority: P6)

**Goal**: 旧ワークフローファイルを削除

**Independent Test**: `.github/prompts/spec-driven-workflow-v1.md` が存在しないことを確認

### Implementation for User Story 6

- [x] T015 [US6] `.github/prompts/spec-driven-workflow-v1.md` を削除
- [x] T016 [US6] 旧ワークフローへの参照がプロジェクト内にないことを確認

**Checkpoint**: ✅ 旧ワークフローファイル削除完了

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 最終確認と文書化

- [x] T017 [P] quickstart.md の内容確認と必要に応じた更新
- [x] T018 [P] Spec Kitコマンドの最終動作確認
- [x] T019 変更のコミットとプルリクエスト作成準備

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし - すぐに開始可能
- **Foundational (Phase 2)**: Setup完了後 - この機能では実質スキップ
- **User Stories (Phase 3-8)**: 順次実行（各ストーリーは独立してテスト可能）
- **Polish (Phase 9)**: すべてのユーザーストーリー完了後

### User Story Dependencies

| ストーリー | 依存関係 | 独立テスト |
|-----------|---------|-----------|
| US1 (P1) | なし | Spec Kitコマンド動作確認 |
| US2 (P2) | なし | /docs/ 構造と品質チェック確認 |
| US3 (P3) | なし | copilot-instructions.md 参照確認 |
| US4 (P4) | US3完了推奨 | 両ファイル重複なし確認 |
| US5 (P5) | なし | chatmodes削除確認 |
| US6 (P6) | US3, US4完了推奨 | 旧ファイル削除確認 |

### Parallel Opportunities

- T010, T011 は並列実行可能（異なるファイル）
- T013, T015 は並列実行可能（異なるファイル/ディレクトリ）
- T017, T018 は並列実行可能（確認作業）

---

## Implementation Strategy

### MVP First (User Story 1-3 Only)

1. Phase 1: Setup 完了
2. Phase 3: US1 完了 → Spec Kitコマンド動作確認
3. Phase 4: US2 完了 → 既存習慣維持確認
4. Phase 5: US3 完了 → copilot-instructions.md更新
5. **STOP and VALIDATE**: 基本移行が完了

### Full Migration

1. MVP完了後
2. Phase 6: US4 完了 → 役割分離
3. Phase 7: US5 完了 → chatmodes削除
4. Phase 8: US6 完了 → 旧ファイル削除
5. Phase 9: Polish → 最終確認

---

## Notes

- [P] tasks = 異なるファイル、依存関係なし
- [Story] label = 特定のユーザーストーリーへのマッピング
- 各ユーザーストーリーは独立して完了・テスト可能
- コミットは各タスクまたは論理グループごとに実施
- 任意のチェックポイントで停止して独立検証可能
