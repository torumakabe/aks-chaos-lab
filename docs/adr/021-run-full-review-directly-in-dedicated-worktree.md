# ADR-021: full レビューを専用 worktree の既存 task で直接実行する

## Status

Accepted

- Date: 2026-09-12
- Amends: [ADR-017](017-approved-index-conversion-for-managed-environments.md) Decision 4 の `review-repo-full` 専用隔離コピーと準備済み環境の process 間引渡し
- Amended by [ADR-024](024-explicit-python-registry-sync.md): レビュー task ごとの approved-index 同期と process 間 lock を廃止し、レビュー前の明示 `sync-dev` と task の `--no-sync` を適用する。

## Context

ADR-017 は `review-repo-full` のために、元 worktree からレビュー専用の隔離コピーを作り、snapshot と fingerprint で内容を固定し、token 付き一時 workspace 内で別 process が準備した Python 環境を後続 QA へ引き渡す例外を認めた。

Copilot の専用 worktree または利用者が明示的に用意した作業用 worktree をレビューの実行境界にすれば、task 内部で同じ目的のコピーと状態引渡しを重ねる必要はない。独自の隔離・同一性確認・一時 credential 引渡しは運用経路を増やし、通常の task が持つ approved-index 同期契約とは別のレビュー専用契約を維持させる。

## Decision

1. `review-repo-full` は Copilot の専用 worktree、または利用者が明示的に用意した作業用 worktree で実行する。task 内部では別の snapshot、一時 Git repository、fingerprint、token 付き一時 workspace を作成しない。
2. full レビューは既存の検査 task を対象 worktree で直接実行する。レビュー専用の `prepare-review-python-env` process と、準備済み仮想環境を後続 process へ引き渡す仕組みは設けない。
3. **ADR-024 で amend**: レビュー前に明示 `sync-dev` を実行し、各 Python task は準備済み環境を `--no-sync` で使う。task process ごとの同期は行わない。
4. **ADR-024 で amend**: 限定 lock 修復を廃止する。public lock、bit-identical artifact、設定と取得元の検査、Docker build、API 成果物の受け渡しに関する他の判断は変更しない。

レビュー専用の隔離コピーを維持する案は採用しない。専用 worktree と二重の隔離境界になり、既存 task とは異なる準備・credential・状態引渡しの契約を残すためである。利用者の通常作業中の worktree で暗黙に full レビューを実行する案も、レビューによる環境再構築と作業状態を分離できないため採用しない。

## Consequences

- review 専用の snapshot、fingerprint、一時 workspace token、準備 process と環境引渡しを保守しなくてよくなり、full レビューは通常の task と同じ同期処理を使う。
- Python task process は明示同期で準備した環境を `--no-sync` で利用する。同期はレビュー task ごとには行わない。
- full レビューの分離は task が内部生成するコピーではなく、呼び出し側が選ぶ専用または明示的な作業用 worktree に依存する。
