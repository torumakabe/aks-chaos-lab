# Azure SRE Agent の運用

## 配備時に完了する設定

`AZURE_SRE_AGENT_ENABLED=true` で base を配備すると、Agent、診断 connector、RBAC、専用 subnet、VNet 統合、private Key Vault、Private Endpoint、Private DNS、監査ログを作成する。postprovision hook はルート `README.md` を `aks-chaos-lab-readme.md` としてナレッジに登録し、インデックス状態を確認する。

Azure Monitor incident integration は、Agent の user-assigned managed identity に subscription scope の Monitoring Contributor を付与するため別途 opt-in とする。利用時は `AZURE_SRE_AGENT_AZURE_MONITOR_INCIDENTS_ENABLED=true` を設定する。Review response plan は SLI 短窓アラート用の1件とし、15分窓の Availability / Latency alert を対象とする。条件と権限は[環境構築手順](deployment.md#azure-sre-agent-任意)を参照する。

`azd up` と段階的な `azd provision base` の両方で同じ hook が動く。`azd deploy` はサービスだけを更新し、GitHub の準備や入力を要求しない。GitHub の後日設定は次節の独立した操作で行う。鍵の生成、GitHub App の作成、鍵の import を配備 hook 内では行わない。

## GitHub App を後から接続する

**GitHub 連携は任意であり、環境作成や SRE Agent の利用に必須ではない。** GitHub App と鍵を用意しなくても、Azure リソースの診断、README ナレッジの利用、別途有効にした Azure Monitor incident integration を使える。本節は GitHub のコードを診断対象に加える場合だけ実行する。通常の配備ごとに繰り返す必要はない。

旧検証環境では、AKS の専用 Pod と専用 identity で private Vault に接続し、Agent の署名、repository の clone、README の読み取りまで成功した。現在の環境でも、以下の一括実行スクリプトによる秘密鍵登録と一時リソースの撤去は、利用者の実行で成功報告を受けている。現在の環境での Agent identity と private IP による署名の監査確認は未完了である。

### App の準備

既存 App と有効な秘密鍵がある場合は再利用する。以下の設定と installation を確認し、必要な場合だけ新規作成や鍵の再発行を行う。

1. GitHub の Settings → Developer settings → GitHub Apps で対象 App を開く。新規作成する場合は App 名を34文字以内にする。
2. Repository permissions は Contents と Metadata を Read-only にし、Webhook は無効にする。installation の対象を必要なアカウントに限定する。
3. Install App で対象アカウントを選び、Only select repositories で `aks-chaos-lab` を選んでインストールする。App の作成だけでは repository の利用許可は完了しない。
4. 有効な PEM がなければ App の Edit → Credentials → Key pairs → New key から取得する。Client ID（`Iv...`）も控える。Client secret は不要。「Expire user authorization tokens」は既定の有効のままでよい。本構成は installation token を使う。

PEM を Git、チャット、deployment parameter、Kubernetes Secret に保存しない。権限を広げるときは、Code Access で必要な権限と Issue / PR 操作用の connector を区別する。本手順はコードの読み取りだけを構成する。

### private Key Vault に Key を import する

`azd env get-value AZURE_SRE_AGENT_VAULT_NAME -e "<env>"` で Vault 名を確認する。作業には、Vault の private endpoint を名前解決して接続できる実行元と、import を許可された identity が必要になる。インターネット上の端末からの直接接続や trusted services bypass は利用できない。

VNet に接続済みで Azure CLI のある管理端末では、管理者が作業 identity に対象 Vault scope の **Key Vault Crypto Officer** を一時的に割り当てて、次を実行する。Azure CLI のサブスクリプションとログイン identity が対象環境のものか確認してから操作する。

```bash
az keyvault key import \
  --vault-name "<AZURE_SRE_AGENT_VAULT_NAME>" \
  --name sre-agent-github-app-key \
  --pem-file "/安全に保管したパス/app.private-key.pem" \
  --ops sign verify \
  --query key.kid -o tsv
```

Vault へは **Secret ではなく RSA Key として import** する。Agent の system-assigned identity は IaC で付与した Crypto User により署名を依頼する。Secret の値を読み出す権限は不要。URI は `https://<vault>.vault.azure.net/keys/sre-agent-github-app-key` で、version を省略すると現在の Key を参照する。

VNet 接続済み端末がない場合は、次の AKS 用手順を使う。既存アプリの identity は使わず、専用 identity と専用 namespace を一時作成する。PEM は `kubectl exec -i` の標準入力で Pod 内の memory emptyDir に転送し、Azure CLI で import する。`az aks command invoke --file` で PEM を送らない。

#### 手元の端末から既存 AKS を使って import する

リポジトリルートで、Bash、Azure CLI、azd、kubectl、kubelogin を用意し、Azure CLI と azd にログインする。実行 identity には、対象 AKS の user credentials 取得、namespace / Pod の作成と削除、対象 RG の Managed Identity の作成と削除、対象 Vault の role assignment の作成と削除権限が必要になる。Azure CLI コンテナを公開 registry から取得する。Windows では WSL または Git Bash で実行する。

##### 1. 登録コマンドを実行する

`<env>` を対象環境名に置き換える。親シェルが zsh でも、明示的に Bash でスクリプトを実行する。`source` や、スクリプトの中身の分割実行はしない。同じ環境で並行実行しない。

```bash
bash scripts/import-sre-agent-github-key.sh "<env>"
```

スクリプトは環境情報を azd から毎回取得する。既存アプリの identity を使わず、`sre-github-key-import` という専用 namespace と Managed Identity を一時作成し、Pod の Ready、認証、Vault の Key 一覧取得を確認する。これらが失敗した場合は秘密鍵入力を開始しない。public access は変更しない。

認証と Vault の Key 一覧取得は、それぞれ最大12回、10秒間隔で再試行する。試行中は待機状況を表示し、成功時には各確認の成功を表示する。Azure CLI の詳細エラーは最終的に失敗した場合だけ表示する。一時 identity の認証確認中に、手元の Azure CLI をログアウトして再ログインする必要はない。

##### 2. 秘密鍵を非表示入力に貼り付ける

入力するのは **パスフレーズなしの RSA 秘密鍵を PEM で表したもの**で、次の2形式を受け付ける。1Password などの信頼できるツールで秘密鍵を管理している場合は、取り出す際に PKCS#1 または PKCS#8 の PEM を選択し、BEGIN 行で形式を確認する。PKCS#8 でも、RSA 以外の鍵は対象外である。

各行は、表中の文字列の前後にハイフン5個ずつを付けた形式になる。

| 形式 | BEGIN 行の文字列 | 対応する END 行の文字列 |
| --- | --- | --- |
| PKCS#1（RSA 秘密鍵、非暗号化） | `BEGIN RSA PRIVATE KEY` | `END RSA PRIVATE KEY` |
| PKCS#8（RSA 秘密鍵、非暗号化） | `BEGIN PRIVATE KEY` | `END PRIVATE KEY` |

OpenSSH 形式（`BEGIN OPENSSH PRIVATE KEY`）、暗号化された PKCS#8（`BEGIN ENCRYPTED PRIVATE KEY`）、パスフレーズ付きの PKCS#1、公開鍵、証明書は使わない。対象外の形式を貼り付けて入力待ちが続く場合は Ctrl+C で中止する。

「GitHub App の秘密鍵（PEM 形式）」という案内に従い、秘密鍵を BEGIN 行から対応する END 行まで全文貼り付けて Enter を押す。入力は完全に非表示で、`*` も表示しない。Ctrl+C で中止できる。OS 固有のクリップボードコマンドやローカルの秘密鍵ファイルは不要。端末の入力記録、クリップボード履歴、端末間同期に秘密鍵が保存されないよう、利用する端末とクリップボード機能の設定を確認する。

入力完了後、Pod の memory emptyDir に標準入力で転送し、RSA 秘密鍵の形式確認と Vault import を続けて実行する。秘密鍵はコマンド引数、ログ、シェル履歴、Vault Secret、Kubernetes Secret に保存しない。Pod 内の秘密鍵は処理終了時に削除する。

同じ秘密鍵が登録済みの場合は公開鍵を比較し、新しいバージョンを作らず登録状態を確認する。別の鍵が登録済みの場合は上書きせず停止する。鍵のローテーションはこのスクリプトの対象外とし、新しい App key pair の発行、同名 Key への新バージョン import、接続確認、旧 key pair の失効を別途実施する。

##### 3. 完了または失敗を確認する

Key の種類が RSA、操作に sign と verify を含み、有効であることを確認した後に Key URI を表示し、一時 namespace、role assignment、Managed Identity（federation を含む）を撤去する。「秘密鍵の登録確認と一時リソースの撤去が完了しました。」と表示され、終了コードが 0 の場合に GitHub 接続へ進む。`exit` を手動実行する必要はない。

**失敗した場合は、原因を解消して手順1の同じコマンドを最初から実行する。** 別の端末からでもよく、前回のシェル変数や kubeconfig は不要。通常のエラーと Ctrl+C では一時リソースの撤去を試みる。端末の強制終了や通信断で撤去できなかった場合も、次回の開始時に専用のラベルとタグが一致する一時リソースを撤去してから作り直す。所有確認や撤去に失敗した場合は、新規作成と秘密鍵入力に進まず停止する。失敗前に import が完了していた場合も、同じ秘密鍵で再実行すれば公開鍵の比較で判定する。

撤去だけを行う場合は次を実行する。登録済みの GitHub 用 Key と Agent の Crypto User 権限は削除しない。

```bash
bash scripts/import-sre-agent-github-key.sh "<env>" --cleanup-only
```

旧手順の `kv-import-<時刻>-<乱数>` というリソースは、このスクリプトの対象外である。旧手順で作成した namespace、Managed Identity、role assignment は、表示された識別情報を使って別途撤去する。新手順の再実行で撤去済みとは判断しない。

### GitHub 認証とリポジトリを登録する

鍵の登録と一時リソースの撤去後、リポジトリルートで配備済みの azd 環境名を指定して実行する。Vault URI は指定環境から取得し、Client ID は端末の入力案内に従って貼り付ける。Client ID は秘密鍵や Client secret とは異なり、入力を非表示にはしない。これまでの `up` / `provision` / `deploy` の完了状態とは独立して再実行できる。

Client ID の入力には、標準入力を接続する `azd exec --interactive` が必要になる。対話端末のない実行では `--client-id "<Iv...>"` を指定する。

```bash
azd exec --interactive -e "<env>" -- uv run --no-project --no-config \
  scripts/configure_sre_agent.py github
```

この操作は `github.com` の認証方式を BYO App に設定し、`https://github.com/torumakabe/aks-chaos-lab` の `main` を Code Access に登録する。既存の github.com 認証設定を置き換えるため、別の App や OAuth を設定済みの Agent では実行前に対象を確認する。スクリプトは domain 設定を読み戻し、repository の URL、branch、type と cloneStatus が Ready であることを確認して正常終了する。通常の連携設定では署名監査の取り込みを待たない。

Client ID 入力後の接続、認証、設定、Ready 待ちは、共通の5分の期限で管理する。Azure CLI の各呼び出しは60秒、API の接続と読み取りの待機は30秒のタイムアウトを設定し、期限までの残り時間が少ない場合は短縮する。API の再試行と repository の再確認でも期限を延長せず、処理段階と待機状況を表示する。通信ライブラリのタイムアウトは処理全体の強制終了ではないため、通信や中断処理によって終了が期限を超える場合がある。失敗とタイムアウトは非ゼロで終了する。Ctrl+C では中断を表示し、終了コード130で終了する。いずれも保存済みの設定や鍵を削除せず、原因を解消して同じコマンドを再実行できる。鍵の再登録は不要である。

認証 audience は `https://azuresre.dev`。API のフィールド名は `privateKeySecretUri` だが、実サービスは `/keys/` URI を要求する。[Learn の Secret URI を使う手順](https://learn.microsoft.com/azure/sre-agent/setup-github-byo-app)と異なるため、Secret URI は受け付けない。

### GitHub 接続の完了確認

通常は前節の `github` コマンドの正常終了で連携設定完了とする。GitHub の Issue / PR / Actions 用 connector はこの手順では作成しない。

public repository の clone 成功だけでは App 認証や private Vault の利用を証明できない。これらの証拠が必要な検証では、次の読み取り専用コマンドで Vault の `AZKVAuditLogs` を照会し、対象 Key の `KeySign` Success、現在の Agent system identity、Agent subnet 内の CallerIpAddress を照合する。これは追加の検証であり、通常の設定ごとの必須操作にはしない。再設定だけで新しい署名が発生することは保証できないため、新しい監査記録がないことだけで接続失敗とは判断しない。

`--since` には証拠を確認したい操作の開始日時をタイムゾーン付きで指定する。省略時は直近30分を調べる。表示された記録の時刻と対象を確認し、以前の環境や対象操作前の署名ログを今回の証拠に使わない。

```bash
azd exec -e "<env>" -- uv run --no-project --no-config \
  scripts/configure_sre_agent.py verify-github --since "<接続設定開始日時>"
```

照会は1回で、自動の取り込み待ちはしない。処理全体の期限を2分、各 Azure CLI 呼び出しを60秒以内とし、残り時間に合わせて短縮する。一致する監査記録がなければ未確認として非ゼロで終了する。ログの取り込み待ちの場合は、数分後に同じ確認コマンドを再実行する。接続設定や clone をやり直す必要はない。問い合わせ自体が失敗した場合はエラーを表示し、ログの取り込み待ちとして扱わない。

通常の `up` / `provision` / `deploy` でこの接続作業を繰り返す必要はない。Agent と Vault を削除して再作成した場合は鍵の登録と接続設定を再実行する。鍵のローテーション時は新バージョンを import し、署名と接続を確認してから古い GitHub App key pair を失効させる。

## ナレッジと response plan の再適用

README 変更後や hook 失敗後は、基盤を再配備せず次を実行する。

```bash
azd exec -e "<env>" -- uv run --no-project --no-config \
  scripts/configure_sre_agent.py bootstrap
```

この操作は opt-in された response plan と README を再適用し、GitHub domain、repository、鍵には触れない。同じファイル名で README を更新する。ナレッジはアップロード時のコピーであり、Git の更新に自動追従しない。検索用インデックスの完了表示と、実際に必要な回答を検索できることは別々に確認する。

インデックスの完了は、ナレッジ一覧の `isIndexed` が true で `errorReason` が空の状態で判定する。登録直後に `errorReason`（File could not be indexed で始まる文言）が一時的に付き、アップロードし直さなくても約2分以内に消えて `isIndexed` が true になる場合がある。hook はこの状態を直ちには失敗とせず、最大5分待つ。5分後も `errorReason` が残るときだけ、その内容を示して失敗する。

## 権限を超える操作で調査が止まる場合

Agent が user-assigned managed identity に許可されていない操作を実行しようとすると、その操作は On-Behalf-Of（OBO）の承認待ちになり、thread の調査が止まる。REST で thread のメッセージを取得すると、該当する tool call の `status` は `PendingAuthorization` を示す。承認できるのは SRE Agent Administrator ロールを持つ work または school アカウントのユーザーだけで、個人の Microsoft アカウントは承認できない（[Agent permissions](https://learn.microsoft.com/azure/sre-agent/permissions#on-behalf-of-obo)）。

この構成の Agent が持つ権限は、Resource Group 範囲の Reader、Monitoring Reader、Log Analytics Reader と、incident integration を有効にした場合の subscription 範囲の Monitoring Contributor である。検証では、Function App の publish profile の取得（`Microsoft.Web/sites/publishxml/action`）が承認待ちになった。Function App の app settings の一覧と AKS の kubeconfig の取得もこれらのロールに含まれない操作であり、同じ仕組みで承認待ちになると考えられるが、実環境では確認していない。承認待ちを避けるために権限を追加するかどうかは、対象の操作ごとに判断する。

## 構成変更と削除の注意

Agent、専用 subnet、VNet は同じリージョンで使う。`AZURE_SRE_AGENT_LOCATION` に異なるリージョンを指定すると base の preprovision hook が停止する。専用 subnet は Agent を無効にしても VNet 内に予約しておき、incremental deployment による接続先 subnet の削除を防ぐ。

Agent の廃止は、[Deployment Stacks への取り込み](deployment.md#deployment-stacks-の有効化と既存環境の移行)を有効な構成のまま完了してから行う。Agent と incident integration の opt-in を false にし、base の Stack what-if で専用リソースの削除を確認して `azd provision base` を実行する。共有 VNet と予約済み subnet は残す。Key Vault は soft-delete され、通常更新では purge しない。環境全体を削除する場合は `azd down --force --purge` を使う。Stack 取り込み前から定義に存在しない残存リソースは自動削除の対象にならない。

検証環境で手動作成した subnet、Private Endpoint、RBAC が残っている場合は、対象環境を選択して [What-if 分析スキル](../.github/skills/bicep-what-if-analysis/SKILL.md)を `--layer base` で実行し、base の再適用前に差分を確認する。特に `snet-sre-agent-test` と既定の `snet-sre-agent` の address prefix 重複、Agent の接続先変更、同じ principal / scope / role の異なる role assignment 名に注意する。既存 Agent の接続先を変えるには切断と再接続が必要なため、通常配備の hook による強制切断や検証用リソースの自動削除は行わない。
