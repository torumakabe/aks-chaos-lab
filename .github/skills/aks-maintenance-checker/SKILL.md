---
name: aks-maintenance-checker
description: AKSクラスターおよびFleet Managerのメンテナンス状況（予定・実行中・承認待ち・完了・失敗）を確認。「メンテナンス状況」「アップグレード履歴」「Fleet更新状態」「承認待ち」を求める場合に使用。
---

# AKS Maintenance Events Checker

AKSクラスターおよびAzure Kubernetes Fleet Managerの実際のメンテナンスイベントを確認する。

**対象:**
- AKSクラスター単体のスケジュールイベント（自動アップグレード、ノードOSアップグレード）
- Fleet Managerのアップグレード実行（Update Runs）
- Fleet Managerの承認待ちゲート（Approval Gates）

**注意:** メンテナンス「設定」ではなく、実際にスケジュールされた・実行されたイベントを対象とする。設定の確認には `az aks maintenanceconfiguration list` を使用する。

## Tools

| Tool | 用途 |
|------|------|
| `az graph query` | Azure Resource Graphからイベント情報を取得 |
| `az aks show` | クラスター情報の確認（リソースID取得） |
| `az fleet show` | Fleet情報の確認（リソースID取得） |
| `az fleet gate list` | Fleet承認ゲートの確認 |

## データソース

