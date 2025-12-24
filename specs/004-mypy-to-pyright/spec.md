# Feature Specification: 型チェッカーをmypyからpyrightへ移行

**Feature Branch**: `004-mypy-to-pyright`  
**Created**: 2024-12-24  
**Status**: Draft  
**Input**: User description: "型チェックをmypyからpyrightに変更します"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 型チェックの実行 (Priority: P1)

開発者として、pyrightを使ってプロジェクトの型チェックを実行し、型に関する問題を発見できるようにしたい。

**Why this priority**: 型チェックは開発ワークフローの中核機能であり、この移行の主目的。

**Independent Test**: `make typecheck` コマンドを実行し、pyrightが正常に動作してエラーなしで完了することを確認。

**Acceptance Scenarios**:

1. **Given** pyrightがインストールされた環境、**When** `make typecheck`を実行する、**Then** pyrightが型チェックを実行し結果を表示する
2. **Given** 型エラーのないコード、**When** `make typecheck`を実行する、**Then** エラーなしで成功終了する
3. **Given** 型エラーのあるコード、**When** `make typecheck`を実行する、**Then** 適切なエラーメッセージと位置情報を表示する

---

### User Story 2 - QAワークフローの実行 (Priority: P1)

開発者として、既存のQAワークフロー（`make qa`、`make check`）が引き続き正常に動作することを確認したい。

**Why this priority**: 既存のワークフローが壊れないことは移行の必須条件。

**Independent Test**: `make qa` を実行し、すべてのチェック（フォーマット、lint、テスト、型チェック）が正常に完了することを確認。

**Acceptance Scenarios**:

1. **Given** 移行後の環境、**When** `make qa`を実行する、**Then** 型チェックを含むすべてのチェックが成功する
2. **Given** 移行後の環境、**When** `make check`を実行する、**Then** 型チェックを含むすべてのチェックが成功する

---

### User Story 3 - クリーンアップの実行 (Priority: P2)

開発者として、`make clean`でpyrightのキャッシュも含めてクリーンアップできるようにしたい。

**Why this priority**: 開発環境の一貫性を保つための補助機能。

**Independent Test**: `make clean` を実行し、pyrightキャッシュが削除されることを確認。

**Acceptance Scenarios**:

1. **Given** pyrightキャッシュが存在する、**When** `make clean`を実行する、**Then** pyrightキャッシュが削除される

---

### User Story 4 - CI/CDワークフローの実行 (Priority: P1)

開発者として、GitHub ActionsのCIワークフローがpyrightを使用して型チェックを実行するようにしたい。

**Why this priority**: CIでの型チェックはコード品質を保証するための重要な自動化であり、ローカル環境との一貫性が必須。

**Independent Test**: GitHub Actionsでpushまたはpull_request時にpyrightによる型チェックが成功することを確認。

**Acceptance Scenarios**:

1. **Given** CIワークフローが更新された環境、**When** pushまたはPRを作成する、**Then** pyrightによる型チェックがCIで実行される
2. **Given** 型エラーのないコード、**When** CIが実行される、**Then** typecheckジョブが成功する

---

### Edge Cases

- mypyの設定が残っている場合、混乱を避けるため削除または無効化する
- pyproject.tomlに両方の型チェッカー設定がある場合、pyrightのみを有効にする

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: プロジェクトはpyrightを型チェッカーとして使用しなければならない
- **FR-002**: `make typecheck`コマンドはpyrightを実行しなければならない
- **FR-003**: `make qa`および`make check`コマンドは引き続き正常に動作しなければならない
- **FR-004**: `make clean`はpyrightのキャッシュを削除しなければならない
- **FR-005**: pyproject.tomlにpyrightの設定を追加しなければならない
- **FR-006**: mypy関連の設定と依存関係を削除しなければならない
- **FR-007**: GitHub Actions CIワークフローのtypecheckジョブはpyrightを使用しなければならない
- **FR-008**: 現在の全Pythonコードがpyrightの型チェックをエラーなしでパスしなければならない

## Assumptions

- pyrightはuvで管理される開発依存関係として追加する
- 既存のmypy設定（`[tool.mypy]`）は削除し、同等の設定をpyrightで再現する
- pyrightの設定は`pyproject.toml`内の`[tool.pyright]`セクションで管理する

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `make typecheck`がpyrightを使用して成功終了する
- **SC-002**: `make qa`がすべてのチェック（フォーマット、lint、テスト、型チェック）で成功終了する
- **SC-003**: 現在のすべてのPythonコードがpyrightの型チェックをエラーなしでパスする
- **SC-004**: GitHub Actions CIワークフローのtypecheckジョブがpyrightを使用して成功する
- **SC-005**: mypyへの依存関係が完全に削除される
