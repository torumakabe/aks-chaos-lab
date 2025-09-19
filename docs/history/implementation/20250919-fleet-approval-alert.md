### 実装 - Fleet Manual Gate 承認待ちアラート追加 - 2025-09-19T00:00:00Z
**目的**: Base モードで Fleet Manual Gate が Pending になった際に Azure Monitor アラートを発火させる仕組みを追加する。
**コンテキスト**: 設計で Scheduled Query Rule + ARG 連携案を確定。既存 Fleet モジュールを拡張し、ドキュメントに新しいアラート運用を追記する。
**決定**:
- KQL テンプレートを `infra/modules/templates/pending-approval-gate.kql` として管理し、Fleet ID を置換して利用する。
- `infra/modules/fleet.bicep` に Scheduled Query Rule (`fleet-approval-pending`) と Reader ロール割当を追加。
- `actionGroupId` パラメータを Fleet モジュールに渡して通知先を制御。
- Requirements/Design/Deployment/README を更新してアラート仕様を明記。
**実行**:
- `infra/modules/fleet.bicep` に `actionGroupId` 追加、KQL 読み込み変数・Scheduled Query Rule・Reader ロール割当を定義。
- `infra/main.bicep` から Fleet モジュールへ `actionGroupId` を配線。
- `infra/modules/templates/pending-approval-gate.kql` を新規作成。
- `docs/requirements.md`, `docs/design.md`, `docs/deployment.md`, `README.md` を承認アラート記述で更新。
- `~/.azure/bin/bicep build infra/main.bicep` を実行し、警告のみでビルド成功を確認。
**出力**:
- コード: `infra/modules/fleet.bicep`, `infra/main.bicep`, `infra/modules/templates/pending-approval-gate.kql`
- ドキュメント: `docs/requirements.md`, `docs/design.md`, `docs/deployment.md`, `README.md`
- ビルドログ: Bicep 成功 (BCP081 警告のみ)
**検証**: フェーズ4で Bicep ビルド結果を記録済み。
**次**: Pending Gate 実機テストと通知挙動確認を将来タスクとして検討。
