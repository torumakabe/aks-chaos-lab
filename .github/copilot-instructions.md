# AKS Chaos Lab — AI コンテキスト

## 概要

AKS 上の Chaos Engineering ラボ環境。azd でインフラとアプリを一括管理する。

## 技術スタック

- **アプリ**: Python 3.13 + FastAPI + uvicorn (`src/`)
- **IaC**: Bicep, subscription scope (`infra/`)
- **K8s**: Kustomize (`k8s/`)
- **依存**: Redis (Managed, Entra ID auth), Application Insights, Prometheus
- **パッケージ管理**: uv (`python`/`pip` 直接実行禁止)
- **品質検証**: ruff + ty + pytest → `cd src && make qa`
- **Bicep 検証**: `az bicep build --file infra/main.bicep`

## プロジェクト構造

```
src/app/          # FastAPI アプリ (main.py, config.py, models.py, redis_client.py)
infra/modules/    # Bicep モジュール (aks, network, redis, acr, identity, chaos 等)
k8s/base/         # Kustomize ベースマニフェスト
k8s/chaos/        # Chaos Mesh 実験
tests/unit/       # ユニットテスト
tests/load/       # Locust 負荷テスト (smoke, baseline, stress, spike)
docs/             # ガイド、フィーチャーコンテキストドキュメント
```

## 参考リソース

- [Reducing Friction for AI-Assisted Development](https://martinfowler.com/articles/reduce-friction-ai/) — 本リポジトリのワークフロー（Knowledge Priming, Design-First, Context Anchoring）の元となった記事
- [Azure Chaos Studio ドキュメント](https://learn.microsoft.com/azure/chaos-studio/)
- [AKS ベストプラクティス](https://learn.microsoft.com/azure/aks/best-practices)
- [Azure CAF リソース命名規則](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming)
- README.md — プロジェクトの全体像と `azd up` による構築手順

## コードパターンの参照先

- FastAPI エンドポイント → `src/app/main.py`
- Bicep モジュール → `infra/modules/`
- K8s マニフェスト → `k8s/base/deployment.yaml`

## 品質ゲート（必須）

- **Python**: ty エラー 0 件、ruff 警告 0 件、pytest 全テスト合格
- **Bicep**: `az bicep build` エラー 0 件

## ワークフロー

- セッション開始時、現在のブランチに関連するフィーチャーコンテキストドキュメントが `docs/features/` にあれば読み込む
- セッションを閉じる際に「このまま閉じたらコンテキストが失われる」と感じたら、
  フィーチャーコンテキストドキュメントに決定事項・理由・制約・進捗を記録する
  (`docs/feature-context-guide.md` を参照)
- 複雑な機能は実装前に段階的な設計会話を行う（要件確認 → コンポーネント設計 → データフロー → インターフェース定義 → 実装）。コードは設計合意後
- ユーザーから見える振る舞いが変わる変更（機能追加、API 変更、デプロイ手順変更、アーキテクチャ変更）では README.md の更新要否を確認し、必要なら同一 PR 内で更新する
- ドキュメントは `docs/` に配置。一時ファイルは `tmp/` に配置し、完了後に削除

