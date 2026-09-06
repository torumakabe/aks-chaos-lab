---
name: aks-maintenance-checker
description: AKSクラスターおよびFleet Managerのメンテナンス状況（予定、実行中、承認待ち、完了、失敗）を読み取り専用で確認。「メンテナンス状況」「アップグレード履歴」「Fleet更新状態」「承認待ち」を求める場合に使用。
---

# AKS Maintenance Events Checker

AKSのスケジュールイベント、FleetのUpdate Run、承認ゲートを照会し、現在状態と取得範囲内の履歴を報告する。確認依頼は読み取り専用で扱い、ゲート承認、アップグレード開始、設定変更は行わない。

## 実行手順

1. 依頼対象のsubscription、resource group、クラスターまたはFleetを特定する。完全なリソースIDが分かる場合はそれを使い、同名資源を取り違えない。対象が決まっていれば照会の承認を再確認せず、依頼範囲を越える探索は行わない。
2. [照会例](references/queries.md#照会例)を読み、必要なクエリだけを実行する。AKSは`containerserviceeventresources`、Fleetは`aksresources`のUpdate Runと承認ゲートを調べる。Fleet確認時はpending gateの照会を省略しない。
3. 実行中、保留中、失敗などの理由に必要な場合だけ、ステージ、グループ、メンバーの詳細を取得する。
4. 照会成否、取得件数、期間、上限、未取得ページを確認する。失敗や不完全な結果を0件と扱わず、取得範囲を超えて「履歴なし」「承認待ちなし」と断定しない。
5. 下記の判断規則で分類し、対象ID、確認日時、結論、根拠、取得上の制限を返す。時刻にはタイムゾーンを付ける。

## 状態の判断

- `gateType == 'Approval'`かつゲートの`state == 'Pending'`を確認できた場合は、「承認待ち（要対応）」として現在状態の先頭に示す。確認は承認操作を含まない。
- Update Runの`Pending`だけでは承認待ちと判断しない。ゲート、ステージ、グループ、メンバーの状態とメッセージから理由を説明し、ゲート照会に失敗した場合は承認待ちの有無を未確認とする。
- Fleetの`NotStarted`、`Running`、`Pending`、`Stopping`は現在状態、`Completed`、`Failed`、`Stopped`は最近の履歴として分ける。
- 成功した0件の照会は「取得範囲内に該当レコードなし」とする。照会失敗とは区別する。

状態名、通知時刻、同一イベントの複数通知の解釈は[リファレンス](references/queries.md#リファレンス)を参照する。詳細な履歴を求められた場合は[表示例](references/queries.md#表示例)を使い、該当しない節や空の表は出力しない。

## 前提条件と対象外

Azure CLIの認証と対象を読める権限（Readerなど）、`resource-graph`拡張機能が必要。Fleet CLIを使う場合は`fleet`拡張機能も必要になる。不足時は制約を示し、確認依頼だけでインストールや認証設定の変更を行わない。

メンテナンス設定そのものの確認はイベント照会とは別で、`az aks maintenanceconfiguration list`を使う。設定変更やFleet更新戦略の変更は、このスキルの対象外とする。
