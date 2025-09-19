### 分析 - Fleet Manual Gate 承認待ちのアラート実装 - 2025-09-19T00:00:00Z
**目的**: Azure Kubernetes Fleet Manager の Update Run 承認ゲートが Pending になった際に Azure Monitor で通知する仕組みを Bicep テンプレートへ追加する影響を整理する。
**コンテキスト**: Base モードでは Fleet Manager が更新を管理し beforeGates に Manual 承認を設定済み。承認を待つ Gate に対する運用上の即時通知が求められた。Azure Resource Graph (ARG) から Gate ステータスを取得し、Azure Monitor アラートに利用可能なことが確認された。
**決定**: ARG クエリを Scheduled Query Rule (Log Alert) に組み込み、Gate が Pending の間アラートを発火させる方向で設計を進める。
**実行**:
- Fleet CLI SDK (`~/.azure/cliextensions/fleet/.../_models_py3.py`) で Gate リソースの ARM 表現 (type, properties.state, properties.target.id) を確認。
- 既存の AKS 自動アップグレード監視で使用していた Scheduled Query Rule 構成 (SystemAssigned ID + Reader 付与) を参照し、Fleet 用に再利用可能か検討。
- ARG クエリで `type == "microsoft.containerservice/fleets/gates"` をフィルタし、`properties.state == 'Pending'` かつ 対象 Fleet の ID (`/fleets/<fleetName>/`) を含む Gate を抽出できることを確認。
**出力**:
- **要件更新案 (EARS)**
  - 状態駆動: `システムは aksSkuName が Base の間、Fleet Manager の Manual Gate が Pending の場合は Azure Monitor アラートを発火し、承認待ちであることを通知するものとする。`
- **影響範囲**
  - `infra/modules/fleet.bicep`: ARG クエリ文字列の読み込み、Scheduled Query Rule リソース、Reader ロール割当を追加。
  - `infra/modules/fleet/templates/`: Pending Gate 検出用 KQL を新設。
  - ドキュメント (`docs/design.md`, `docs/deployment.md`, `README.md`) に承認アラート仕組みを追記。
- **リスク/懸念**
  - プレビュー API のため ARG スキーマ変化があり得る。クエリは Fleet 名に依存するため将来のマルチメンバー構成では追加フィルタ検討が必要。
  - Gate が複数 Pending の場合、アラートが継続発火する可能性があるが運用上許容。アラート抑制が必要なら将来のしきい値/ディメンション調整が必要。
- **信頼度スコア**: 0.78 (ARG クエリの構造は文書化されているが実機検証で細部確認が必要)
**検証**: 解析のみ実施。実装後に Bicep ビルドで構文検証を行う。
**次**: 設計フェーズでクエリテンプレートと Scheduled Query Rule の詳細構成を決定する。
