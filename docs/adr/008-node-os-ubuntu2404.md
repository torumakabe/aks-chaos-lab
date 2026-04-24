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
  - 適用時、Blue-Green 戦略により `default` プール VMSS が置換される。ソーク時間は batchSoak 15min + finalSoak 60min ≈ 75 分以上を見込む。
  - Ubuntu 24.04 は kernel / glibc が上がるため、node-level の依存（privileged DaemonSet 等）に互換性影響が出る可能性はゼロではない。本リポジトリは AMA / ama-metrics / Chaos Mesh 等マネージド/CNCF 系コンポーネントに限定されており、Microsoft 側で 24.04 対応済みのため影響は低いと判断。
  - CVM (Confidential VM) は Ubuntu 22.04 では非対応、24.04 では対応。本プロジェクトは CVM を使っていないため影響なし。

- **代替案（不採用）**:
  - `osSKU: 'Ubuntu'`（既定）: K8s 1.35+ アップグレードまで 22.04 のまま。OS 移行のタイミングを K8s 移行と結びつけざるを得ない。
  - `osSKU: 'AzureLinux3'`: 選択肢として有効だが、OS 更新と同時にディストリビューション変更まで行うのはスコープ過大。別 ADR として将来検討余地あり。
  - そのまま放置: サポート終了後に強制移行となり計画的運用ができない。ラボ教材としても悪手。

## 付録: Blue-Green アップグレード実測動作

本 ADR 適用時（2026-04-24）の実測結果を記録する。公式ドキュメント ([Blue-green node pool upgrades](https://learn.microsoft.com/azure/aks/upgrade-aks-cluster#blue-green-node-pool-upgrades)) とは用語・内部実装の見え方が異なるため、運用上の参照として残す。

### 設定値（`infra/modules/aks.bicep` の `default` pool）

| パラメータ | 値 |
|---|---|
| upgradeStrategy | BlueGreen |
| drainBatchSize | 50% |
| drainTimeoutInMinutes | 30 |
| batchSoakDurationInMinutes | 15 |
| finalSoakDurationInMinutes | 60 |

### 実測タイムライン（ノード 2 台、1 バッチで置換）

| 時刻 (JST) | 事象 | 経過 |
|---|---|---|
| 11:10:46 | Green サージ開始（VMSS capacity 2→4） | 0 |
| 11:11:48 | Blue 2 ノードが cordoned (SchedulingDisabled) | +1m |
| 11:12 頃 | Green 2 ノード Ready | +1〜2m |
| 12:45:52 | VMSS capacity 4→2、Blue インスタンス削除完了 | **+94m** |
| 12:51 | `provisioningState: Succeeded` | +100m |

- Blue cordon → Blue delete の所要 = **94 分**
- 設定 soak 合計 = batchSoak(15) + finalSoak(60) = **75 分**
- 差分 ~19 分 = drain フェーズ（Pod eviction）＋ VMSS scale-in API 所要と解釈でき、**soak time は設定通り**動作している。

### 実装挙動 vs ドキュメント

公式ドキュメントは「parallel green pool を作成し、blue pool を削除する」と記述するが、実測では以下の動作が観測された:

- **VMSS は 1 つのまま**（`aks-default-15188033-vmss`）。新しい VMSS は作られない。
- **agentPool 名も変わらない**（`default` のまま）。
- 同一 VMSS 内で **capacity をサージ**（2→4）し、新 instance (`00000a`, `00000b`) を Green として Ready にしてから、旧 instance (`000008`, `000009`) を cordon / drain / soak / delete する。
- Resource Graph はプレビュー API 固有の `upgradeStrategy` / `upgradeSettingsBlueGreen` / `blueGreenStatus` を返さない。`az rest --url ...?api-version=2025-08-02-preview` を使う必要がある。

ドキュメントは「parallel pool」という抽象的な表現で、VMSS レベルの具体実装は明示していない。ラボでの挙動確認・トラブルシュート時には「同一 VMSS 内サージ」であることを前提に監視クエリを書くのが実践的。

### 運用メモ

- Blue-Green コミット中は `nodepool show` の `provisioningState` が長時間 `Upgrading` / `Updating` のままとなる。`az rest` の `blueGreenStatus` で `phase` を見ると進行が細かくわかる。
- `kubectl get nodes` で `SchedulingDisabled` のノードが残っていても、Green 側が Ready であればワークロードには影響しない。
- `azd provision` 全体では AKS リソース更新に ~1h 47m、総計 2h 01m かかった。

## 参考

- [Upgrade OS versions in AKS](https://learn.microsoft.com/azure/aks/upgrade-os-version)
- [Node images in Azure Kubernetes Service (AKS)](https://learn.microsoft.com/azure/aks/node-images)
- [Azure updates: Ubuntu 22.04 retirement (#557928)](https://azure.microsoft.com/updates/?id=557928)
- [AKS Retirement GitHub issue (Ubuntu 22.04)](https://aka.ms/aks/ubuntu2204-retirement-github)
