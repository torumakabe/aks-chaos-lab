### 振り返り - Dual autoUpgradeProfiles 構成 - 2025-09-19T00:00:00Z
**成功**:
- Stable（制御プレーン）と NodeImage（ノード OS）双方の autoUpgradeProfiles をテンプレートに組み込み、Manual Gate を共有できた。
- ドキュメントに併用方針を明記し、運用ガイドラインに沿った構成を説明できた。

**課題**:
- 実際の Update Run 発生頻度や承認フロー負荷は未検証。運用での観測が必要。
- NodeImage チャネルが Stable とどのように連動するか（Update Run の競合など）は追加モニタリングが必要。

**学び**:
- autoUpgradeProfiles リソースは複数併用可能であり、`updateStrategyId` を共有させることで承認ゲートを再利用できる。
- Bicep 出力を個別にエクスポートしておくと、後続のパイプライン設定で利用しやすい。

**フォローアップ**:
1. 実環境で Update Run ログを収集し、NodeImage 専用プロファイルの動作を記録する。
2. 承認通知（`fleet-approval-pending`）が両 Update Run で適切に機能するか確認する。
3. 必要であれば NodeImage プロファイルを停止する際の手順（`disabled: true` など）をドキュメントに追加検討。
