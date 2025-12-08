# Feature Specification: Spec Kit移行

**Feature Branch**: `001-speckit-migration`  
**Created**: 2025-12-08  
**Status**: Draft  
**Input**: User description: "現在の独自の仕様駆動開発ワークフローからSpec Kitに移行する"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Spec Kit ワークフローへの完全移行 (Priority: P1)

開発チームメンバーとして、現在の独自の仕様駆動開発ワークフロー（spec-driven-workflow-v1.md）からSpec Kitの標準化されたワークフローに移行したい。これにより、仕様作成・計画・実装のプロセスが統一され、効率的な開発が可能になる。

**Why this priority**: Spec Kitへの移行は、チーム全体の開発プロセスの基盤となる変更であり、他のすべての機能開発に先立って完了する必要がある。

**Independent Test**: `/speckit.specify`、`/speckit.plan`、`/speckit.tasks`、`/speckit.implement` コマンドを使用して、新しい機能の仕様から実装までのフローが正常に動作することを確認する。

**Acceptance Scenarios**:

1. **Given** Spec Kitがインストールされている状態, **When** `/speckit.specify` コマンドを実行する, **Then** 新しい機能ブランチとスペックファイルが作成される
2. **Given** 仕様が作成された状態, **When** `/speckit.plan` コマンドを実行する, **Then** 実装計画が生成される
3. **Given** 計画が作成された状態, **When** `/speckit.tasks` でタスク分解し `/speckit.implement` コマンドを実行する, **Then** 計画に基づいた実装が行われる

---

### User Story 2 - 旧ワークフローからの円滑な移行 (Priority: P2)

開発チームメンバーとして、既存の仕様駆動ワークフローで確立された良い習慣（ドキュメント管理、品質チェック、決定記録など）がSpec Kitの新しいワークフローでも維持されることを確認したい。

**Why this priority**: 既存のベストプラクティスを失わないことは重要だが、まずは基本的な移行が完了することが前提となる。

**Independent Test**: 既存の `/docs/` 構造とドキュメント管理方針がSpec Kit移行後も機能することを確認する。

**Acceptance Scenarios**:

1. **Given** Spec Kitに移行した状態, **When** 新しい機能を開発する, **Then** `/docs/` ディレクトリの構造とドキュメントが適切に管理される
2. **Given** Spec Kitに移行した状態, **When** 品質チェック（型チェック、リント、テスト）を実行する, **Then** 従来と同じ品質基準が維持される

---

### User Story 3 - copilot-instructions.mdの更新 (Priority: P3)

開発チームメンバーとして、GitHub Copilotの指示ファイル（copilot-instructions.md）が新しいSpec Kitワークフローを正しく参照するように更新されることを望む。

**Why this priority**: 指示ファイルの更新は移行の最終段階として行うべき作業であり、コアな移行が完了してから実施する。

**Independent Test**: copilot-instructions.mdがSpec Kitの新しいワークフローを正しく参照していることを確認する。

**Acceptance Scenarios**:

1. **Given** Spec Kitへの移行が完了した状態, **When** copilot-instructions.mdを確認する, **Then** Spec Kitワークフローへの参照が正しく記載されている

---

### User Story 4 - 設定ファイルの役割整理 (Priority: P4)

開発チームメンバーとして、`copilot-instructions.md`と`constitution.md`の役割が明確に分離され、内容の重複がない状態を望む。

**Why this priority**: 設定ファイルの整理は移行の仕上げ作業であり、コアな移行とcopilot-instructions.mdの更新が完了してから実施する。

**Independent Test**: 両ファイルを確認し、重複がなく役割が明確に分離されていることを確認する。

**Acceptance Scenarios**:

1. **Given** Spec Kitへの移行が完了した状態, **When** copilot-instructions.mdを確認する, **Then** AIへの指示（応答スタイル、ファイル管理ルール、ワークフロー参照）のみが記載されている
2. **Given** Spec Kitへの移行が完了した状態, **When** constitution.mdを確認する, **Then** プロジェクトのコア原則（品質基準、ドキュメント管理、ガバナンス）が記載されている
3. **Given** 両ファイルが更新された状態, **When** 内容を比較する, **Then** 重複する記述がない

---

### User Story 5 - chatmodesディレクトリの廃止 (Priority: P5)

開発チームメンバーとして、`.github/chatmodes/`ディレクトリを廃止したい。理由は以下の通り：
- モデルの進化により、4.1-Beast.chatmode.mdのような特別な指示が不要になった
- VS Codeのchatmodesからagentsへの移行が進んでいる

