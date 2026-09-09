# ADR-020: Node Auto Provisioning を既定で有効にする

## Status

Accepted

- Date: 2026-09-09

## Context

ラボの標準構成を Node Auto Provisioning（NAP）利用とし、環境フラグが未設定でも標準構成を再現できるようにする。[ADR-018](018-adopt-aks-node-auto-provisioning-for-arm64-capacity.md) の Context 末尾、Decision 4 と 9 にある既定無効および未設定時のスキップの判断を部分変更する。

eval 環境では AKS の node provisioning mode が Auto だった一方、使用した azd 環境にはフラグがなく、既定値 false によって Manual と Cluster Autoscaler への復元が要求され、プレビューが停止した。フラグが未設定になった経緯は未確認である。既定無効を続けると、標準構成の再現に毎回明示的な有効化が必要になるため採用しない。

## Decision

1. `AZURE_AKS_ENABLE_NODE_AUTO_PROVISIONING` が未設定の場合は NAP を有効にする。Bicep の main と module の既定値、azd parameters の fallback、条件付き task の未設定時の扱いを一致させる。
2. 明示的な false は無効のままとする。空文字を含む不正値や azd 環境の解決失敗では後続へ進めない。
3. 通常の `azd up` の実行順序と条件付き `node-provisioning` task を維持する。未設定または true の場合に適用し、false の場合はスキップする。false への変更で既存 NodePool 等を自動削除しない。
4. ADR-018 の他の判断は維持する。NAP 有効時は System AgentPool を 2 台に固定し、Cluster Autoscaler を無効にする。独自の NodePool と AKSNodeClass を使用し、VM SKU 候補、8 vCPU の容量目安、disruption 制約も変更しない。

## Consequences

- フラグが未設定でも NAP を利用する標準構成で構築できる。
- フラグが未設定の既存非 NAP 環境も、次回の provision で NAP 有効化の対象になる。従来構成を保持するには false の明示が必要になる。
- 明示的に無効化する選択は残るが、既存 NodePool 等の撤去は別途必要になる。適用条件と無効化を含む操作手順は [デプロイ文書の NAP 節](../deployment.md#node-auto-provisioning) を参照する。
