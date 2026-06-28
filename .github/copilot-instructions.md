# AKS Chaos Lab — AI コンテキスト

## 概要

AKS 上の Chaos Engineering ラボ環境。azd でインフラとアプリを一括管理する。

## 技術スタック

- **アプリ**: Python 3.14 + FastAPI + uvicorn (`src/`)
- **IaC**: Bicep, subscription scope (`infra/`)
- **K8s**: Kustomize (`k8s/`)
- **依存**: Redis (Managed, Entra ID auth), Application Insights, Prometheus
- **パッケージ管理**: uv workspace (ルート `pyproject.toml` + `uv.lock`、`python`/`pip` 直接実行禁止)
- **品質検証**: ruff + ty + pytest → クリーン環境では `uv run scripts/tasks.py sync-dev` 後に `uv run scripts/tasks.py qa-app`
- **Bicep 検証**: `az bicep build --file infra/main.bicep`
- **編集時自動検証**: `.github/hooks/hooks.json` に postToolUse hook を登録。`.py` 編集で workspace ruff (check --fix + format)、`.bicep` 編集で `az bicep format` + `build` を実行し、変更/失敗時はエージェントへ `additionalContext` でフィードバックする

## プロジェクト構造

```
pyproject.toml    # uv workspace ルート (ruff/ty 共通設定、開発用依存)
uv.lock           # workspace 共通ロック
src/api/          # FastAPI アプリ (uv member: aks-chaos-lab-api)
src/api/app/      # main.py, config.py, models.py, redis_client.py 等
src/api/Dockerfile # build context = repo root, `uv sync --package aks-chaos-lab-api`
src/external-sli-publisher/  # Azure Functions publisher (uv member, デプロイは requirements.txt)
infra/modules/    # Bicep モジュール (aks, network, redis, acr, identity, chaos 等)
k8s/apps/chaos-app/ # chaos-app Kustomize マニフェスト
k8s/chaos/        # Chaos Mesh 実験
docs/deployment.md # 構築・削除・検証手順
docs/observability.md # 可観測性の運用詳細
docs/chaos-experiments.md # Chaos 実験の実行ガイド
docs/adr/         # Architecture Decision Records
docs/features/    # Feature Document（作業途中の状態保存）
```

## 参考リソース

- [Reducing Friction for AI-Assisted Development](https://martinfowler.com/articles/reduce-friction-ai/) — 本リポジトリのワークフロー（Knowledge Priming, Design-First, Context Anchoring）の元となった記事
- [Azure Chaos Studio ドキュメント](https://learn.microsoft.com/azure/chaos-studio/)
- [AKS ベストプラクティス](https://learn.microsoft.com/azure/aks/best-practices)
- [Azure CAF リソース命名規則](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming)
- README.md — プロジェクトの全体像とドキュメント入口
- docs/deployment.md — `azd up/down`、権限、feature flag、ローカル開発、負荷テスト
- docs/observability.md — 観測シグナル、SLI、アラート、OTLP logs

## コードパターンの参照先

- FastAPI エンドポイント → `src/api/app/main.py`
- Bicep モジュール → `infra/modules/`
- K8s マニフェスト → `k8s/apps/chaos-app/deployment.yaml`

## 品質ゲート（必須）

- **Python**: ty エラー 0 件、ruff 警告 0 件、pytest 全テスト合格
- **Bicep**: `az bicep build` エラー 0 件

## Windows / cross-shell 注意

- Windows で Locust 負荷テストを実行する場合は、`uv run locust ...` を直接使わず `uv run scripts/tasks.py load-*` を使う。wrapper が child process に `PYTHONUTF8=1` を設定し、cp932 などの既定 encoding による TOML 読み取り失敗を避ける。
- Locust CSV 出力は `LOCUST_CSV_PREFIX` と、必要な場合のみ `LOCUST_CSV_FULL_HISTORY=true` で指定する。任意引数を広く通す `LOCUST_EXTRA_ARGS` のような仕組みは追加しない。
- WSL / bash helper に Windows path を渡す場合は、`C:\...` ではなく `/mnt/c/...` 形式へ変換する。PowerShell / Windows native shell では `C:\...` 形式を使う。

## 知識ソース

コード生成・技術選定・構成変更の際は、以下を確認すること:

- `docs/adr/` — 確定済みの設計判断。新規の判断が矛盾しないよう確認する
- `docs/features/` — 作業中の Feature Document。決定事項・制約を尊重する
- `docs/deployment.md` — 構築・削除・検証手順
- `docs/observability.md` — 可観測性の運用詳細
- `infra/` — 現在のインフラ構成（Bicep）
- `src/` — 現在のアプリケーションコード
- `k8s/` — 現在の Kubernetes マニフェスト

## ワークフロー

- セッション開始時、`docs/features/` に関連する Feature Document があれば `resume` エージェントで作業を再開する
- セッションの区切りでは `wrap-up` エージェントで ADR 候補の洗い出し・Feature Document の要否判断・リトマステストを行う
- 複雑な機能は実装前に段階的な設計会話を行う（要件確認 → コンポーネント設計 → データフロー → インターフェース定義 → 実装）。コードは設計合意後
- ユーザーから見える振る舞いが変わる変更（機能追加、API 変更、デプロイ手順変更、アーキテクチャ変更）では README.md または該当する `docs/*.md` の更新要否を確認し、必要なら同一 PR 内で更新する
- ドキュメントは `docs/` に配置。一時ファイルは `tmp/` に配置し、完了後に削除

## 事実検証

ドキュメントが「ADR-NNN で決定済み」「既存の〇〇」等とリポジトリ内の成果物の存在を主張している場合、実際のファイルで検証すること:

- ADR → `docs/adr/INDEX.md` および `docs/adr/`
- Feature Document → `docs/features/`
- IaC → `infra/`
- アプリケーションコード → `src/`
- Kubernetes マニフェスト → `k8s/`
