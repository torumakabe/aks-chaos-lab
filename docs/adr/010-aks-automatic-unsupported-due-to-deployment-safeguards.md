# ADR-010: AKS Automatic は本リポジトリで非サポート (Deployment Safeguards が chaos-mesh と非互換)

## Status

Accepted

## Context

本リポジトリ `aks-chaos-lab` の中核機能は **AKS 上での Chaos Engineering 実演** であり、その実装は以下の 2 段で構成される:

1. **Chaos Mesh** (`infra/helm/chaos-mesh-values.yaml`、`azure.yaml services.chaos-mesh`): クラスタ内の fault injector。chaos-daemon DaemonSet が各ノードで privileged 動作する。
2. **Azure Chaos Studio** (`infra/modules/chaos/experiments.bicep`): `urn:csci:microsoft:azureKubernetesServiceChaosMesh:*` 系 fault types で **Chaos Mesh の CRD を作成** することで実験を駆動する。AKS-native fault types は実装上 Chaos Mesh を前提としており、Chaos Mesh が不在ならいかなる実験も実行できない。

リポジトリで定義済みの 9 種の experiment はすべて chaos-daemon に依存する:

| experiment | Chaos Mesh CRD | 必要な権限 |
|---|---|---|
| exp-aks-pod-failure | PodChaos `pod-failure` | SYS_PTRACE (init container 書換) |
| exp-aks-network-delay / loss | NetworkChaos | NET_ADMIN (tc / iptables) |
| exp-aks-stress | StressChaos | SYS_PTRACE (process attach) |
| exp-aks-io | IOChaos | sidecar + privileged |
| exp-aks-time | TimeChaos | SYS_PTRACE (libfaketime injection) |
| exp-aks-kernel | KernelChaos | SYS_ADMIN + eBPF |
| exp-aks-http | HTTPChaos | sidecar + iptables |
| exp-aks-dns | DNSChaos | NET_ADMIN + chaos-daemon /etc/resolv.conf 書換 |

