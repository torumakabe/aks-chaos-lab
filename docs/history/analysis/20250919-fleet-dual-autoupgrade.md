### 分析 - Fleet 自動アップグレードを制御プレーン用とノードイメージ用に分割 - 2025-09-19T00:00:00Z
**目的**: Fleet Manager でクラスタ全体の自動アップグレードとノード OS 自動アップグレードを同時に有効化するため、別々の autoUpgradeProfiles を用意する要件を整理する。
**コンテキスト**: Microsoft のガイド「Interactions between node OS auto-upgrade and cluster auto-upgrade」で両者の併用が推奨されている。現在のテンプレートは `channel=Stable` の単一プロファイルのみ。
**決定**: Control Plane (Stable) と Node Image (NodeImage) の2つの autoUpgradeProfiles を同一フリートに作成し、共通の更新戦略（承認ゲート付き）を参照する。
**実行**:
- ドキュメントを確認し、クラスタ auto-upgrade とノード OS auto-upgrade の併用が推奨される点を整理。
- 既存 Bicep (`infra/modules/fleet.bicep`) で autoUpgradeProfiles が単一リソースであることを確認。
- 競合リスク: 両プロファイルが同じメンバーに Update Run を発火するため、承認ゲートで人手による調整が必要。ただしガイドライン上併用がベストプラクティス。
**出力**:
- 必要な変更点リスト
  - Stable 用 autoUpgradeProfile の名称変更（例: `default-controlplane-auto-upgrade`）
  - NodeImage 用 autoUpgradeProfile の追加（例: `default-nodeimage-auto-upgrade`）
  - どちらも `updateStrategyId` を既存戦略に設定し、Manual Gate を共有
  - NodeImage プロファイルにも `nodeImageSelection.type=Latest` を設定
  - Scheduled Query Rule の dependsOn を両プロファイルに更新
  - README/Docs に両プロファイルの存在と目的を記載
- リスク/懸念
  - Update Run の頻度増により承認作業やノード再起動が増える可能性
  - 追加の autoUpgradeProfile を無効化する場合の手順を今後ドキュメントに明記すべき
**信頼度**: 0.85（Bicep設計の影響範囲が明確であり、残る不確定要素は運用時の頻度のみ）。
**次**: 設計フェーズで具体的なリソース構成と命名、ドキュメント更新内容を定義する。
