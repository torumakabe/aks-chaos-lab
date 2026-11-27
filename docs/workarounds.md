# ワークアラウンド棚卸し

このリポジトリで継続中のワークアラウンドと、それぞれの **解消条件** をまとめる。`review-repo` エージェントの定期チェック対象。GA や仕様改善で不要になったものは剥がす。

ID は固定し、削除済み ID は再利用しない。

---

## A. Azure Monitor SLI 関連

### A-1. `infra.layers` で base/sli を分離し、external SLI metric 出現を待つ

- **概要**: `azd up` の workflow を `provision base` → `deploy api` → `deploy observability` → `deploy chaos-mesh` → `deploy external-sli-publisher` → `provision sli` に分割し、`sli` layer の `preprovision` で SLI 用 good / total metrics が Managed Prometheus に出るまで待つ。
- **理由**: Azure Monitor SLI は作成時点で入力 metric と partitioning dimensions が Managed Prometheus に存在することを要求する。メトリクス materialize 前に SLI を作ると validation で失敗する。
- **場所**: `azure.yaml`、`infra/sli/main.bicep`、`docs/adr/012-functions-direct-external-sli-probe.md`
- **解消条件**: 必要な input metrics と partitioning dimensions が不在でも SLI 作成が受理され、その後の発行開始で評価されるようになる。この条件は metrics 待機の撤去判断に用いるものであり、base/sli layer 分離全体の廃止は別途判断する。
- **確認方法**: 実験の承認を得た新規の一時環境で、external-sli-publisher の発行開始を SLI 作成後まで止め、必要な input metrics と partitioning dimensions が Managed Prometheus に存在しないことを確認する。不在を維持したまま metrics 待機を外して `infra/sli/main.bicep` を適用し、SLI 作成が受理されることを確認する。作成中に publisher が発行した場合や、既に入力が存在した場合の成功は撤去の根拠にしない。受理後に発行を開始し、SLI destination metric の出現で評価開始を確認する。

### A-2. `scripts/wait-for-external-sli-signals.py` で external SLI metric 出力待機

- **概要**: `sli` layer の `preprovision` hook で、Managed Prometheus 上に `chaos_app_external_availability_total`、`chaos_app_external_latency_total`、および `latencyThresholdLe` に対応する `chaos_app_external_latency_good{le="<latencyThresholdLe>"}` が出るまで待つ。Azure Monitor SLI destination `:Value` metric は `postprovision` hook では待たない。
- **理由**: A-1 と同じ。SLI 作成前に external SLI input metrics が materialize されている必要がある。SLI 作成後の destination metric は評価開始まで時間がかかるため、`azd up` の完了条件にしない。必要な場合は `uv run scripts/wait-for-external-sli-signals.py --skip-source --require-sli-destination` で手動確認する。
- **場所**: `scripts/wait-for-external-sli-signals.py`、`azure.yaml`
- **解消条件**: A-1 と同じ。
- **確認方法**: 入力不在の成立条件と撤去判断は A-1 と同じ。destination metric の手動確認は、SLI 作成後に `uv run scripts/wait-for-external-sli-signals.py --skip-source --require-sli-destination` を実行する。

### A-3. AMW managed resource group 内 DCR への SLI RBAC 付与

- **概要**: `MA_<amw-name>_<region>_managed` リソースグループ内の AMW と同名 DCR に対して、SLI 用 UAMI に `Monitoring Reader` と `Monitoring Metrics Publisher` を付与する。
- **理由**: managed DCR の `Monitoring Metrics Publisher` を外すと、SLI 更新時の storage location validation が失敗した。`Monitoring Reader` は Microsoft Learn に記載された SLI destination metric の読み出し要件に合わせて付与する。Reader を単独で外した場合は検証していない。
- **場所**: `infra/modules/azmonitor/sli-managed-dcr-rbac.bicep`、ADR-009 §RBAC
- **解消条件**: Microsoft 側で AMW 本体への RBAC だけで SLI が作れるよう挙動が修正される、または公式に managed RG への RBAC が必要だと文書化され、別の方法（policy / built-in role）が用意される。
- **確認方法**: 承認済みの検証環境で managed DCR のロールを一つずつ外し、他の権限を固定して RBAC 伝播後の結果を比較する。Publisher は SLI 作成と更新時の validation、Reader は公式の読み出し要件と destination metric の読み出し結果を確認する。両ロールを同時に外した結果や、PUT の成功だけで双方の不要を判断しない。

