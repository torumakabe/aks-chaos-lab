# ADR-005: AKS 診断ログの Basic テーブル収集

## Status

Accepted

## Context

AKS クラスターのコントロールプレーン診断ログ（API Server 監査ログ、コントローラーマネージャー等）が未収集の状態だった。カオス実験やトラブルシューティングではコントロールプレーンの挙動を事後分析できる必要があるが、診断ログは量が大きくなりやすく、Analytics テーブルで収集するとインジェストコストが高くなる。

Log Analytics のリソース固有テーブル（AKSAuditAdmin, AKSControlPlane）は Basic プランに対応しており、Analytics と比べてインジェスト単価が大幅に低い。一方、Basic プランにはクエリ対象が直近 8 日間、join/union 不可、アラートルール不可という制約がある。

## Decision

- **AKSAuditAdmin と AKSControlPlane を Basic テーブルとして収集する**: 診断設定で `logAnalyticsDestinationType: 'Dedicated'` を指定し、リソース固有テーブルへ送信。テーブルプランを Basic に設定する。
- **kube-audit（AKSAudit テーブル）は収集しない**: get/list を含む全操作ログはデータ量が膨大になるため、変更操作のみの kube-audit-admin（AKSAuditAdmin）で十分と判断した。
- **コントロールプレーン全カテゴリを有効化**: kube-apiserver, kube-controller-manager, kube-scheduler, cluster-autoscaler, cloud-controller-manager, guard, CSI コントローラー群を AKSControlPlane テーブルに集約する。
- **AzureDiagnostics モードは使用しない**: AzureDiagnostics テーブルは Basic プラン非対応のため、リソース固有テーブルモードを選択した。

## Consequences

- **コスト効率**: Basic プランによりインジェストコストを抑えつつ、コントロールプレーンの可視性を確保できる。
- **トラブルシューティング**: カオス実験前後のコントロールプレーン挙動（スケジューリング、オートスケーラー判断等）を事後確認できるようになる。
- **制約**: Basic テーブルのため、8 日間を超える過去データへのクエリ、join を使った横断分析、ログベースのアラートは不可。長期保持が必要な場合は totalRetentionInDays の調整で対応する（Basic でも最大 12 年間のアーカイブ保持が可能）。
- **段階的拡張**: 将来的に AKSAudit（全操作ログ）の収集が必要になった場合は、同じ仕組みで追加可能。
