### 設計 - Fleet 自動アップグレードを制御プレーン用とノードイメージ用に分割 - 2025-09-19T00:00:00Z
**目的**: 制御プレーン用（Stableチャネル）とノードイメージ用（NodeImageチャネル）の2種類の autoUpgradeProfiles を同一フリートに定義し、承認ゲートを共有する構成を決定する。
**コンテキスト**: 分析フェーズで併用が推奨されることを確認。既存テンプレートは `default-auto-upgrade` の1リソースのみ。
**決定**:
1. `infra/modules/fleet.bicep` に制御プレーン用 `autoUpgradeProfile` とノードイメージ用 `nodeImageAutoUpgradeProfile` を定義する。
2. 両プロファイルとも `properties.updateStrategyId = fleetUpdateStrategy.id` を設定し、Manual Approval Gate を共有する。
3. `channel` は制御プレーン用が `autoUpgradeChannel`（既定: Stable）、ノードイメージ用が `NodeImage` 固定。どちらも `nodeImageSelection.type = 'Latest'` を指定する。
4. Scheduled Query Rule `fleet-approval-pending` の `dependsOn` を両プロファイルに更新し、Gate 作成前にアラートが走らないようにする。
5. 出力は `autoUpgradeProfileId` と `nodeImageAutoUpgradeProfileId` の2つをエクスポートする。
6. ドキュメント更新: 要件・設計・デプロイ手順・README に「2つの autoUpgradeProfiles を作成する」旨を追記。
**フローダイアグラム**
```mermaid
flowchart TD
  Fleet --> Strategy[Update Strategy (Approval Gate)]
  Strategy --> CPProfile[autoUpgradeProfile (Stable)]
  Strategy --> NodeProfile[autoUpgradeProfile (NodeImage)]
  CPProfile --> UpdateRunCP[Update Run (Control plane + Node Image)]
  NodeProfile --> UpdateRunNode[Update Run (Node Image only)]
```
**テスト計画**
- `~/.azure/bin/bicep build infra/main.bicep` で構文検証。
- 今後の運用テスト: `AutoUpgradeProfileStatus` と `fleets/updateRuns` を監視し、両プロファイルが意図したタイミングで Update Run を生成するか確認する。
**リスク/フォローアップ**
- Update Run の承認回数増: 承認手順と通知（`fleet-approval-pending`）で対応。
- ノード再起動が増える場合、運用チームと調整して承認タイミングを決める必要あり。
**信頼度**: 0.85
**次**: 実装フェーズで Bicep とドキュメントの更新を行う。