| テーブル | 対象 | 参考ドキュメント |
|---------|------|-----------------|
| `containerserviceeventresources` | AKSクラスター単体 | [AKS Communication Manager](https://learn.microsoft.com/en-us/azure/aks/aks-communication-manager) |
| `aksresources` (updateruns) | Fleet Manager | [Fleet Update Runs Monitoring](https://learn.microsoft.com/en-us/azure/kubernetes-fleet/howto-monitor-update-runs) |
| `aksresources` (gates) | Fleet Manager承認ゲート | [Fleet Approval Gates](https://learn.microsoft.com/en-us/azure/kubernetes-fleet/update-strategies-gates-approvals) |

## 実行フロー

```
1. 対象の特定
   - クラスター名/Fleet名からリソースIDを取得
   ↓
2. Resource Graphクエリ実行
   - AKSクラスター: containerserviceeventresources
   - Fleet Manager: aksresources (updateruns)
   - Fleet承認待ち: aksresources (gates) または az fleet gate list
   ↓
3. 結果の解釈と説明
   - イベントステータスごとに分類
   - Fleet承認待ちは履歴ではなく要対応として分離
   - タイムライン形式で表示
```

## 実行手順

### Step 1: 対象の特定

#### AKSクラスターの場合

```bash
# クラスターのリソースIDを確認
az aks show -g <resource-group> -n <cluster-name> --query id -o tsv
```

#### Fleet Managerの場合

```bash
# FleetのリソースIDを確認
az fleet show -g <resource-group> -n <fleet-name> --query id -o tsv
```

### Step 2: Resource Graphクエリの実行

#### AKSクラスター単体のイベント取得

```bash
az graph query -q "
containerserviceeventresources
| where type == 'microsoft.containerservice/managedclusters/scheduledevents'
| where id contains '/subscriptions/<subscription-id>/resourcegroups/<resource-group>/providers/Microsoft.ContainerService/managedClusters/<cluster-name>'
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

**フィルタなしで全クラスターのイベントを取得する場合:**

```bash
az graph query -q "
containerserviceeventresources
| where type == 'microsoft.containerservice/managedclusters/scheduledevents'
| extend clusterName = tostring(split(id, '/')[8])
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
| project clusterName, lastUpdateTime, startTime, notificationType, upgradeType, status, eventId
| order by lastUpdateTime desc
" --first 50
```

#### Fleet Managerのアップグレードイベント取得

```bash
az graph query -q "
aksresources
| where type == 'microsoft.containerservice/fleets/updateruns'
| where id contains '/Microsoft.ContainerService/fleets/<fleet-name>'
| extend parsedProps = parse_json(properties)
| extend state = tostring(parsedProps.status.status.state)
| extend startTime = todatetime(parsedProps.status.status.startTime)
| extend completedTime = todatetime(parsedProps.status.status.completedTime)
| extend upgradeType = tostring(parsedProps.managedClusterUpdate.upgrade.type)
| project
    name,
    state,
    startTime,
    completedTime,
    upgradeType
| order by startTime desc
" --first 20
```

#### Fleet Managerの承認待ちゲート取得（Fleet確認時は必須）

承認ゲートが `Pending` の場合、Update Run の履歴ではなく「承認待ち（要対応）」として出力する。

```bash
az graph query -q "
aksresources
| where type == 'microsoft.containerservice/fleets/gates'
| where id contains '/Microsoft.ContainerService/fleets/<fleet-name>/gates/'
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
  --resource-group <resource-group> \
  --fleet-name <fleet-name> \
  --state Pending
```

**フィルタなしで全Fleetのイベントを取得する場合:**

```bash
az graph query -q "
aksresources
| where type == 'microsoft.containerservice/fleets/updateruns'
| extend fleetName = tostring(split(id, '/')[8])
| extend parsedProps = parse_json(properties)
| extend state = tostring(parsedProps.status.status.state)
| extend startTime = todatetime(parsedProps.status.status.startTime)
| extend upgradeType = tostring(parsedProps.managedClusterUpdate.upgrade.type)
| project fleetName, name, state, startTime, upgradeType
| order by startTime desc
" --first 20
```

#### Fleet Managerのステージ・メンバー詳細取得

実行中、保留中、または失敗したアップグレードの詳細を確認する場合:

```bash
az graph query -q "
aksresources
| where type == 'microsoft.containerservice/fleets/updateruns'
| where id contains '/Microsoft.ContainerService/fleets/<fleet-name>'
| where name == '<update-run-name>'
| extend parsedProps = parse_json(properties)
| mv-expand stages = parsedProps.status.stages
| mv-expand groups = stages.groups
| mv-expand members = groups.members
| project
    stageName = tostring(stages.name),
    stageState = tostring(stages.status.state),
    groupName = tostring(groups.name),
    groupState = tostring(groups.status.state),
    memberName = tostring(members.name),
    memberState = tostring(members.status.state),
    memberCluster = tostring(split(members.clusterResourceId, '/')[8]),
    memberMessage = tostring(members.message)
"
```

### Step 3: 結果の解釈

クエリ結果をもとに、以下の形式で報告する。

Fleet Managerの分類ルール:

- `microsoft.containerservice/fleets/gates` に `gateType == 'Approval'` かつ `gateState == 'Pending'` がある場合は、最優先で「承認待ち（要対応）」に出力する。
- Update Runの `state == 'Pending'` は承認待ちとは限らない。承認ゲートの有無、ステージ/グループ/メンバーの状態とメッセージを確認して理由を説明する。
- 「最近のアップグレード履歴」には原則として終端状態（`Completed`, `Failed`, `Stopped`）のみを載せる。`NotStarted`, `Running`, `Pending`, `Stopping` は履歴ではなく現在状態として扱う。

## 出力フォーマット（必須）

以下のセクションを**必ず**含めること:

1. **確認対象と日時**
2. **AKSクラスターイベント**（該当する場合）
3. **Fleet Managerイベント**（該当する場合）

### テンプレート

```markdown
## AKS メンテナンスイベント確認結果

**確認日時:** YYYY-MM-DD HH:MM UTC

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

## 前提条件

- Azure CLIでログイン済み（`az login`）
- `resource-graph` 拡張機能がインストール済み（`az extension add --name resource-graph`）
- サブスクリプションに対する **Reader** ロール
- Fleet Managerを使用する場合は `fleet` 拡張機能がインストール済み（`az extension add --name fleet`）

## このスキルを使わない場合

- メンテナンス「設定」を確認したい → `az aks maintenanceconfiguration list` を使用
- メンテナンス設定を変更したい → `az aks maintenanceconfiguration add/update` を使用
- Fleet更新戦略を設定したい → `az fleet updatestrategy` を使用

## 注意事項

- AKSクラスターのイベントは、自動アップグレードが有効かつメンテナンスウィンドウが設定されている場合に記録される
- Fleet Managerのデータは、Fleetリソースが存在するサブスクリプションのResource Graphに格納される
- Fleet Managerの承認待ちはUpdate Run履歴だけでは見落としやすい。`microsoft.containerservice/fleets/gates` または `az fleet gate list --state Pending` を必ず確認する
- Update Runの `Pending` と承認ゲートの `Pending` は意味が異なる。承認待ちとして断定するのは承認ゲートが `Pending` の場合のみ
- イベントデータは一定期間後に削除される可能性がある
- **クエリ構文の違い:**
  - `az graph query` CLI: テーブル名のみ（例: `containerserviceeventresources`）
  - Azure Monitor アラートルール: `arg("").` プレフィックスが必要（例: `arg("").containerserviceeventresources`）
