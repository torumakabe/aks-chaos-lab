# 応答のスタイル

- 応答は日本語を使う。

# ファイル管理とプロジェクト構造

- ルートフォルダは必要最低限のファイル数に保ち、プロジェクト構造を明確に保つ。
- README ではないドキュメントは、`docs`ディレクトリを作成し、その中に入れる。ドキュメントの形式は Markdown とする。
- 一時的なテストやデバッグ用のファイルを作成する場合は、ルートフォルダではなく`tmp`ディレクトリを作成し、その中で実行する。
- 一時的ではない、その後も継続的にテストに使うべきファイルは、`tests`ディレクトリを作成し、その中に入れる。
- 調査や検証が完了したら、不要になった一時ファイルは削除する。

# ワークフロー

- 機能開発は Spec Kit ワークフローに従う（/speckit.specify, /speckit.plan, /speckit.tasks, /speckit.implement）
- 以下のような変更は Spec Kit ワークフローを経由せず直接実施する：
  - API バージョンの更新（Azure API、Kubernetes API など）
  - 依存パッケージのバージョン更新（セキュリティパッチ、バグ修正）
  - 軽微なドキュメント修正（誤字、リンク修正）
- 詳細なルールは `.specify/memory/constitution.md` を参照

## Active Technologies
- Python 3.13 + uv (パッケージマネージャー)、FastAPI、uvicorn (002-uv-consolidation)
- YAML (GitHub Actions) + Bash (テストスクリプト) + Azure CLI, Azure Developer CLI (azd), Bicep CLI, curl (003-platform-integration-test)
- N/A（一時的なAzureリソース） (003-platform-integration-test)

## Recent Changes
- 002-uv-consolidation: Added Python 3.13 + uv (パッケージマネージャー)、FastAPI、uvicorn
