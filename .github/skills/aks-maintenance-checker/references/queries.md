# メンテナンス照会と表示の詳細

照会する対象とイベント種別が決まったら、該当するクエリ例を読む。状態分類や時刻の意味を確認する場合は後半のリファレンスを参照する。

## 目次

- [ツールとデータソース](#ツールとデータソース)
- [照会例](#照会例)
- [表示例](#表示例)
- [リファレンス](#リファレンス)
- [取得範囲とデータの制限](#取得範囲とデータの制限)

## ツールとデータソース

| Tool | 用途 |
|------|------|
| `az graph query` | Azure Resource Graphからイベント情報を取得 |
| `az aks show` | クラスター情報の確認（リソースID取得） |
| `az fleet show` | Fleet情報の確認（リソースID取得） |
| `az fleet gate list` | Fleet承認ゲートの確認 |

| テーブル | 対象 | 参考ドキュメント |
|---------|------|-----------------|
| `containerserviceeventresources` | AKSクラスター単体 | [AKS Communication Manager](https://learn.microsoft.com/en-us/azure/aks/aks-communication-manager) |
| `aksresources` (updateruns) | Fleet Manager | [Fleet Update Runs Monitoring](https://learn.microsoft.com/en-us/azure/kubernetes-fleet/howto-monitor-update-runs) |
| `aksresources` (gates) | Fleet Manager承認ゲート | [Fleet Approval Gates](https://learn.microsoft.com/en-us/azure/kubernetes-fleet/update-strategies-gates-approvals) |

## 照会例

### 対象の特定

名前だけで照会先を決めず、依頼されたsubscriptionとresource groupを指定して完全なリソースIDを取得する。完全なIDが提供されている場合はそれを使う。

#### AKSクラスターの場合

```bash
# クラスターのリソースIDを確認
az aks show --subscription <subscription-id> \
  -g <resource-group> -n <cluster-name> --query id -o tsv
```

#### Fleet Managerの場合

```bash
# FleetのリソースIDを確認
az fleet show --subscription <subscription-id> \
  -g <resource-group> -n <fleet-name> --query id -o tsv
```

### Resource Graphクエリ

`<cluster-resource-id>`と`<fleet-resource-id>`を取得済みの完全なIDへ置き換える。子リソースの区切りまで含むprefixで対象を限定し、同名リソースとの取り違えを避ける。`--first`は1回の取得上限であり、全履歴の取得を保証しない。

#### AKSクラスター単体のイベント取得

```bash
az graph query --subscriptions <subscription-id> -q "
containerserviceeventresources
| where type == 'microsoft.containerservice/managedclusters/scheduledevents'
| where id startswith '<cluster-resource-id>/scheduledEvents/'
| extend status = tostring(properties.eventStatus)
| extend upgradeType = case(
    tostring(properties.eventDetails) has 'K8sVersionUpgrade', 'K8sVersionUpgrade',
    tostring(properties.eventDetails) has 'NodeOSUpgrade', 'NodeOSUpgrade',
    'Unknown'
)
| extend notificationTime = todatetime(properties.scheduledTime)
| extend startTime = todatetime(properties.startTime)
| extend lastUpdateTime = todatetime(properties.lastUpdateTime)
| extend eventId = tostring(properties.eventId)
| extend hoursUntilStart = datetime_diff('hour', startTime, notificationTime)
| extend notificationType = case(
    status != 'Scheduled', '',
    hoursUntilStart >= 144, '7日前通知',
    hoursUntilStart >= 20, '24時間前通知',
    '直前通知'
)
| project
    id,
    clusterName = tostring(split(id, '/')[8]),
    lastUpdateTime,
    startTime,
    notificationTime,
    notificationType,
    upgradeType,
    status,
    eventId
| order by lastUpdateTime desc
" --first 50
```

#### Fleet Managerのアップグレードイベント取得

```bash
az graph query --subscriptions <subscription-id> -q "
aksresources
| where type == 'microsoft.containerservice/fleets/updateruns'
| where id startswith '<fleet-resource-id>/updateRuns/'
| extend parsedProps = parse_json(properties)
| extend state = tostring(parsedProps.status.status.state)
| extend startTime = todatetime(parsedProps.status.status.startTime)
| extend completedTime = todatetime(parsedProps.status.status.completedTime)
| extend upgradeType = tostring(parsedProps.managedClusterUpdate.upgrade.type)
| project
    id,
    name,
    state,
    startTime,
    completedTime,
    upgradeType
| order by startTime desc
" --first 20
```

#### Fleet Managerの承認待ちゲート取得（Fleet確認時は必須）

承認待ちの判断規則は [SKILL.md](../SKILL.md#状態の判断) に従う。

```bash
az graph query --subscriptions <subscription-id> -q "
aksresources
| where type == 'microsoft.containerservice/fleets/gates'
| where id startswith '<fleet-resource-id>/gates/'
| extend gateType = tostring(properties.gateType)
| extend gateState = tostring(properties.state)
| extend targetId = tostring(properties.target.id)
| extend displayName = tostring(properties.displayName)
| extend updateRunName = tostring(properties.target.updateRunProperties.name)
| extend stageName = tostring(properties.target.updateRunProperties.stage)
| extend groupName = tostring(properties.target.updateRunProperties.group)
| extend timing = tostring(properties.target.updateRunProperties.timing)
| where gateType == 'Approval' and gateState == 'Pending'
| project
    id,
    gateName = name,
    displayName,
    updateRunName,
    stageName,
    groupName,
    timing,
    gateState,
    targetId
| order by updateRunName asc, stageName asc, groupName asc
" --first 50
```

Resource Graphで詳細が不足する場合は、Fleet CLIでも確認する。

```bash
az fleet gate list \
  --subscription <subscription-id> \
  --resource-group <resource-group> \
  --fleet-name <fleet-name> \
  --state Pending
```

#### Fleet Managerのステージとメンバー詳細取得

実行中、保留中、または失敗したアップグレードの詳細を確認する場合:

```bash
az graph query --subscriptions <subscription-id> -q "
aksresources
| where type == 'microsoft.containerservice/fleets/updateruns'
| where id startswith '<fleet-resource-id>/updateRuns/'
| where name == '<update-run-name>'
| extend parsedProps = parse_json(properties)
| mv-expand stages = parsedProps.status.stages
| mv-expand groups = stages.groups
| mv-expand members = groups.members
| project
    id,
    stageName = tostring(stages.name),
    stageState = tostring(stages.status.state),
    groupName = tostring(groups.name),
    groupState = tostring(groups.status.state),
    memberName = tostring(members.name),
    memberState = tostring(members.status.state),
    memberCluster = tostring(split(members.clusterResourceId, '/')[8]),
    memberMessage = tostring(members.message)
" --first 50
```

複数クラスターやFleet全体を依頼された場合も、承認されたsubscriptionとresource groupの範囲を維持する。上記クエリを対象IDごとに使うか、指定範囲だけのID集合で絞る。

## 表示例

詳細な履歴を求められた場合の例。対象と依頼に関係する節だけを使い、取得できなかった項目は0件に置き換えない。件数は取得したレコード数であることを示す。

```markdown
## AKS メンテナンスイベント確認結果

**確認日時:** YYYY-MM-DD HH:MM UTC
**対象ID:** <full-resource-id>
**取得範囲:** <対象期間、取得件数、上限、未取得ページの有無>

---

### 🔷 AKSクラスター単体のイベント

**クラスター:** <cluster-name>  
**リソースグループ:** <resource-group>

#### イベントサマリー

| 状態 | 件数 |
|------|------|
| 🗓️ Scheduled（予定通知） | X |
| ▶️ Started（実行中） | X |
| ✅ Completed（完了） | X |
| ❌ Failed（失敗） | X |
| 🚫 Canceled（キャンセル） | X |

#### 予定されているメンテナンス

| メンテナンス開始予定 | タイプ | 通知タイプ | 通知日時 | eventId |
|--------------------|--------|-----------|---------|---------|
| YYYY-MM-DD HH:MM UTC | NodeOSUpgrade | 7日前通知 | YYYY-MM-DD HH:MM UTC | xxx-xxx |
| YYYY-MM-DD HH:MM UTC | NodeOSUpgrade | 24時間前通知 | YYYY-MM-DD HH:MM UTC | xxx-xxx |

#### 最近のメンテナンス履歴

| 実行日時 | タイプ | 状態 |
|---------|--------|------|
| YYYY-MM-DD HH:MM UTC | NodeOSUpgrade | ✅ Completed |

---

### 🔶 Fleet Managerアップグレードイベント

**Fleet:** <fleet-name>  
**リソースグループ:** <resource-group>

#### アップグレード実行サマリー

| 状態 | 件数 |
|------|------|
| ⏳ NotStarted | X |
| 🟡 Pending（保留中） | X |
| ▶️ Running | X |
| ⏸️ Stopping | X |
| ⏹️ Stopped | X |
| ✅ Completed | X |
| ❌ Failed | X |

#### 承認待ち（要対応）

| Gate名 | 表示名 | Update Run | ステージ | グループ | タイミング | 状態 |
|--------|--------|------------|----------|----------|------------|------|
| aaaa0a0a-... | Do not start during business hours! | run-XXX | prod | canary | Before | 🟡 Pending |

#### 進行中または保留中のアップグレード（詳細）

**名前:** run-XXXXXXXX  
**タイプ:** NodeImageOnly  
**開始日時:** YYYY-MM-DD HH:MM UTC

| ステージ | グループ | クラスター | 状態 |
|---------|---------|----------|------|
| prod | canary | aks-web-01 | ✅ Completed |
| prod | apac | aks-app-01 | ▶️ Running |

#### 最近のアップグレード履歴

| 名前 | タイプ | 開始日時 | 完了日時 | 状態 |
|------|--------|---------|---------|------|
| run-XXX | NodeImageOnly | ... | ... | ✅ Completed |
```

## リファレンス

### AKSクラスター イベントステータス

| ステータス | 絵文字 | 説明 |
|-----------|--------|------|
| Scheduled | 🗓️ | メンテナンスが予定されている（事前通知） |
| Started | ▶️ | メンテナンスが開始された |
| Completed | ✅ | メンテナンスが正常に完了した |
| Failed | ❌ | メンテナンスが失敗した |
| Canceled | 🚫 | メンテナンスがキャンセルされた |

### AKSクラスター 通知タイプ（Scheduled イベントのみ）

| 通知タイプ | 説明 | 判定条件 |
|-----------|------|---------|
| 7日前通知 | メンテナンス開始の約7日前に送信される事前通知 | `hoursUntilStart >= 144時間` |
| 24時間前通知 | メンテナンス開始の約24時間前に送信される事前通知 | `hoursUntilStart >= 20時間 かつ < 144時間` |
| 直前通知 | メンテナンス開始直前の通知 | `hoursUntilStart < 20時間` |

> **重要:** `scheduledTime` は通知レコードが作成された日時、`startTime` は実際のメンテナンス開始予定日時です。同じ `eventId` を持つ複数のScheduledレコードは、同一メンテナンスに対する複数回の事前通知を表します。

### AKSクラスター アップグレードタイプ

| タイプ | 説明 |
|--------|------|
| K8sVersionUpgrade | Kubernetesバージョンのアップグレード |
| NodeOSUpgrade | ノードOSのセキュリティパッチ適用 |

### Fleet Manager イベントステータス

| ステータス | 絵文字 | 説明 |
|-----------|--------|------|
| NotStarted | ⏳ | アップグレード実行がまだ開始されていない |
| Pending | 🟡 | アップグレード実行が保留中。承認待ち、バージョン提供待ち、検証待ちなどの理由をゲート/ステージ/メンバー詳細で確認する |
| Running | ▶️ | アップグレード実行が進行中 |
| Stopping | ⏸️ | アップグレード実行が停止処理中 |
| Stopped | ⏹️ | アップグレード実行が停止された |
| Completed | ✅ | アップグレード実行が正常に完了した |
| Failed | ❌ | アップグレード実行が失敗した |

### Fleet Manager 承認ゲートステータス

| ステータス | 絵文字 | 説明 |
|-----------|--------|------|
| NotStarted | ⏳ | Update Runがまだゲートに到達していない |
| Pending | 🟡 | 承認待ち。Update Runは承認されるまで先へ進まない |
| Skipped | ⏭️ | ステージまたはグループがスキップされたためゲートもスキップされた |
| Completed | ✅ | 承認済み |

### Fleet Manager アップグレードタイプ

| タイプ | 説明 |
|--------|------|
| Full | Kubernetesバージョン + ノードイメージの両方をアップグレード |
| NodeImageOnly | ノードイメージのみをアップグレード |
| ControlPlaneOnly | コントロールプレーンのみをアップグレード |

## 取得範囲とデータの制限

- AKSクラスターのイベントは、自動アップグレードが有効かつメンテナンスウィンドウが設定されている場合に記録される
- Fleet Managerのデータは、Fleetリソースが存在するサブスクリプションのResource Graphに格納される
- イベントデータは一定期間後に削除される可能性がある
- 期間を限定する場合は、開始予定日時と通知日時のどちらを条件にしたかを記録する
- 応答の件数、取得上限、継続トークンや未取得ページを確認する。追加取得が必要なら同じ対象と期間を維持する。取得範囲や保持期間に制限がある結果を全履歴と呼ばない
- 照会失敗、権限不足、不完全な応答は未確認とする。成功した0件の応答は「取得範囲内に該当レコードなし」と報告する
- **クエリ構文の違い:**
  - `az graph query` CLI: テーブル名のみ（例: `containerserviceeventresources`）
  - Azure Monitor アラートルール: `arg("").` プレフィックスが必要（例: `arg("").containerserviceeventresources`）