一方、**AKS Automatic** モードは [Deployment Safeguards](https://learn.microsoft.com/azure/aks/deployment-safeguards) が **Enforce 強制** で常時有効化されており、無効化はできない。Microsoft Learn の公式記述:

> *"Baseline Pod Security Standards are now turned on by default in AKS Automatic. The baseline Pod Security Standards in AKS Automatic can't be turned off."*
> — Microsoft Learn `aks/deployment-safeguards`

実機で `kubectl get validatingadmissionpolicy aks-managed-baseline-capabilities -o yaml` を取得した結果、CEL allow-list は以下のとおり PSS Baseline 標準に固定されている:

```cel
allowedCapabilities = [
  "AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID",
  "KILL", "MKNOD", "NET_BIND_SERVICE", "SETFCAP", "SETGID",
  "SETPCAP", "SETUID", "SYS_CHROOT"
]
```

Chaos Mesh `v2.8.3` の公式 [`values.yaml`](https://github.com/chaos-mesh/chaos-mesh/blob/v2.8.3/helm/chaos-mesh/values.yaml) で chaos-daemon 既定 capability:

```yaml
chaosDaemon:
  capabilities:
    add: [SYS_PTRACE, NET_ADMIN, NET_RAW, MKNOD, SYS_CHROOT, SYS_ADMIN, KILL, IPC_LOCK]
  privileged: true
```

**8 個中 5 個** (`SYS_PTRACE`, `NET_ADMIN`, `NET_RAW`, `SYS_ADMIN`, `IPC_LOCK`) が拒否される。さらに:

- `aks-managed-baseline-privileged-containers` VAP が `privileged: true` を別途拒否する。
- `aks-managed-baseline-hostpath-volumes` VAP が **Microsoft 認定イメージ以外**の HostPath を全拒否する。chaos-daemon は `/run/containerd/containerd.sock`, `/sys/`, `/var/run/` を mount するため、サードパーティイメージである chaos-mesh では通過できない。
- `az aks safeguards update --excluded-ns` は AKS Automatic では `RequestNotAllowedBecauseAssociatedClusterIsAutomaticCluster` で API 拒否される (関連 issue: [Azure/AKS#5442](https://github.com/Azure/AKS/issues/5442) — OTel Collector で同質ブロックを確認)。
- `kubectl edit` で VAP を変更しても AKS が数分で自動復旧する。

### chaos-mesh helm parameter での回避可否

`chaosDaemon.privileged: false` + `chaosDaemon.capabilities.add: []` のように chaos-daemon を castrate しても、

- HostPath は依然として拒否される (image レベルでブロック)。
- そもそも capability を削ると chaos-daemon は fault injection を**実行できない** (各 capability は使用箇所が固定)。
- chaos-mesh chart には chaos-daemon DaemonSet を無効化するフラグが**存在しない** (helm/chaos-mesh の templates 構造で hardcoded)。

すなわち **helm parameter による回避は技術的に不可能**。

### 実機検証 (2026-05-06)

`AKS_SKU_NAME=Automatic` でクラスタを構築 (`sli-auto` 環境、AKS 1.34.6 Standard tier) し chaos-mesh を `helm install` した結果、次のエラーで停止:

```
Error: daemonsets.apps "chaos-daemon" is forbidden:
  ValidatingAdmissionPolicy 'aks-managed-baseline-capabilities'
  with binding 'aks-managed-baseline-capabilities-binding' denied request:
  Disallowed capabilities detected:
  container "chaos-daemon" has disallowed capabilities: [SYS_PTRACE]
```

### 参考: Microsoft 側のロードマップ

AKS Automatic で chaos-mesh を Trusted Container Add-on として allow-list に追加する公式アナウンスは現時点で**存在しない** (2026-05 時点)。本決定は将来 AKS / chaos-mesh 双方の状況が変わった段階で再検討する余地がある。

## Decision

**AKS Automatic を本リポジトリで正式に非サポートとする。**

### Bicep の変更

1. `infra/main.bicep` の `aksSkuName` `@allowed` から `'Automatic'` を削除。`['Base']` のみ受け付ける。
2. `aksSkuName == 'Base'` を前提とした条件分岐 (Fleet 関連 3 箇所) を削除し、Fleet を unconditional に作成。
3. `infra/modules/aks.bicep` から `aksAutomaticSpecificProperties` / `aksAutomaticProperties` 変数を削除し、`properties: aksBaseProperties` に簡約。
4. パラメータ説明文から「Base モードでのみ使用」のような Automatic 前提の記述を削除。

### ドキュメントの変更

- `README.md` の「Base / Automatic 両対応」記述を削除し、本 ADR へリンク。
- ADR-008 / ADR-006 / ADR-007 内の Automatic 言及は履歴として保持 (過去 ADR の改変はしない)。

### 環境変数

`AKS_SKU_NAME` 環境変数は引き続き残すが、`Base` 以外の値は Bicep validation で `Allowed values are: 'Base'` エラーとなる。これにより誤設定が早期に検出される。

## Consequences

### 利点

- **リポジトリの目的との整合**: chaos-mesh が中核である本ラボにおいて、Chaos が動かない構成を選択可能にしておくことは誤解を招くだけ。明示的に除外することで「動く構成だけが選べる」状態になる。
- **コードの簡素化**: `aksAutomaticSpecificProperties` (~30 行) と Fleet 関連の三項演算子 (4 箇所) が削除され、`infra/main.bicep` / `infra/modules/aks.bicep` の認知負荷が下がる。
- **安全側の早期失敗**: `AKS_SKU_NAME=Automatic` を誤って設定しても、`azd provision` の Bicep 検証段階で即座に拒否される (高価な AKS 作成後に chaos-mesh で失敗するのを防ぐ)。
- **ドキュメントの一貫性**: 「9 種の Chaos 実験」と「Automatic 対応」を同時に主張する矛盾が解消される。

### 制約 / トレードオフ

- **AKS Automatic の運用簡素化メリットは享受できない**。Node Auto Provisioning や K8s 自動アップグレードを試したいユーザは、本リポジトリの目的とは別の lab で確認する必要がある。
- **将来の見直しが必要**。AKS 側で chaos-mesh が Trusted Add-on になった場合、または AKS Automatic で第三者 DaemonSet の host capability が limited に許可されるようになった場合、本 ADR を Superseded にして再度 Automatic を追加する判断が必要。

### 影響範囲

- 既存環境 (`eval` など Base モードで稼働中): **影響なし**。`aksSkuName='Base'` のため、Bicep の `@allowed` 縮小後も差分なし。
- `infra/main.json` (auto-generated): `az bicep build` で再生成され、`Automatic` の文字列が消える。
- `azure.yaml`: 変更なし (SKU を意識しない構造)。
- `k8s/`: 変更なし。
- 過去 ADR (008 等) の Automatic 言及: 履歴として保持。

## 代替案 (不採用)

### 案 B: Automatic を allow-list に残し README で警告のみ追加

- 利点: 将来の relaxation 時に Bicep を変更せず復活できる。
- 不採用理由: 警告だけでは早期失敗が効かず、ユーザは `azd up` で AKS 作成 (~7 分) 後の chaos-mesh deploy 段階で初めて失敗を知る。コスト・時間ロスが大きい。さらに「コードでは選べる選択肢」が「実際には動かない」という UX 上の矛盾が残る。

### 案 C: chaos-mesh を fork して Microsoft 認定イメージに置換

- 利点: AKS Automatic 上でも動く可能性。
- 不採用理由: スコープが本ラボの範疇を大きく逸脱する。chaos-daemon の fault injection 実装は kernel-level の操作であり、Microsoft 側の認定パイプラインにも乗らない。ラボ教材としても保守困難。

### 案 D: Azure Chaos Studio の VMSS-level fault types に切り替え

- 利点: chaos-mesh 不要。
- 不採用理由: AKS Automatic は VMSS をユーザに公開せず Azure 側が管理するため、`urn:csci:microsoft:virtualMachineScaleSet:*` 系の fault types を AKS Automatic クラスタに対して targeting できない。

## 参考

- [Microsoft Learn: Deployment safeguards in AKS](https://learn.microsoft.com/azure/aks/deployment-safeguards)
- [Microsoft Learn: AKS Automatic 概要](https://learn.microsoft.com/azure/aks/intro-aks-automatic)
- [Chaos Mesh v2.8.3 Helm values](https://github.com/chaos-mesh/chaos-mesh/blob/v2.8.3/helm/chaos-mesh/values.yaml)
- [Azure/AKS#5442 — OTel Collector blocked by deployment safeguards on Automatic](https://github.com/Azure/AKS/issues/5442)
- 実機検証ログ: `tmp/sli-auto/cycle1-up.log` (chaos-mesh helm install 失敗時の VAP denial メッセージ)
- VAP CEL allow-list の取得元: 本リポジトリで `aksSkuName=Automatic` で構築した AKS 1.34.6 クラスタ `aks-aks-chaos-lab-sli-auto` (japaneast, 2026-05-06)
