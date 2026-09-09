# ADR-008: AKS ノード OS を Ubuntu 24.04 (osSKU: Ubuntu2404) に明示ピン留め

## Status

Accepted

## Context

AKS ノードプール `default` の現行ノードイメージは `AKSUbuntu-2204gen2containerd-202603.30.0` (Ubuntu 22.04) で稼働している。`infra/modules/aks.bicep` の `agentPoolProfiles` には `osSKU` が明示されておらず、AKS 既定の挙動（Kubernetes 1.25〜1.34 では Ubuntu 22.04 がデフォルト）に依存した状態になっていた。

Microsoft は Ubuntu 22.04 の AKS サポート終了を公式にアナウンスしている:

- 2027-06-30: Ubuntu 22.04 のサポート・セキュリティアップデート終了。これ以降、新規ノードプール作成・ノードイメージ生成・セキュリティパッチ配布が停止。
- 2028-04-30: Ubuntu 22.04 ノードイメージが削除され、スケール・修復操作が失敗するようになる。
- 参考: [Upgrade operating system (OS) versions in AKS](https://learn.microsoft.com/azure/aks/upgrade-os-version) / [Azure updates #557928](https://azure.microsoft.com/updates/?id=557928)

ラボ環境とはいえ、サポート終了 OS に依存し続けるのは IaC のあるべき姿に反する。また K8s 1.33 のまま osSKU を既定 (`Ubuntu`) に設定しても 22.04 のままであり、24.04 へ移行するには次のいずれかが必要:

1. Kubernetes を 1.35 以上にアップグレードし、osSKU は `Ubuntu` 既定のまま自動移行を待つ
2. osSKU を versioned な `Ubuntu2404` に明示ピン留めし、K8s バージョンに依存せず 24.04 に切り替える

本プロジェクトの AKS クラスターは現在 K8s 1.33 で、1.35 への追随には Automatic チャネルの進行待ちまたは明示アップグレードが必要。OS 移行と K8s 移行を分離したい（リスクを独立に評価したい）ラボの性質上、先に OS だけを移行できる選択肢 2 の方が適する。

## Decision

`infra/modules/aks.bicep` の `default` ノードプール定義に以下を追加する:

```bicep
osSKU: 'Ubuntu2404'
```

- Kubernetes 1.32〜1.38 で `Ubuntu2404` はサポートされる（現行 1.33 は範囲内）。
- 既存の Blue-Green アップグレード戦略（`upgradeStrategy: 'BlueGreen'`, drainBatchSize 50%, batchSoak 15min, finalSoak 60min）により、ノード置換は安全にローリング実施される。
- `agentPoolProfiles` のうち `default` のみ明示。`aksAutomaticSpecificProperties` 側の `system` プールは Automatic モード管理のため osSKU を指定しない（本構成は Base モードを使用）。
- `osType` は Linux が既定のため追加指定しない。

## Consequences

- **利点**:
  - Ubuntu 22.04 retirement（2027-06-30 / 2028-04-30）に先立って明示的に 24.04 にピン留めすることで、IaC がサポート中 OS にのみ依存する状態になる。
  - K8s バージョンアップグレードと OS 移行を独立に扱えるため、変更のリスク評価と切り戻し判断が単純化する。
  - `nodeImageVersion` が `AKSUbuntu-2404gen2containerd-*` になり、kernel / glibc / systemd などが新しい LTS に揃う。
  - ラボ目的（「試して、データも見て」）上、新しい OS バージョンでの Chaos 実験 / 観測挙動を確認する機会にもなる。

- **制約 / トレードオフ**:
  - `Ubuntu2404` SKU は Kubernetes 1.38 までのサポートとアナウンスされている。将来 1.39 以降へ上げる際に、再度 `Ubuntu`（既定 SKU、1.35+ で 24.04 既定）への戻し or 次世代 versioned SKU への移行判断が必要。
  - 適用時、Blue-Green 戦略により `default` プールのノードが置換される。ソーク時間は batchSoak 15min + finalSoak 60min ≈ 75 分以上を見込む。
  - Ubuntu 24.04 は kernel / glibc が上がるため、node-level の依存（privileged DaemonSet 等）に互換性影響が出る可能性はゼロではない。本リポジトリは AMA / ama-metrics / Chaos Mesh 等マネージド/CNCF 系コンポーネントに限定されており、Microsoft 側で 24.04 対応済みのため影響は低いと判断。
  - CVM (Confidential VM) は Ubuntu 22.04 では非対応、24.04 では対応。本プロジェクトは CVM を使っていないため影響なし。

- **代替案（不採用）**:
  - `osSKU: 'Ubuntu'`（既定）: K8s 1.35+ アップグレードまで 22.04 のまま。OS 移行のタイミングを K8s 移行と結びつけざるを得ない。
  - `osSKU: 'AzureLinux3'`: 選択肢として有効だが、OS 更新と同時にディストリビューション変更まで行うのはスコープ過大。別 ADR として将来検討余地あり。
  - そのまま放置: サポート終了後に強制移行となり計画的運用ができない。ラボ教材としても悪手。

## 付録: 適用時の観測と所要時間の評価

本 ADR 適用時（2026-04-24）は、Decision に記載した Blue-Green 設定に加えて `drainTimeoutInMinutes: 30` を使用し、2 台のノードを1バッチで置換した。

この適用では VMSS と agentPool 名を維持したまま、同一 VMSS の capacity が2台から4台へ増加した。Green の2台が Ready になった後、旧インスタンスの削除により2台へ戻った。[公式ドキュメントの「parallel green pool」](https://learn.microsoft.com/azure/aks/upgrade-aks-cluster#blue-green-node-pool-upgrades)は VMSS レベルの実装を明示しておらず、この観測を他の構成や将来の実装に対する保証とはしない。

Green サージ開始から旧インスタンス削除完了までは約95分、`provisioningState: Succeeded` までは約100分だった。当時は、設定した soak 合計75分との差を drain と VMSS scale-in の所要時間と解釈し、設定どおりの soak と整合すると評価した。所要時間の見積もりには、soak 以外の処理時間も必要である。

当時の Resource Graph 応答には `upgradeStrategy`、`upgradeSettingsBlueGreen`、`blueGreenStatus` が含まれず、詳細な進行状況の取得には制約があった。

## 参考

- [Upgrade OS versions in AKS](https://learn.microsoft.com/azure/aks/upgrade-os-version)
- [Node images in Azure Kubernetes Service (AKS)](https://learn.microsoft.com/azure/aks/node-images)
- [Azure updates: Ubuntu 22.04 retirement (#557928)](https://azure.microsoft.com/updates/?id=557928)
- [AKS Retirement GitHub issue (Ubuntu 22.04)](https://aka.ms/aks/ubuntu2204-retirement-github)
