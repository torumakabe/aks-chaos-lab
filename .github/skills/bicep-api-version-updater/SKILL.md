---
name: bicep-api-version-updater
description: BicepファイルのAzureリソースAPIバージョンを確認または更新する。確認依頼とreview-repoでは公開情報だけを使うcheck-only、明示された更新依頼ではupdateを使う。「APIバージョンを更新」「Bicepを最新化」「古いAPIバージョンをチェック」を求める場合に使用。
---

# Bicep API Version Updater

BicepファイルのAzureリソースAPIバージョンを確認し、更新依頼では互換性とローカル検証を満たす安定版へ更新する。確認だけの依頼を更新へ広げない。

## check-onlyモード

手動の確認依頼、および`review-repo`または`bicep-api-version-check` workflowからの呼び出しでは、この節だけを実行する。worktreeは読み取り専用として扱い、Azure認証を行わない。subscriptionを照会しない。

1. repo health inventory JSONが渡された場合は、schema version、対象commit、Bicep resource API座標を記録する。解釈できないschemaまたは座標は`unverified`とする。inventoryのない手動確認では、依頼対象のBicepを読み、ファイル、リソースタイプ、現在のAPI versionを抽出する。存在しないinventoryのschemaやcommitを推測しない。
2. Microsoft Learnの公開APIリファレンスとbreaking changesを文書検索、取得ツールで確認する。公開候補の確認にはAzure公式`Azure/azure-rest-api-specs` repositoryの対応するstableまたはpreview仕様も参照する。定期workflowでは、許可された`learn.microsoft.com`とGitHub APIをPython標準ライブラリで取得する。
3. 現在のversion、公開されている安定版、プレビュー版からGAへ移行できるかを比較する。
4. 結果を`pass`、`fail`、`unverified`、`excluded`へ分類する。Microsoft Learnと`Azure/azure-rest-api-specs`のどちらからも公開情報を取得できない対象は`unverified`とする。両者のversion一覧が異なる場合は各確認結果を示し、反映時刻の差は確認できた場合だけ説明する。
5. 対象座標数、確認済み座標数、未検証座標数、除外座標数からcoverageを計算し、入力の出所、根拠URL、確認時刻とともに返す。
6. 結果を返して終了する。後続のupdateモードへ進まない。

check-onlyモードでは、ローカルまたはAzure上の構成を変更する操作を行わない。現在値が公開情報と異なる場合も、影響と確認事項だけを報告する。

## updateモード

ユーザーの明示承認がある場合に実行する。「APIバージョンを更新して」のように対象範囲が決まった更新依頼は、その範囲の編集承認として扱い、再確認しない。Azure CLIによる照会、Bicepのversion変更、ローカル検証までを扱い、デプロイは行わない。

Azure認証と対象subscriptionが必要な照会では、既存の認証と指定対象を使う。認証がない、対象を特定できない、権限が不足する場合は制約を報告する。別subscriptionへの照会や認証設定の変更へ勝手に広げず、公開情報で確認できる範囲と未確認事項を分ける。

### 使用するツール

| ツール | 用途 |
|------|------|
| `az provider show` | 対象subscriptionで提供されるAPI versionの確認 |
| 公開文書の検索、取得ツール | API仕様とbreaking changesの確認 |
| `scripts/tasks.py` の `build-bicep` | 構文とLinterの検証 |
| ファイル検索、閲覧ツール | 対象Bicepと既存変更の確認 |
| ファイル編集ツール | 承認範囲のAPI version変更 |

#### API versionの情報源

Azure MCP Serverの`bicepschema`と`az provider show`では、APIバージョンの取得元が異なる。

