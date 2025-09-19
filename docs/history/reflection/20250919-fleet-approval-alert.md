### 振り返り - Fleet Manual Gate アラート追加 - 2025-09-19T00:00:00Z
**成功**:
- ARG クエリ + Scheduled Query Rule で承認待ちの可視化をシンプルに実装できた。
- `actionGroupId` をモジュール引数化したことで通知経路の有無を柔軟に制御可能にした。

**課題**:
- 実環境で Gate リソースのサンプルデータを取得していないため、KQL クエリの完全な検証は未実施。
- 複数 Gate が Pending の場合の通知頻度制御は今後の運用設計で検討が必要。

**学び**:
- `sys.loadTextContent` 用のテンプレート パスは共通 `infra/modules/templates` にまとめることで再利用性が高まる。
- Scheduled Query Rule でも SystemAssigned ID + Reader で ARG 参照を許可できる。

**フォローアップ**:
1. 実環境で Pending Gate を発生させ、アラート発火と解消の挙動を確認する。
2. 連続通知を抑制するための `failingPeriods` などのアラート設定最適化を検討する。
3. アクション グループを使わない場合でも監視ダッシュボードで Pending Gate を表示するための可視化案を検討する。
