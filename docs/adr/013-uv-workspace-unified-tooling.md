# ADR-013: uv workspace でツーリングを統一しつつデプロイ単位は分離維持

- Status: Accepted
- Date: 2026-05-21

## Context

リポジトリには 3 つの Python ルート (`src/api/` FastAPI コンテナ、`src/external-sli-publisher/` Azure Functions、`scripts/` 運用スクリプト) があり、`pyproject.toml` の `[tool.ruff]` / `[tool.ty.*]` / `[tool.pytest.ini_options]` がほぼ重複している。`scripts/tasks.py` の lint/format/typecheck/test は api/publisher で 2 系統並走し、`scripts/` は `src/api` の設定を `--config pyproject.toml` で間借りしている。post-edit hook (`.github/hooks/scripts/post-edit-quality-feedback.py`) もパスごとのルーティングが必要で複雑化している。一方、コンテナと Functions のデプロイ単位独立性は ADR-009/012 で維持する方針。

## Decision

uv workspace を採用し、ツーリング層 (ruff/ty/pytest, dev 依存) を統一する。デプロイ成果物は引き続き分離する。

1. ルート `pyproject.toml` を新設し `[tool.uv.workspace] members = ["src/api", "src/external-sli-publisher"]`
2. `[tool.ruff]` / `[tool.ty.*]` をルートに集約、サブパッケージ側は削除。`[tool.pytest.ini_options]` は API/Publisher で env や coverage 設定が異なるためサブパッケージに残し、テスト実行時のみ各 cwd から `uv run pytest` を呼ぶ
3. `uv.lock` をルート 1 本化、サブパッケージの `uv.lock` は削除
4. `src/api/Dockerfile` の build context をリポジトリルートに変更し、`uv sync --package aks-chaos-lab-api --locked --no-install-project` で API 依存のみ取り出す
5. Functions 側は `requirements.txt` を正本とし、`target_check_publisher_requirements` で同期検査継続
6. `scripts/` は workspace dev 環境から ruff/ty/pytest を呼ぶだけにし、`--config` 間借りを廃止
7. CI の `version-file` をルート `pyproject.toml` に変更
8. `azure.yaml` の `project:` パスは維持 (azd の service 単位は不変)
9. post-edit hook のパス振り分けを撤去し、ルートから `uv run ruff check <abs_path>` を呼ぶだけにする (lock の自動同期を許すため `--frozen` は付けない)

## Consequences

- 利点: ツーリング設定の単一化、`scripts/` の間借り解消、hook 単純化、dev 体験向上
- リスク/コスト: Dockerfile 再構成と CI/hook 修正が必要。Functions の `requirements.txt` と workspace 依存の二重管理は残る (検査で担保)
- 検証: `uv run scripts/tasks.py qa` 全合格、`docker build -f src/api/Dockerfile .` 成功、`target_check_publisher_requirements` 合格、`azd package api` / `azd package external-sli-publisher` 成功

## 採用しなかった代替案

- **完全モノパッケージ化**: コンテナ/Functions のデプロイ最適化を阻害しコールドスタートに悪影響。ADR-009/012 と非整合
- **現状維持**: 設定重複・間借り・hook ルーティングの負債が残存

## 関連 ADR

- ADR-006 (OTel 依存ピン留め) — API 依存ツリーは workspace 化後も保持
- ADR-009 / ADR-012 — Functions / Azure Monitor SLI のデプロイ単位独立性を維持
