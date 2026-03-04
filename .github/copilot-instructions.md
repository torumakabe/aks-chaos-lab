# 応答のスタイル

- 応答は日本語を使う。

# プロジェクト概要

AKS (Azure Kubernetes Service) 上で Chaos Engineering を実践するためのラボ環境。Azure Developer CLI (azd) でインフラとアプリを一括管理する。

- **アプリ**: Python 3.13 + FastAPI (`src/`)
- **IaC**: Bicep (`infra/`)
- **K8s マニフェスト**: Kustomize (`k8s/`)
- **パッケージ管理**: uv
- **型チェッカー**: ty (Astral)
- **リンター/フォーマッター**: ruff
- **テスト**: pytest

# ファイル管理とプロジェクト構造

- ルートフォルダは必要最低限のファイル数に保ち、プロジェクト構造を明確に保つ。
- README ではないドキュメントは、`docs`ディレクトリを作成し、その中に入れる。ドキュメントの形式は Markdown とする。
- 一時的なテストやデバッグ用のファイルを作成する場合は、ルートフォルダではなく`tmp`ディレクトリを作成し、その中で実行する。
- 一時的ではない、その後も継続的にテストに使うべきファイルは、`tests`ディレクトリを作成し、その中に入れる。
- 調査や検証が完了したら、不要になった一時ファイルは削除する。

# Git ブランチ管理

- **main ブランチに直接コミットしてはならない**。必ずフィーチャーブランチを作成し、PR 経由でマージする。
- ブランチ命名例:
  - 機能追加: `feature/<機能名>` または Spec Kit の `<番号>-<短縮名>`（例: `005-aks-nap-mode`）
  - バグ修正: `fix/<修正内容>`
  - メンテナンス: `chore/<内容>`

# 破壊的操作の確認

- `git reset`, `git push --force`, ブランチ削除、`azd down` など**取り消しが困難な操作**は、実行前にユーザーの明示的な許可を得ること。
- ユーザーが「できるか」「可能か」と質問した場合は、**回答のみ行い、実行はしない**。実行の指示があるまで待つこと。

# Python 開発

- Python の実行には必ず `uv run` を使う（`python` を直接呼ばない）。
- パッケージ追加は `uv add`、同期は `uv sync`。

## 開発コマンド（`src/` ディレクトリ内で実行）

```bash
# 品質検証（リント + テスト + 型チェック）を一括実行
cd src && make qa

# 個別実行
make test          # ユニットテスト
make lint          # ruff リント（自動修正あり）
make typecheck     # ty 型チェック
make format        # ruff フォーマット
make format-check  # フォーマット確認（変更なし）
```

## Bicep 検証

```bash
az bicep build --file infra/main.bicep
```

# ワークフロー

- 機能開発は Spec Kit ワークフローに従う（/speckit.specify, /speckit.plan, /speckit.tasks, /speckit.implement）
- 以下のような変更は Spec Kit ワークフローを経由せず直接実施する：
  - API バージョンの更新（Azure API、Kubernetes API など）
  - 依存パッケージのバージョン更新（セキュリティパッチ、バグ修正）
  - 軽微なドキュメント修正（誤字、リンク修正）
- 詳細なルールは `.specify/memory/constitution.md` を参照
