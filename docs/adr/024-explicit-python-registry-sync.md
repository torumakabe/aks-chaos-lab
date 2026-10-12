# ADR-024: Python package registry の選択と同期を明示操作にする

## Status

Accepted

- Date: 2026-10-12
- Amends: [ADR-017](017-approved-index-conversion-for-managed-environments.md) の workspace 同期と lock 修復に関する判断

## Context

本リポジトリは public GitHub repository であり、public PyPI を使える利用者を主な対象とする。一方、public package registry へ直接接続できず、組織承認済みの private index が必要な利用者も支援する。

uv lockfile は registry と artifact URL を記録するため、同じ lock を異なる registry から同期するには、private index 用の hash 固定 requirements 変換が必要になる。従来は public 環境で uv の暗黙同期を使う一方、private 環境では task process ごとに仮想環境を再構築し、process lock、認証情報の引渡し、限定的な lock 修復を行っていた。registry の差に加えて同期の責務と状態が task runner、レビュー処理、lock validator に分散していた。

## Decision

1. public PyPI を既定の registry とする。user-level uv 設定に index がなければ public PyPI を選ぶ。設定する場合は、public PyPI の明示設定か、HTTPS の named index 一つだけに `default = true` を指定する。private index を選ぶ場合は、user-level 設定で明示する。
2. 接続可否から registry を推測しない。TOML の解析失敗、明示した `UV_CONFIG_FILE` の欠落、複数 index、不正 URL、`default = true` の欠落、取得元を変える禁止環境変数や設定があれば、同期前に停止する。public と private のどちらにも自動 fallback しない。
3. `sync` と `sync-dev` を public/private 共通の明示同期入口とする。`sync` は全 workspace package の runtime 依存を、`sync-dev` は全 dependency group を同期する。CI も workspace task の前に `sync-dev` を実行する。
4. public 同期は user-level 設定の影響を受けないよう `uv sync --locked --no-config` を使い、lock を変更しない。失敗時は public が選ばれていたこと、private へ自動切替していないこと、組織承認済み index の設定方法を固定文言で案内して非ゼロ終了する。
5. private 同期に限り、public lock を読み取り専用で検査し、frozen export から一時 requirements を作成して `uv pip sync --require-hashes` を実行する。private index の設定は `default = true` で public fallback を無効にする。private cache、仮想環境の再作成、workspace `.pth` はこの同期処理に集約する。同期前後で `uv.lock` の hash が変わった場合は失敗する。
6. 認証情報は、選択した index の username/password 環境変数から private install process にだけ渡す。requirements、lockfile、ログには含めず、private sync の失敗出力は URL と認証情報を含まない固定文言にする。認証失敗や artifact の欠落はそのまま失敗とし、public へ fallback しない。
7. すべての workspace task は `uv run --no-sync` で既存 `.venv` を使う。task、レビュー、post-edit hook は依存同期や lock 修復を行わない。同期の後に task を実行し、同じ worktree 内で同期と task を並行実行しない。
8. public lock validator は読み取り専用とし、限定 lock 修復を廃止する。不正な lock は変更せず停止する。lock の更新には public PyPI を使う既存 workflow を利用する。
9. [ADR-017](017-approved-index-conversion-for-managed-environments.md) の Docker build における private index 対応、BuildKit secret / cache 分離、API image の明示的な受け渡しは変更しない。API container の runtime 依存同期も対象外とする。

## Consequences

- 利用者は registry を判断して異なる task を選ばず、同じ `sync` / `sync-dev` を実行する。設定なしでは public PyPI を使い、到達できない場合は明示的に失敗する。
- task ごとの private 再同期、同期 process lock、準備済み環境の状態管理、レビュー専用の credential 引渡し、Git HEAD に基づく lock 修復を削除する。
- private 同期には uv の lock URL 固定を補う一時 requirements adapter が残る。uv が同じ public lock を両方の index で使い、hash 検証、private-only 通信、fallback 禁止、認証情報の秘匿、全対応 platform の artifact 維持を採用 version で実証できた場合、この adapter と専用 cache / venv / `.pth` 処理を後続変更で削除できる。
- 実際の private registry の認証と artifact の配布状態はローカルテストだけでは保証しない。private index が public lock と同じ hash の artifact を提供できなければ同期は失敗する。

## 採用しなかった代替案

- **public/private ごとに利用者が異なる task を選ぶ**: 操作を重複させ、利用者ごとに選択判断を要求するため採用しない。
- **接続失敗後に別 registry を試す**: 利用方針と異なる registry への通信を引き起こし得るため採用しない。
- **task ごとの自動同期と process lock を維持する**: 明示同期を使う方式に対して状態と実行経路を増やすため採用しない。
- **不正 lock の自動修復**: lock の差分を推測して書き換える複雑さを残すため採用しない。検査に失敗した lock は利用者が更新手順で置き換える。