### A-5. 新規 Service Group の権限反映を待つ

- **概要**: 環境別 Service Group 上の `Service Group Administrator` 割り当てを確認する。作成者への自動付与があれば追加せず、不足する場合だけ付与する。割り当て確認後の `slis/write` の403は、資産や割り当てを変更せず、時間を置いて再試行する。
- **理由**: 新規 Service Group では割り当てが存在していても SLI 操作への反映が遅れる。[公式概要](https://learn.microsoft.com/azure/governance/service-groups/overview#role-based-access-controls)では作成者への自動付与と子 Service Group へのロール継承を説明している。
- **場所**: `docs/deployment.md` §新規 Service Group 作成後の権限準備
- **解消条件**: 新規 Service Group の作成直後から必要な SLI 操作が成功し、対象 scope の権限確認と待機を個別に案内する必要がなくなる。親ロールのみでの SLI 操作は、直接割り当ての有無と反映時間を区別して再評価する。
- **確認方法**: 新規 Service Group の割り当て、対象 identity と scope を確認し、同じ SLI 定義を時間を置いて再試行する。待機中に Service Group や割り当てを変更しない。

### A-8. base Stack に残る未管理 SLI を削除前に検出する

- **概要**: base Stack があり sli Stack がない場合、base Stack の管理対象から環境所有の Service Group ID を取得する。Service Group が存在し SLI が残っていれば、削除を止めて sli Stack への取り込みを求める。
- **理由**: base Stack だけを削除すると、sli Stack が管理していない tenant scope の SLI が残る可能性がある。Service Group の一覧 API がないため、base Stack のリソース一覧を使って確認する。
- **場所**: `scripts/validate-deployment-stacks-before-down.py`、[環境削除](deployment.md#環境削除)
- **解消条件**: azd が Stack 間の tenant scope 依存関係を検証し、削除前に未管理 SLI を検出する。
- **確認方法**: hook のテストで、base Stack に Service Group が含まれ SLI が残る場合に停止し、SLI がない場合は削除を許可することを確認する。

---

## B. OTLP / Application Insights 関連 (ADR-006)

---

## C. プレビュー機能 / Preview API バージョン

### C-1. feature flag `AKS-AddonAutoscalingPreview` を `az feature register` 必須

- **概要**: `AKS-AddonAutoscalingPreview` をサブスクリプション単位で事前登録する。
- **理由**: 現行構成の AKS VPA addon autoscaling を有効にして新規クラスタを作成する場合、未登録では AKS preflight が失敗する。既存の `eval` クラスタの更新では、未登録でも preflight が成功したため、既存クラスタの更新だけでは登録の要否を判断できない。
- **場所**: `docs/deployment.md` §プレビュー機能とリソースプロバイダー登録
- **解消条件**: VPA addon autoscaling が GA し、feature flag 登録が不要になる。
- **確認方法**: feature を未登録に戻した一時サブスクリプション状態で fresh `azd up` または `azd provision base` が通るか確認する。

### C-2. AKS の preview API バージョンを継続使用

- **概要**: AKS の作成本体には `Microsoft.ContainerService/managedClusters@2026-05-02-preview` を使用する。ID と scope のみを使う existing 参照には GA API を使用する。Fleet 関連 resource type は `Microsoft.ContainerService/fleets@2026-06-01` と同 version の member / update strategy / auto upgrade profile を使用する。
- **理由**: AKS の [GA `2026-06-01` の schema](https://raw.githubusercontent.com/Azure/azure-rest-api-specs/main/specification/containerservice/resource-manager/Microsoft.ContainerService/aks/stable/2026-06-01/managedClusters.json) には、現行構成で使う `workloadAutoScalerProfile.verticalPodAutoscaler.addonAutoscaling`、`upgradeStrategy`、`upgradeSettingsBlueGreen` が存在しない。[preview `2026-05-02-preview` の schema](https://raw.githubusercontent.com/Azure/azure-rest-api-specs/main/specification/containerservice/resource-manager/Microsoft.ContainerService/aks/preview/2026-05-02-preview/managedClusters.json) にはこれらのプロパティが定義されているため、preview を継続する。
- **場所**: `infra/modules/aks.bicep` の AKS 作成宣言
- **解消条件**: GA API の公式 schema が `workloadAutoScalerProfile.verticalPodAutoscaler.addonAutoscaling`、`upgradeStrategy`、`upgradeSettingsBlueGreen` に対応し、同 API への移行後も現行設定が維持される。
- **確認方法**: GA API の公式 schema でこれら3プロパティへの対応を確認してから、AKS `managedClusters` の API を置換し、対象環境を選択して [What-if 分析スキル](../.github/skills/bicep-what-if-analysis/SKILL.md)を `--layer base` で実行する。差分を確認後、承認済みの検証環境で `azd provision base` を適用し、GET で `provisioningState: Succeeded` と、3プロパティに設定した値が維持されることを確認する。デプロイ成功だけでは preview API を撤去しない。

### C-3. Azure Monitor 系 managed resource group 命名は制御不可

- **概要**: AMW (`MA_<amw>_<region>_managed`)、App Insights (`ai_*`) の managed RG 名は固定パターンで命名できない。AKS の `MC_*` のみ `nodeResourceGroup` で制御可能。
- **理由**: Azure 側の仕様。
- **場所**: ADR-006 §マネージドリソースグループの制約
- **解消条件**: Azure 側で命名 API が提供される。
- **確認方法**: Microsoft Learn の AMW / App Insights ドキュメントで命名カスタマイズの記述が追加されたか確認する。

---

## D. 既知の遅延 / 制約

### D-5. OpenTelemetry UpDownCounter `http.server.active_requests` の Pod 再起動時ドリフト

- **概要**: FastAPI / OTel auto-instrumentation が emit する `http.server.active_requests` は UpDownCounter (DELTA aggregation) で per-process state を持つ。Chaos 実験で Pod 再起動が頻発する環境では、`rate()` / `delta()` で集計した時に負値や jitter が出る。
- **理由**: UpDownCounter は process-local な state を保持し、Pod restart で reset されるが、Azure Monitor / Prometheus 側の集計クエリは Cumulative 前提で、reset を補正する仕組みがない。加えて、実機検証では標準 `http.server.active_requests` 自体が dot / underscore 名とも AMW に出ないケースがある。
- **場所**: `docs/observability.md` §運用上の注意、`src/api/app/telemetry.py`、`src/api/app/main.py`
- **解消条件**: 標準 `http.server.active_requests` が restart / no-traffic に強い形で安定 emit される、または Azure Monitor 側で reset 補正が提供される。
- **確認方法**: fresh 環境で App AMW に `http.server.active_requests` / `http_server_active_requests` が安定して出るか、かつ Pod restart 後に負値や jitter が出ないか確認する。

### D-6. API OTLP `Instrumentation` は Deployment より先に適用する必要がある

- **概要**: AKS App Monitoring admission webhook は `Instrumentation` custom resource を参照して `OTEL_EXPORTER_OTLP_*` を注入する。`Deployment/chaos-app` と `Instrumentation/chaos-app-otel` を同じ Kustomize unit で同時適用すると、Pod admission 時点で `Instrumentation` が存在せず、API Pod に OTLP env が入らない race が起きる。
- **理由**: Kubernetes は同一 Kustomize bundle 内の CR と Deployment の admission-time 依存を保証しない。既に admitted された Pod は後から retroactive に mutate されないため、Application Insights traces / metrics / logs と Redis dependency が欠落する。
- **場所**: `azure.yaml` の `api-instrumentation` service、`k8s/apps/chaos-app/instrumentation/`、`scripts/check-api-otel-injection.py`、`docs/observability.md` §運用上の注意、ADR-006
- **解消条件**: AKS App Monitoring が参照先 `Instrumentation` 未作成時でも Deployment / Pod を後から安全に再評価できる、または Kubernetes 側で CR と Deployment の admission-time ordering を宣言できる。
- **確認方法（現行動作）**: `api-instrumentation` の `postdeploy` hook が Instrumentation の準備完了を待ち、`api` の `predeploy` hook がその存在を、`postdeploy` hook が Pod への OTel 設定注入を自動確認する。これは先行適用を維持した構成の確認であり、撤去可能という証拠ではない。通常の適用順序と承認済み index 向けの操作は [デプロイ手順](deployment.md) を参照する。
- **確認方法（撤去判断）**: 公開仕様で未作成の Instrumentation への対応を確認したうえで、実験の承認を得た新規の一時環境で比較する。先行適用と存在待機を外し、Deployment の先行適用と同時適用のそれぞれで、手動の再適用や Pod restart なしに OTel 設定が注入され、Application Insights に traces / metrics / logs が届くことを確認する。先行適用を維持した対照構成と同じ版と設定を使い、Pod 作成時刻と注入結果を比較する。既存 eval の適用順序は変更しない。

### D-7. ama-metrics `mdsd.err` で `AMACoreAgent: Connection refused` が多発（当時の観測範囲ではデータ到達への影響なし）

- **概要**: `ama-metrics` Deployment の replica pod (`prometheus-collector` container) で `mdsd.err` に `[CreateSocket] Failed to connect port 12564 ... to AMACoreAgent: Connection refused` と `[OtlpTokenFetcher] AMACoreAgent tenant not started, trying to start it. DCR Contents: ...dcr-<otlp>...` が約 60 秒周期で継続出力される。
- **理由**: 当時の観測環境では、replica pod の image に `amacoreagent` バイナリが同梱されていたが、replica pod 内では `AMACoreAgent` プロセスが supervisor から起動されていなかった。同じ image を使う `ama-logs` DaemonSet 側では `AMACoreAgent` が正常起動していた。
- **実害評価**: 当時の観測環境では Managed Prometheus / Container Insights / ContainerNetworkLogs / OTLP traces / logs のデータ到達は正常だった。この環境とデータ種別の範囲では、影響を `mdsd.err` のディスク消費とログノイズと評価した。他の環境や更新後のデータ到達を保証するものではない。
- **場所**: AKS managed addon の `kube-system/ama-metrics-*` Deployment。リポジトリ側のコードでは制御不能。
- **解消条件**: Microsoft 側で `prometheus-collector` image の supervisor が replica pod でも `AMACoreAgent` を起動する、あるいは OTLP DCR 配信を replica pod 対象から除外する修正が入る。
- **確認方法**: image tag の更新後に `kubectl -n kube-system exec <ama-metrics-pod> -c prometheus-collector -- ps -ef | grep amacoreagent` と `mdsd.err` を確認する。併せて Managed Prometheus / Container Insights / ContainerNetworkLogs / OTLP traces / logs の更新後のデータが継続して宛先に届くことを確認する。
- **追跡**: [#130](https://github.com/torumakabe/aks-chaos-lab/issues/130)（当時の観測環境と上記データ種別の範囲ではデータ到達への影響なしと判定、closed）。

### D-9. `ErrorAwareSampler` は span 終了後の ERROR を判定できない

- **概要**: `OTEL_TRACES_SAMPLER` が未設定の場合、API は `ParentBased(ErrorAwareSampler)` を使用する。この sampler は親のない root span の name または HTTP path attribute に `chaos`、`error`、`throw` を含む場合に常にサンプルし、それ以外を `TELEMETRY_SAMPLING_RATE` に従って判定する。親 span がある場合は親の sampling decision を継承するため、キーワードを含むリクエストでも未サンプルの親を継承すると保持されない。キーワードを含まないリクエストが span 終了時に ERROR となった場合も、その trace が保持される保証はない。
- **理由**: OpenTelemetry SDK は sampler を span 開始時に呼び出すため、span 終了時に設定される `status=ERROR` を判定材料にできない。リポジトリには Collector 側の tail-based sampling 構成がなく、アプリ側のキーワード判定は欠落を減らすための限定的な回避策である。
- **場所**: `src/api/app/telemetry.py` の `ErrorAwareSampler`、`src/api/tests/unit/test_telemetry.py`
- **解消条件**: SDK 側で全 span を Collector へ送り、Collector 側で `status=ERROR` を条件にした tail-based sampling を構成して、キーワードに依存せず ERROR trace を保持できるようになる。または OpenTelemetry SDK が span 終了時の状態に基づく sampling を提供する。
- **確認方法**: `uv run pytest src/api/tests/unit/test_telemetry.py -k error_aware_sampler` でキーワード判定と ratio-based 判定を確認する。tail-based sampling を導入する場合は、キーワードを含まないエンドポイントでエラーを発生させ、Collector と Application Insights で該当 trace が保持されることを確認する。

### D-12. Renovateはversionとchecksumを一体更新できないため、LefthookのpinをRenovate管理から外し専用taskで更新する

- **概要**: `.github/workflows/ci.yml`の`LEFTHOOK_VERSION`と`LEFTHOOK_SHA256`は対で固定する。`LEFTHOOK_SHA256`はRenovateが計算できないため、Renovateに`LEFTHOOK_VERSION`だけを更新させると、そのPull RequestはCIの`sha256sum -c -`でchecksum不一致となり必ず失敗する。Lefthookは`.github/renovate.json`の対象に含めない。更新時は`update-lefthook-pin --version <version>`で公式checksumと`LEFTHOOK_VERSION`を一体更新し、`check-version-pins`と`test-hooks`で検証する。
- **理由**: Renovateのregex custom managerは単一の`matchStrings`が捕捉した値の更新候補を提示するだけで、別ファイルや別行のchecksumを計算して同時に書き換える機能を持たない。versionとchecksumを同じcustom managerで安全に一括更新する一般的な方法は現時点でない。
- **場所**: `.github/workflows/ci.yml`の`LEFTHOOK_VERSION`と`LEFTHOOK_SHA256`、`scripts/tasks.py`の`check-version-pins`と`update-lefthook-pin`（`.github/renovate.json`にLefthookのcustom managerは置かない）
- **解消条件**: RenovateがGitHub Releaseのchecksum資産から関連値を解決し、同じcustom managerでversionとchecksumを一体更新できるようになる。その時点でLefthookをRenovate管理へ戻し、専用更新taskの必要性を再評価する。
- **確認方法（現行動作）**: `update-lefthook-pin --version <version>`が公式`lefthook_checksums.txt`から対象versionのchecksumを取得し、versionとchecksumを同じ変更で更新することを確認する。更新後に`check-version-pins`と`test-hooks`を実行する。不正値の検出は一時コピーで`LEFTHOOK_SHA256`を1文字削り、`check-version-pins`が`fail`になることを確認する。
- **確認方法（撤去判断）**: Renovateの公開仕様で、同じcustomManagerによるversionとchecksumの一体更新が提供されたことを確認する。その後、承認済みの検証用リポジトリで旧版からの更新を試し、同じPRで`LEFTHOOK_VERSION`と`LEFTHOOK_SHA256`が更新され、checksumが更新先の対象platform用公式資産と一致することを確認する。手動補正なしで既存CIが成功することを撤去判断の条件とし、versionだけの更新成功では専用処理を廃止しない。

### D-13. Fleet 登録後に AKS 拡張を導入する

- **概要**: AKS の作成後、Fleet module の完了を待ってから Inspektor Gadget 拡張を導入する。拡張側に `dependsOn: [fleetManager]` を置き、Fleet を特定の拡張に依存させない。
- **理由**: Inspektor Gadget の初期化に伴う AKS 更新中に、Fleet member の登録が `ManagedClusterNotInExpectedState` で拒否されることがある。機能上の依存ではなく、同じ AKS への管理操作の重複を避けるための順序である。
- **場所と対象範囲**: `infra/main.bicep` の同一 deployment 内の Fleet module と拡張 module。Fleet module 内の登録、更新戦略、アラートなどが失敗すると、拡張導入も開始しない。module 完了後も Azure 内部の更新が残る場合があり、内部操作の競合や別 deployment、外部操作による更新まで排他するものではない。
- **拡張の追加と廃止**: 拡張を追加するときは、その拡張側に Fleet 完了後の依存を置く。拡張同士の AKS 更新も競合する場合は、該当する拡張間にも順序を設ける。Inspektor Gadget の廃止だけでは Fleet 側の依存を変更しない。宣言の除去と Azure 上の既存拡張の削除は別操作として扱う。
- **解消条件**: Azure 側が Fleet 登録と拡張初期化の競合を待機または安全に処理できることを確認し、依存を外した新規環境で初回構築が成功する。
- **確認方法（現行動作）**: 生成 ARM template で Fleet が AKS に、拡張が Fleet に依存することを確認する。新規環境の Activity Log で Fleet module 完了後に拡張が開始し、base 作成が azd コマンドの再実行なしで成功することを確認する。これは順序制御が動くことの確認であり、その必要性がなくなったという証拠ではない。
- **確認方法（撤去判断）**: 実験の承認を得た新規の一時環境で、拡張側の Fleet 依存だけを外した構成と、依存を維持した対照構成を比較する。region、AKSと拡張の版、その他の設定をそろえ、Fleet 登録と拡張初期化の開始時刻、完了状態、エラーを Activity Log で確認する。操作が重なった場合も base 作成が azd コマンドの再実行なしで成功することを確認し、初回構築の比較を反復する。既存 eval への再適用、操作が偶然重ならなかった実行、一度だけの成功を競合解消の証拠にしない。
- **再試行**: module 完了後に残る内部操作との競合は、拡張サービスの再試行に任せる。失敗が deployment に返される場合は、対象リソースと最終エラーコードを確認し、Bicep の `@retryOn` による回数上限付きの再試行を検討する。

### D-14. Local DNS の API 更新が既存ノードへ反映されない場合

- **対象範囲**: API の Local DNS プロファイルで設定値が `mode: Required`、読み取り専用の状態が `state: Enabled` だが、新規 Pod の resolver、ノード設定、9253 番のメトリクスで Local DNS の稼働を確認できない既存 pool。
- **追加対応**: 対象 pool の `az aks nodepool get-upgrades` で更新先を確認し、承認後に `az aks nodepool upgrade --node-image-only` を実行する。Kubernetes バージョンと保存済みの BlueGreen 設定は変更しない。この追加更新を通常の有効化へ無条件に組み込まない。
- **理由**: API 更新後も Local DNS のノード設定が生成されず、node image 更新後の新ノードでは稼働した。原因は未確定で、すべての AKS 構成に該当するとは判断していない。
- **解消条件**: 同じ条件の既存 pool で `localdns-config` の更新だけによりノード構成が反映され、新規 Pod の名前解決と Local DNS のメトリクスを確認できるようになる。
- **確認方法**: 更新前後のノード設定と新規 Pod の resolver を比較し、各ノードの `up{job="localdns-metrics"}` と問い合わせ counter を確認する。

### D-15. azd layer の deploymentStacks 設定と公開 schema の差

- **対象範囲**: `azure.yaml` の `infra.layers[].deploymentStacks`。azd 1.37.1 の実行時はこの設定を読み込むが、同版の公開 JSON schema の layer properties には含まれないため、エディターに未定義プロパティの警告が出る。
- **対応**: 設定は実行時の `provisioning.Options.DeploymentStacks` に従い各 layer に置く。親の `infra.deploymentStacks` へ移すと layers との併用検証に失敗する。schema の独自コピーや validation の無効化は追加しない。
- **根拠**: [azd 1.37.1 の Options](https://github.com/Azure/azure-dev/blob/6bd1275b6b31104034673946953533eb860dd646/cli/azd/pkg/infra/provisioning/provider.go)、[公開 schema](https://github.com/Azure/azure-dev/blob/6bd1275b6b31104034673946953533eb860dd646/schemas/v1.0/azure.yaml.json)。
- **解消条件**: 公開 schema が layer の `deploymentStacks` を定義し、エディターと CLI の両方で同じ設定を受け付ける。
- **確認方法**: schema の layer properties と CLI の設定読込を確認する。読込確認には存在しない名前を指定した `azd hooks run stacks-config-validation-only` を使い、Azure の配備は行わない。

### D-16. Stack の作成後に個別取得が404になる場合

- **対象範囲**: `azd provision` が Stack の `ResourceNotFound`（404）で終了し、`az stack sub list` には対象 Stack が存在する一方、`az stack sub show` は404になる場合。
- **対応**: 先行 deployment の完了を確認し、環境、layer、定義、opt-in 設定を変えずに、`azd provision` を `--no-state` 付きで再実行する。個別のリソース削除やアプリの再ビルドは不要。原因は未特定であり、`--no-state` なしでの回復は未確認。
- **解消条件**: この症状について Azure または azd の修正が確認され、同じ構成の作成と再作成で再実行せずに Stack を取得できる。

対象環境の subscription ID、環境名、失敗した layer を指定し、Stack 内部の最新 deployment を確認する。

```bash
SUBSCRIPTION_ID="<subscription-id>"
ENV_NAME="<env>"
LAYER="base"
STACK_NAME="azd-stack-${ENV_NAME}-${LAYER}"

az deployment sub list --subscription "$SUBSCRIPTION_ID" \
  --query "sort_by([?starts_with(name, '${STACK_NAME}-')], &properties.timestamp)[-1].{name:name,state:properties.provisioningState}" \
  --output json
```

`state` が `Running` などの処理中なら完了を待つ。`Succeeded` なら、失敗したときの引数を維持し、`--no-state` を付けて provision を再実行する。基本形は次のとおり。`Failed`、`Canceled`、または該当履歴がない場合は、この回避策で再実行せず、元のエラーと対象を確認する。

```bash
azd provision "$LAYER" -e "$ENV_NAME" --no-state

az stack sub show --subscription "$SUBSCRIPTION_ID" --name "$STACK_NAME" \
  --query '{state:provisioningState,resources:resources}' --output json
```

Stack の `state` が `succeeded`、対象リソースの `status` が `managed` であることを確認する。再実行でも同じ404になる場合は、反復せず `azd --debug` のログと deployment の correlation ID を保存して調査する。
