### 実装 - Fleet 自動アップグレードを制御プレーン/ノードイメージで分割 - 2025-09-19T00:00:00Z
**目的**: Control Plane 用（Stable）と Node Image 用（NodeImage）の autoUpgradeProfiles をテンプレートに追加し、Manual Gate を共有させる。
**コンテキスト**: 設計で Dual autoUpgradeProfile 方針を決定済み。
**実行**:
- `infra/modules/fleet.bicep`
  - 制御プレーン向けの `autoUpgradeProfile` リソースを追加し、`name: default-auto-upgrade` を設定。
  - `nodeImageAutoUpgradeProfile` を追加し、`channel: 'NodeImage'`（`nodeImageSelection` は仕様上省略）を設定。
  - Scheduled Query Rule `fleet-approval-pending` の `dependsOn` を両プロファイルに更新。
  - 出力を `autoUpgradeProfileId` と `nodeImageAutoUpgradeProfileId` に更新。
- ドキュメント更新
  - `docs/requirements.md` に 2種類の autoUpgradeProfile 併用要件を追記。
  - `docs/design.md` を Dual プロファイル構成＆Mermaid図に更新。
  - `docs/deployment.md`, `README.md` に Stable/NodeImage プロファイル作成の説明を追加。
- `~/.azure/bin/bicep build infra/main.bicep` を実行し、プレビュー警告のみでビルド成功を確認。
**出力**:
- 変更ファイル: `infra/modules/fleet.bicep`, `docs/requirements.md`, `docs/design.md`, `docs/deployment.md`, `README.md`
- ビルドログ: BCP081 警告のみ（型未提供）
**次**: 検証フェーズでビルド結果を記録し、運用テストは別途計画する。
