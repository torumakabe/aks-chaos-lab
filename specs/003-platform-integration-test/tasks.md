# Tasks: プラットフォーム統合テストパイプライン

**Input**: Design documents from `/specs/003-platform-integration-test/`
**Prerequisites**: plan.md (✓), spec.md (✓), research.md (✓), quickstart.md (✓)

**Tests**: 本機能ではspec.mdで明示的なテスト要求がないため、テストタスクは含まない。統合テスト自体がパイプラインの成果物である。

**Organization**: タスクはユーザーストーリーごとにグループ化し、独立した実装・テストを可能にする。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可能（異なるファイル、依存関係なし）
- **[Story]**: 所属するユーザーストーリー（US1, US2, US3, US4）
- 説明には正確なファイルパスを含める

## Path Conventions

- **ワークフロー**: `.github/workflows/`
- **テストスクリプト**: `src/tests/integration/`
- **ドキュメント**: `docs/`

---

## Phase 1: Setup (共有インフラストラクチャ)

**Purpose**: プロジェクト初期化と基本構造の準備

- [x] T001 ワークフローファイルの雛形を作成 `.github/workflows/integration-test.yml`
- [x] T002 [P] GitHub Secretsの設定手順をドキュメント化 `docs/deployment.md`

---

## Phase 2: Foundational (ブロッキング前提条件)

**Purpose**: すべてのユーザーストーリーが依存するコアインフラ

**⚠️ CRITICAL**: このフェーズが完了するまでユーザーストーリーの作業は開始不可

- [x] T003 workflow_dispatchトリガーと入力パラメータを定義 `.github/workflows/integration-test.yml`
- [x] T004 [P] concurrency設定を追加（group: integration-test, cancel-in-progress: false） `.github/workflows/integration-test.yml`
- [x] T005 [P] OIDC認証のpermissionsを設定 `.github/workflows/integration-test.yml`
- [x] T006 ジョブ間の依存関係構造を定義（validate → provision → deploy → test → cleanup） `.github/workflows/integration-test.yml`

**Checkpoint**: ワークフローの基本構造が完成 - ユーザーストーリー実装を開始可能

---

## Phase 3: User Story 1 - 手動トリガーによる統合テスト実行 (Priority: P1) 🎯 MVP

**Goal**: 開発者がGitHub Actionsから手動でパイプラインを実行し、Bicep検証を通じてインフラ変更を確認できる

**Independent Test**: GitHub Actionsの「Run workflow」から実行し、validateジョブが成功することを確認

### Implementation for User Story 1

- [x] T007 [US1] validateジョブを実装: checkout, Azure login, bicep build `.github/workflows/integration-test.yml`
- [x] T008 [US1] validateジョブにwhat-if分析を追加 `.github/workflows/integration-test.yml`
- [x] T009 [US1] validateジョブのタイムアウト設定（15分） `.github/workflows/integration-test.yml`
- [x] T010 [US1] validateジョブのログ構造化（::group::使用） `.github/workflows/integration-test.yml`

**Checkpoint**: validateジョブが単独で動作し、Bicepテンプレートの検証が可能

---

## Phase 4: User Story 2 - 統合テスト環境の自動プロビジョニング (Priority: P2)

**Goal**: 一時的なAzure環境を自動的にプロビジョニングし、テスト完了後にクリーンアップする

**Independent Test**: validateジョブ成功後、provision/deployジョブが実行され、cleanup後にリソースグループが削除されていることを確認

### Implementation for User Story 2

- [x] T011 [US2] provisionジョブを実装: Azure login, azd env new, azd provision `.github/workflows/integration-test.yml`
- [x] T012 [US2] provisionジョブの環境変数設定（inttest-{run_id}命名規則） `.github/workflows/integration-test.yml`
- [x] T013 [US2] provisionジョブのAKS SKUパラメータ対応 `.github/workflows/integration-test.yml`
- [x] T014 [US2] provisionジョブのタイムアウト設定（25分） `.github/workflows/integration-test.yml`
- [x] T015 [US2] deployジョブを実装: azd deploy `.github/workflows/integration-test.yml`
- [x] T016 [US2] deployジョブのタイムアウト設定（10分） `.github/workflows/integration-test.yml`
- [x] T017 [US2] cleanupジョブを実装: リソースグループ削除, azd env delete `.github/workflows/integration-test.yml`
- [x] T018 [US2] cleanupジョブのif条件設定（always()） `.github/workflows/integration-test.yml`
- [x] T019 [US2] cleanupジョブのタイムアウト設定（15分） `.github/workflows/integration-test.yml`
- [x] T020 [US2] provision/deploy/cleanupジョブのログ構造化 `.github/workflows/integration-test.yml`
- [x] T021 [US2] outputs設定でジョブ間のIngress URLを引き渡し `.github/workflows/integration-test.yml`

**Checkpoint**: プロビジョニング → デプロイ → クリーンアップの一連のフローが動作

---

## Phase 5: User Story 3 - エンドツーエンドの機能テスト (Priority: P2)

**Goal**: デプロイされたアプリケーションに対してHTTP統合テストを実行し、プラットフォーム連携を確認

