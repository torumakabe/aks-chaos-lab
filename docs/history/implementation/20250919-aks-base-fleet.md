### 実装 - AKS Baseモードの更新管理をFleet Managerへ委譲 - 2025-09-19T00:00:00Z
**目的**: Baseモード時に Azure Kubernetes Fleet Manager が AKS 更新を管理し、更新前に手動承認を必須とするためのインフラ定義とドキュメントを整備する。
**コンテキスト**: 分析・設計フェーズで Fleet 導入と Approval Gate 要件を確定済み。既存の AKS 自動アップグレード設定やアラートは削除する方針。
**決定**:
- Fleet 用 Bicep モジュールを新設し、フリート・メンバー・更新戦略・自動アップグレード プロファイルを構築する。
- AKS モジュールから自動アップグレード関連設定・アラートを削除する。
- README/デプロイ手順/要件/設計ドキュメントを Fleet 管理と承認フローに合わせて更新する。
**実行**:
- `infra/modules/aks.bicep` から `autoUpgradeProfile`、メンテナンス構成、Scheduled Query アラート、および関連変数を削除し、未使用となった `actionGroupId` パラメータを除去。
- `infra/modules/fleet.bicep` を新規作成し、`Microsoft.ContainerService/fleets@2025-04-01-preview` 系リソースで Approval Gate を含む更新戦略と Stable チャンネル自動アップグレード プロファイルを定義。
- `infra/main.bicep` に Fleet モジュール呼び出しと `fleetName` 変数を追加し、Base モード時のみデプロイされるよう条件分岐を設定。
- `docs/requirements.md` に Fleet 管理および Approval Gate に関する EARS 要件を追記。
- `docs/design.md` に Base モードの更新アーキテクチャを追記し、Mermaid 図で Approval Gate フローを可視化。
- `docs/deployment.md` と `README.md` に Fleet が生成するリソース一覧と `az fleet gate approve` を用いた手動承認手順を記載。
- `~/.azure/bin/bicep build infra/main.bicep` を実行し、型未提供警告（BCP081）のみでビルド成功を確認。
**出力**:
- コード: `infra/modules/aks.bicep`, `infra/modules/fleet.bicep`, `infra/main.bicep`
- ドキュメント: `docs/requirements.md`, `docs/design.md`, `docs/deployment.md`, `README.md`
- ビルドログ: Bicep ビルド成功（BCP081 警告のみ）
**検証**: Bicep ビルドで構文検証を実施。Fleet リソースの型はプレビューのためスキップされたが、構文エラーは無し。
**次**: 検証フェーズでドキュメント整合性と追加テスト要否を確認。必要に応じて Fleet ゲート動作の実機検証タスクを計画する。
