---
name: design-snapshot
description: 現在の IaC と ADR から設計書スナップショットを生成する。「設計書を作成」「design snapshot」と言われたら使う。
---

現在のインフラ構成と設計判断から設計書スナップショットを生成する。

## 入力

以下を読み取る:
- `infra/main.bicep` — 現在のインフラ構成
- `infra/main.bicepparam` — パラメータ設定
- `k8s/base/` — Kubernetes マニフェスト（Kustomize ベース）
- `docs/adr/` — 確定済みの設計判断
- `.github/copilot-instructions.md` — プロジェクト概要と規約

## 出力

`docs/design-infrastructure.md` に設計書を生成する。既存ファイルがあれば上書きする。

## 構成

1. 概要（設計方針、スコープ）
2. コンポーネント構成（IaC から抽出）
3. 主要な設計判断の要約（ADR から抽出、詳細は ADR への参照）
4. ネットワーク構成
5. Kubernetes ワークロード構成（マニフェストから抽出）
6. 認証・セキュリティ
7. 運用・監視

設計書は**スナップショット**であり、次に生成し直せば更新される。手動で維持する必要はない。
