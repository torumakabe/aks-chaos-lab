# ADR-018: NAPをArm64 workloadの追加capacityに採用する

## Status

Accepted

- Date: 2026-08-21

## Context

Japan Eastで特定のVM SKUを割り当てられない場合にも、Arm64の`chaos-app`を実行できる追加capacityが必要である。ノードはEphemeral OS Diskを維持する。

AKS Standardでは、NAPを有効にしても従来型のSystem AgentPoolが必要である。NAPはSystem workload用ノードも作成できるが、System AgentPoolそのものを置き換えられない。このため、NAPだけではAKS全体の割り当て耐性を確保できない。

一方、NAPは`Standard_D4pds_v5`と`Standard_D4pds_v6`を別々のNodeClaimとして作成できる。両SKUはEphemeral OS Diskのplacementが異なるため、単一のVirtual Machines multi-SKU AgentPoolには混在できない。

## Decision

1. AKS StandardでNAPを採用し、Arm64 User workloadの追加capacityを作成する。
2. 既存のArm64 System AgentPoolを2台で維持する。System workloadのcapacityはNAPへ移さない。
3. NAPのNodePoolは`Standard_D4pds_v5`または`Standard_D4pds_v6`、on-demand、Ephemeral OS Diskを条件とする。
4. NAPとCluster Autoscalerは併用しない。NAPのfeature flagは既定で無効にする。
5. SKUの選択順序、`AllocationFailed`後の別SKUへの再試行、zoneの切り替え順序は保証事項として扱わない。
6. 初期導入時のNodePool resource limitは8 vCPUとする。候補SKUはいずれも4 vCPUのため、通常は2台分に相当する。ただし、Karpenterのlimit判定はeventual consistencyであり、急なscale-outでは一時的に上限を超える可能性があるため、厳密なノード数上限として扱わない。
7. consolidationは空ノードだけを対象とし、node expirationは無効にする。voluntary disruptionは同時1台までに制限する。

## Consequences

- User workloadには、異なるEphemeral OS Disk placementを持つv5とv6を追加capacityの候補として提供できる。
- 固定System AgentPoolの割り当て失敗や修復不能には対応できない。
- 別SKUまたは別zoneへのfallbackは保証されない。
- custom taintを設定しないため、制約が一致する`kube-system` PodがNAP nodeへ配置される可能性がある。System AgentPoolを2台維持する判断は、System Podの排他的な配置を保証しない。
- 稼働中のworkloadを低利用率だけを理由に移動しないため、`WhenEmptyOrUnderutilized`よりも余剰capacityが残る場合がある。
- 8 vCPUのresource limitは費用の目安にはなるが、急なscale-out時の一時超過を防ぐ厳密なコスト上限にはならない。
- 8 vCPUを超える追加capacityが必要な場合は、費用とsubnet IPへの影響を確認してからlimitを変更する必要がある。
- 新規NAP nodeでは、`ama-logs`のOTLP listenerがReadyになる前にworkloadが起動し、最初のlog batchが失われる場合がある。collectorがReadyになった後のログ収集は回復するため、監査用途ではない起動ログに限る既知の制約として受容する。
- System AgentPoolの固定費と、NAPの監視および障害調査の運用負荷が残る。

NAPの採用により、Arm64 User workloadはv5とv6を追加capacityの候補にできる。ただし、固定System AgentPoolをNAPへ移さないため、この判断だけではAKS全体の割り当てエラーを回避できない。

## 採用しなかった代替案

- **v5とv6を単一Virtual Machines multi-SKU AgentPoolへ入れる**: 異なるEphemeral OS Disk placementを混在できない。
- **System workloadにもNAPを使用する**: 必須のSystem AgentPoolを削減できず、管理対象だけが増える。
- **Managed OS Diskへ変更する**: Ephemeral OS Diskを維持する要件を満たさない。

## 関連 ADR

- ADR-008: System AgentPoolのUbuntu 24.04指定を維持する。
- ADR-010: AKS Automaticを採用せず、AKS Standardを維持する。