**Why this priority**: 廃止作業は他の移行作業と独立しており、最終段階で実施できる。

**Independent Test**: `.github/chatmodes/`ディレクトリが削除され、関連する参照がないことを確認する。

**Acceptance Scenarios**:

1. **Given** Spec Kitへの移行が完了した状態, **When** `.github/chatmodes/`ディレクトリを確認する, **Then** ディレクトリが存在しない
2. **Given** chatmodesディレクトリが削除された状態, **When** プロジェクト内を検索する, **Then** chatmodesへの参照がない

---

### Edge Cases

- 移行中に既存の進行中の機能開発がある場合、どのように扱うか？
  - 仮定: 進行中の作業がないクリーンな状態で移行を開始する
- 旧ワークフローのドキュメントを削除するか、アーカイブするか？
  - 仮定: 参照のため一時的にアーカイブし、移行完了後に削除を検討する

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `.specify/` ディレクトリ構造がプロジェクトルートに存在し、Spec Kitの標準構成に準拠すること
- **FR-002**: `/speckit.specify` コマンドが新しい機能ブランチとスペックファイルを正常に作成できること
- **FR-003**: `/speckit.plan` コマンドが仕様に基づいた実装計画を生成できること
- **FR-004**: `/speckit.tasks` と `/speckit.implement` コマンドが計画に基づいた実装を支援できること
- **FR-005**: `/docs/` ディレクトリの既存構造（requirements.md、design.md、api.md、deployment.md）が維持されること
- **FR-006**: 品質チェック（型チェック0エラー、リント0警告、全テスト合格）のプロセスが維持されること
- **FR-007**: `copilot-instructions.md` が新しいSpec Kitワークフローを参照するように更新されること
- **FR-008**: 旧ワークフローファイル（spec-driven-workflow-v1.md）を移行完了後に削除すること
- **FR-009**: `copilot-instructions.md`と`constitution.md`の役割を分離し、重複を排除すること
  - `copilot-instructions.md`: AIへの指示（応答スタイル、ファイル管理ルール、ワークフロー参照のみ）
  - `constitution.md`: プロジェクトのコア原則（品質基準、ドキュメント管理、ガバナンス）
- **FR-010**: `.github/chatmodes/`ディレクトリを削除すること（モデル進化によりchatmodesが不要になり、agentsへ移行が進んでいるため）

### Key Entities

- **Spec File**: 各機能の仕様を記述するMarkdownファイル（`specs/###-feature-name/spec.md`）
- **Plan File**: 実装計画を記述するファイル（`specs/###-feature-name/plan.md`）
- **Feature Branch**: 機能開発用のGitブランチ（`###-feature-name`形式）
- **Workflow Configuration**: Spec Kitの設定と指示ファイル（`.specify/` ディレクトリ内）

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Spec Kitの全コマンド（specify, plan, tasks, implement）が正常に動作する
- **SC-002**: 新しい機能の仕様作成から実装までのワークフローが30分以内に開始できる
- **SC-003**: 既存のドキュメント構造（/docs/）が100%維持される
- **SC-004**: 品質チェック基準（型チェック0エラー、リント0警告、全テスト合格）が維持される
- **SC-005**: チームメンバーが新しいワークフローを1時間以内に理解し使用開始できる

## Assumptions

- Spec Kitの `.specify/` ディレクトリ構造は既にプロジェクトに存在している
- 移行は進行中の機能開発がない状態で開始する
- 旧ワークフロー（spec-driven-workflow-v1.md）は移行完了後に削除する
- 既存の `/docs/` 構造は維持し、Spec Kitと並行して使用する

## Clarifications

### Session 2025-12-08

- Q: 旧ワークフローファイル（spec-driven-workflow-v1.md）の処理方針は？ → A: 移行完了後に削除する
- Q: 品質チェックの具体的な基準は？ → A: 厳格基準（型チェック0エラー、リント0警告、全テスト合格を必須）
- Q: copilot-instructions.mdとconstitution.mdの使い分けは？ → A: copilot-instructions.mdはAIへの指示のみ、constitution.mdはプロジェクトのコア原則を記載し、重複を排除する
- Q: chatmodesディレクトリの処理方針は？ → A: 削除する（モデル進化によりchatmodesが不要になり、agentsへ移行が進んでいるため）

- Q: update-agent-context.shのCOPILOT_FILEパス修正方針は？ → A: C-2を採用（`.github/agents/copilot-instructions.md` → `.github/copilot-instructions.md` に修正）。GitHub Copilotが認識する正しいパスを使用する。