| ツール | データソース | 更新タイミング |
|--------|-------------|---------------|
| `bicepschema` | Bicep CLIに同梱された型定義（[Azure/bicep-types-az](https://github.com/Azure/bicep-types-az)） | Bicep CLIのリリース時 |
| `az provider show` | Azureリソースプロバイダーへの問い合わせ | 照会時 |

型定義の対応状況だけで公開候補の新しさを判断しない。`az provider show`と公開API仕様を照合する。

`az provider show` はリソースプロバイダーに登録されているリソースタイプのみを返す。一部の子リソース（例: `redisEnterprise/databases/accessPolicyAssignments`）は登録されていない場合があり、その場合はAPIリファレンスを参照する必要がある。

### 実行手順

#### Step 1: リソースタイプとAPIバージョンの抽出

ファイル検索、閲覧ツールで依頼対象のresource宣言を読む。更新前のAPI versionとユーザーの既存変更を記録する。

> **対象外:** `resourceInput<'Type@version'>` / `resourceOutput<'Type@version'>` 構文（Bicep 0.34.1以降）は本スキルの対象外。これらは型定義用でありリソースをデプロイしないため、APIバージョン更新の優先度が異なる。

#### Step 2: プレビュー版のスキップ判定

APIバージョンに `-preview` が含まれる場合は**更新をスキップ**。

**ただし、以下の分析は必ず実施すること:**

1. 現在のBicepファイルで使用している属性を特定
2. Step 3の経路で最新GA版を確認
3. コードの属性がGA版でサポートされているか確認（ドキュメント検索）
4. GA移行可否と、判断に関わる属性を報告する

#### Step 3: 最新GA版の取得と比較

##### 3-1. 最新GA版の取得

```bash
az provider show --subscription <subscription-id> -n Microsoft.Network \
  --query "resourceTypes[?resourceType=='virtualNetworks'].apiVersions[]" \
  -o json
```

返された一覧の順序には依存せず、`-preview` を除いたversionを比較する。CLIの終了コードとエラーを確認し、照会失敗をGA版なしと扱わない。

##### 3-2. 比較と判断

- 現在のバージョン == 最新GA版 → スキップ（既に最新）
- 現在のバージョン < 最新GA版 → Step 4へ
- GA版なし、または取得結果を確認できない → 変更せず理由を報告
- 現在のバージョンが取得候補より新しい → 自動で下げず、情報源の差を報告

##### 3-3. フォールバック: APIリファレンス参照

`az provider show`の成功結果が空の場合や照会できない場合は、公開APIリファレンスと`Azure/azure-rest-api-specs`を参照する。照会失敗の理由は残す。

```
URL形式:
https://learn.microsoft.com/en-us/azure/templates/{provider}/{resourceType}

例:
https://learn.microsoft.com/en-us/azure/templates/microsoft.cache/redisenterprise/databases/accesspolicyassignments
```

APIリファレンスページの上部に利用可能なAPIバージョン一覧が表示される。`-preview` を含まない最新バージョンを選択する。

#### Step 4: Breaking Changes確認

`microsoft_docs_search` で破壊的変更を検索:

```
検索クエリ例:
- "Microsoft.ContainerService managedClusters API breaking changes"
```

参考リンク:
- AKS: https://aka.ms/aks/breakingchanges

#### Step 5: APIバージョン更新（仮適用）

使用中の属性とbreaking changesを確認する。仮適用する変更がある場合は、Step 6の検証を変更前にも実行して診断を記録し、対象resource宣言のAPI versionだけを編集する。この時点では仮適用とする。

#### Step 6: Linter検証と更新確定

```bash
uv run --no-project "${PWD}/scripts/tasks.py" build-bicep
```

リポジトリルートから実行し、変更前後の診断を比較する。

##### 更新によりエラーまたは警告が増えた場合

1. 問題を生じた自分のAPI version変更だけを更新前へ戻し、ユーザーの既存変更を保持する。ファイル全体の復元は行わない。
2. 同じ検証で、新たに生じた診断が解消したか確認する。
3. 対象、診断、戻した変更をスキップ理由として報告する。既存警告は今回の更新による警告と分ける。

BCP081など型定義の未対応による警告では、更新後のプロパティ検証ができない。対応する型定義が利用できるまで元のversionを維持する。

**禁止:** 最新GA版でBCP081警告が出た場合に、警告が出ない別のバージョンへ変更すること。元のバージョンに戻すのみ許可。

既存の`#disable-next-line BCP081`で意図的に警告を抑制しているリソースは、仕様を確認したうえで更新できる。今回の更新を通すための抑制追加は行わない。

##### 検証できた場合

buildが成功し、新規警告がなければ更新を確定する。ツール不足や既存エラーなどで検証を完了できない場合は、検証済みと扱わず、仮適用したversion変更だけを戻して制約を報告する。

### 報告

依頼範囲の全リソースについて、ファイル、リソースタイプ、変更前後のversion、更新またはスキップの理由、検証結果を報告する。複数ある場合は表にまとめる。プレビュー版が存在する場合はGA移行可否と、その判断に関わる属性を含める。取得不能や互換性未確認は、移行可能と扱わない。

ローカル検証にはuvとBicep CLIを使う既存task環境が必要になる。不足を理由に別の検証経路を追加しない。

### このスキルを使わない場合

- 「特定のプレビュー機能を使いたい」（意図的なプレビュー使用）
- 「APIバージョンを固定したい」（安定性優先）
