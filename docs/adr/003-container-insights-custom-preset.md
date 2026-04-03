# ADR-003: Container Insights データ収集プリセットの Custom 化

## Status

Accepted

## Context

Container Insights のデータ収集プリセットがデフォルトの `All` のまま運用されており、9 ストリームすべてが Log Analytics に送信されていた。インジェスト実績を分析したところ、Perf（約 45%）と InsightsMetrics（約 6%）が全体の約半分を占めていた。これらのデータは Managed Prometheus（Azure Monitor Workspace）で同等のメトリクスを収集済みであり、Log Analytics への送信は重複コストとなっていた。

一方、ContainerInventory・KubeNodeInventory・ContainerNodeInventory はコンテナやノードの構成情報（イメージ名・タグ・OS バージョン・kubelet バージョン等）の履歴を提供しており、カオス実験の事後分析やトラブルシューティングで `kubectl` では代替できない時系列データとして価値がある。

## Decision

- **`dataCollectionPreset` を `Custom` に変更**: `All`（9 ストリーム）や `LogsAndEvents`（4 ストリーム）ではなく、`Custom` を選択して個別にストリームを指定する。
- **Perf と InsightsMetrics を除外**: Managed Prometheus（AMW）の Recording Rules およびアラートルールで同等データを収集・監視済みのため、Log Analytics への二重送信を停止する。
- **Inventory 系ストリームは維持**: `ContainerInventory`、`ContainerNodeInventory`、`KubeNodeInventory` は構成情報の時系列記録として残す。`LogsAndEvents` プリセットではこれらが除外されるため採用しなかった。
- **収集対象ストリーム（7 本）**: `Microsoft-ContainerLog`、`Microsoft-ContainerLogV2`、`Microsoft-KubeEvents`、`Microsoft-KubePodInventory`、`Microsoft-ContainerInventory`、`Microsoft-ContainerNodeInventory`、`Microsoft-KubeNodeInventory`

## Consequences

- **コスト削減**: インジェスト量が約 50% 減少する見込み。
- **制約**: Perf テーブルと InsightsMetrics テーブルへの KQL クエリが不可となる。ただし Prometheus の PromQL/Grafana で同等の分析が可能。
- **構成情報の維持**: Inventory 系テーブルは引き続き利用可能で、カオス実験時のコンテナ・ノード状態の事後確認に支障はない。
