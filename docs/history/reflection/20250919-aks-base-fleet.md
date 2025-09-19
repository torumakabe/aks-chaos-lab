### 振り返り - Fleet Manager 導入による更新管理 - 2025-09-19T00:00:00Z
**目的**: Baseモード更新管理の Fleet 移行作業を振り返り、改善点とフォローアップを整理する。
**コンテキスト**: Fleet 連携と Approval Gate 仕様を実装し、ドキュメントとテンプレート構成を更新した。
**成功**:
- Fleet リソース定義をモジュール化し、Base モードのみへの条件適用で既存 Automatic モードへ影響を出さずに移行できた。
- 仕様ドキュメント（requirements/design/deployment/README）がすべて更新され、承認フローと運用手順を明示できた。
**課題 / 改善余地**:
- Fleet Gate および Update Run 失敗時の通知メカニズムはプレビュー情報が不足しており、実機検証に基づくアラート設計が未完。
- プレビュー API のため Bicep ビルドで型チェックがスキップされる。GA 後にテンプレート検証を再実施する必要がある。
**学び**:
- Azure CLI 拡張のローカル SDK からプレビュー API のスキーマを把握する手法が有効だった。
- Approval Gate を beforeGates に配置することで、Update Run 全体の開始をハードゲート化できる。
**フォローアップ**:
1. Fleet Gate / Update Run の実機ログを収集し、アクティビティログまたは Monitor Logs を根拠とした通知実装を検討する。
2. Fleet API が GA になった際に Bicep 型定義を更新し、警告解消と追加バリデーションを行う。
3. Base/Automatic 切替時のドキュメント差分を CI で検証する仕組み（例: `azd what-if` + lint）を検討する。