**Independent Test**: testジョブが/healthとRedis連携エンドポイントを正常にテストできることを確認

### Implementation for User Story 3

- [x] T022 [US3] testジョブの基本構造を実装 `.github/workflows/integration-test.yml`
- [x] T023 [US3] curlによるスモークテスト（/health）を追加 `.github/workflows/integration-test.yml`
- [x] T024 [US3] プラットフォーム統合テストスクリプトを作成 `src/tests/integration/test_platform.py`
- [x] T025 [US3] testジョブからpytestを実行 `.github/workflows/integration-test.yml`
- [x] T026 [US3] testジョブのタイムアウト設定（10分） `.github/workflows/integration-test.yml`

**Checkpoint**: HTTP統合テストが実行され、アプリケーションとプラットフォームの連携が検証可能

---

## Phase 6: User Story 4 - テスト結果のレポートと通知 (Priority: P3)

**Goal**: テスト結果がGitHub Actions上で明確に表示され、失敗時の原因特定が容易になる

**Independent Test**: テスト失敗時にGitHub Step Summaryでエラー概要が表示されることを確認

### Implementation for User Story 4

- [x] T027 [US4] 各ジョブに$GITHUB_STEP_SUMMARY出力を追加 `.github/workflows/integration-test.yml`
- [x] T028 [US4] 失敗時のエラーサマリー出力を実装 `.github/workflows/integration-test.yml`
- [x] T029 [US4] 成功時のテスト結果サマリー出力を実装 `.github/workflows/integration-test.yml`

**Checkpoint**: テスト結果がGitHub UIで明確に確認でき、失敗原因が5分以内に特定可能

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 複数のユーザーストーリーに影響する改善

- [x] T030 [P] ドキュメント更新: 統合テストパイプラインの説明を追加 `docs/deployment.md`
- [x] T031 [P] quickstart.md検証: 手順に従ってパイプラインを実行 `specs/003-platform-integration-test/quickstart.md`
- [x] T032 全体タイムアウト設定の確認（60分） `.github/workflows/integration-test.yml`
- [x] T033 エッジケース対応: キャンセル時のクリーンアップ確認

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存関係なし - 即座に開始可能
- **Foundational (Phase 2)**: Setupの完了が必要 - すべてのユーザーストーリーをブロック
- **User Stories (Phase 3-6)**: Foundationalフェーズの完了が必要
  - US1 → US2 → US3 → US4 の順序で実装（パイプラインの流れに沿う）
- **Polish (Phase 7)**: すべてのユーザーストーリーが完了後

### User Story Dependencies

- **User Story 1 (P1)**: Foundational完了後に開始可能 - 他のストーリーに依存なし
- **User Story 2 (P2)**: US1のvalidateジョブが必要 - provisionはvalidate成功後に実行
- **User Story 3 (P2)**: US2のdeploy完了が必要 - testはアプリデプロイ後に実行
- **User Story 4 (P3)**: US1-3のジョブ構造が必要 - レポート機能を追加

### Within Each User Story

- ジョブの基本構造を先に実装
- 次にタイムアウトとログ構造化を追加
- 最後に細かい設定を調整

### Parallel Opportunities

- T002 はドキュメント作業のため、他と並列可能
- T004, T005 は独立した設定のため並列可能
- T030, T031 はPolishフェーズで並列可能

---

## Parallel Example: Foundational Phase

```bash
# Foundationalフェーズで並列実行可能なタスク:
Task: "T004 concurrency設定を追加"
Task: "T005 OIDC認証のpermissionsを設定"

# 上記完了後:
Task: "T006 ジョブ間の依存関係構造を定義"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup完了
2. Phase 2: Foundational完了（CRITICAL）
3. Phase 3: User Story 1完了
4. **STOP and VALIDATE**: validateジョブが正常に動作することを確認
5. MVP達成: Bicep検証が手動実行可能

### Incremental Delivery

1. Setup + Foundational → 基盤完成
2. User Story 1追加 → Bicep検証可能 → **MVP!**
3. User Story 2追加 → プロビジョニング・クリーンアップ可能
4. User Story 3追加 → 統合テスト実行可能
5. User Story 4追加 → レポート機能完成
6. 各ストーリーが独立してテスト可能

---

## Post-Implementation Updates

以下はユーザーフィードバックに基づく追加更新:

- [x] T034 [US1] GitHub Environment `integration-test` を使用するようワークフローを更新 `.github/workflows/integration-test.yml`
- [x] T035 [US1] `secrets.*` から `vars.*` への認証情報参照を変更 `.github/workflows/integration-test.yml`
- [x] T036 `azd pipeline config` を使用したセットアップ手順をドキュメント化 `docs/deployment.md`

---

## Notes

- [P] タスク = 異なるファイル、依存関係なし
- [Story] ラベルでタスクを特定のユーザーストーリーにマッピング
- 各ジョブのタイムアウト設定は必須（SC-002対応）
- ログ構造化は::group::を使用（SC-005対応）
- クリーンアップはalways()で必ず実行（SC-003対応）
- 論理グループごとにコミット
- GitHub Environment を使用することでブランチ名に依存しないOIDC認証が可能
