# ADR-017: 管理対象環境向け approved-index 変換フロー (ADR-013 の一部を amend)

## Status

Accepted

- Date: 2026-08-08
- Amended: 2026-09-08（明示 task の限定 lock 修復と API 成果物の受け渡し）

## Context
Public GitHub repository では public PyPI source の root `uv.lock` を唯一の正本とする。一方、public PyPI へ直接アクセスできず、organization-approved package index の使用を必須とする管理対象環境では、ADR-013 の依存関係同期方式を適用できない。

管理対象環境で同期により lock の取得元だけが変わった場合、依存判断を変えずに正本へ戻せる範囲を限定する必要がある。また、専用 API build の固定ローカルタグでは、明示した成果物がデプロイ先で稼働しているかを識別できない。暗黙の状態共有より、対象と停止条件を明示する方法を選ぶ。

## Decision
1. Public GitHub repository では public PyPI source の root `uv.lock` を唯一の正本として維持する。
2. 管理対象環境では、user-level uv configが単一の`[[index]]`と`default = true`を持つことを検査する。index source、find-links、hash検証、TLS検証、projectまたはdependency groupを変更する設定と環境変数がある場合は同期を中止する。exportと後続の`uv run`はroot projectと対象venvの絶対pathへ固定する。
3. 管理対象環境ではpublic lockのsourceを検査し、`uv export --frozen`で未コミットの一時requirementsへ変換する。変換後のrequirementsも検査してdirect URLを拒否し、registry packageへ`--require-hashes`を適用してapproved-indexから環境を構築する。workspace sourceはindexを介さず参照する。通常環境とは別のuv cacheを使い、過去にpublic PyPIから取得したartifactを再利用しない。approved-indexがpublic lockの許可するbit-identical artifactを提供することを成立条件とする。
4. task runnerが有効なapproved-index設定を検出した場合、通常のtaskでは、各processの最初のworkspaceコマンドを実行する前に、仮想環境を消去してapproved-indexから同期する。同期開始からtask processの終了までは、対象venvの正規化pathから導出したprocess間lockを保持し、別processによる同じvenvの再構築を防ぐ。同じprocess内の後続コマンドは、直前に構築した環境を`--no-sync`で使用する。永続的なsync stateは保存しない。同期の開始時と完了時でlockのhashが異なる場合はコマンドを実行せず、public sourceへ自動fallbackしない。
    - `review-repo-full` では、同じレビューの後続 QA で準備済み環境を共有するため、レビュー専用の隔離コピー内で準備した環境に限り、process 間の引渡しを認める。準備には上記の取得元条件と hash 検査を適用し、成功した場合だけレビュー処理が後続 QA へ明示的に引き渡す。準備失敗時は後続 QA を起動しない。元 worktree や任意の既存 venv の再利用は認めない。引渡しと排他の有効範囲は[デプロイ文書](../deployment.md#full-レビューの-python-環境)に記載する。
5. task runnerはscriptの絶対pathと`uv run --no-project`で起動し、taskのstate検査より前に別のprojectを探索または同期しないようにする。
6. GitHub Actions、Azure Functions remote build、外部利用者はpublic PyPIを継続して使用する。
7. Docker buildも同じexportとpip syncを使う。管理対象環境のローカルbuildはbuild argumentでapproved-index modeとconfigのSHA-256を明示し、BuildKit secretでuser-level uv configをmountする。Dockerfileはmountしたconfigのhashを照合し、build modeとhashをdependency layerおよびBuildKit cache mountのkeyへ含める。公開CIにはsecretを渡さない。
8. uvのhostはルート`pyproject.toml`で定義する単一minor seriesの互換範囲を許可する。GitHub Actionsとlock更新workflowはsetup-uvに互換範囲を読み取らせ、`lowest` resolution strategyで下限を選択する。`azd package api`はDockerfileを直接buildするため、Dockerのuv versionだけは下限を明記し、taskで`pyproject.toml`との一致を検査する。
9. dependency update時のpublic lockはGitHub Actionsのread-only artifact workflowで生成し、botのcommit permissionまたはwrite permissionを使わない。
10. ADR-013のroot lock一本化、Dockerのuv sync、post-edit hookの自動sync許可を上記方式へamendする。post-edit hookは依存関係の整合性を判定せず、project venvのruffを直接実行する。uv workspaceによる統一とADR-009/012のdeploy単位分離は維持する。
11. public lock の validator は厳格な読み取り専用検査として維持する。限定的な自動修復は、CLI から明示実行する `sync-dev-approved-index` と `package-api-approved-index` だけに許可する。暗黙の同期、QA、レビュー、Docker 内を含む検査経路へ修復許可を伝播させず、不正な lock では停止する。
    - 有効な approved-index 設定と、現 worktree の固定した Git HEAD にある検査済み public lock を修復の前提とする。staged lock が HEAD と異なる場合、lock または関連 manifest が競合中の場合、root/member の `pyproject.toml` 全体に HEAD との差分がある場合は停止する。
    - 許容差分は各 package の `source.registry`、その package の sdist/wheel の `url`、同じ artifact の既存 `size` / `upload-time` の消失だけとする。同名キーを他の位置で除外しない。package 数、名前、版、依存関係、marker、workspace source、artifact 数、hash、その他すべてのフィールドは一致を要求し、`size` / `upload-time` の追加や値変更も拒否する。
    - lock 単位で修復を排他し、置換直前に HEAD、index、対象 manifest、lock が読み取り時点から変わっていないことを再確認する。比較元不在、解析不能、条件外差分、処理中の変更では差分を保持して停止する。成立時だけ原子的に正本へ戻して通知し、既存 validator と同期中の lock hash 検査を適用する。
    - 既に有効な public lock には修復用の Git 条件を課さず、通常環境と CI の依存更新を維持する。取得元の検査を manifest と lock の依存整合性検査とは扱わない。
12. 専用 API build は固定ローカルタグをやめ、成功した build のローカル image ID に対応する一意な参照を出力する。専用 deploy は `--image` でその参照を明示的に受け取り、未指定、不在、識別不一致、対象外 architecture ではクラウド変更前に停止する。既存の `azd deploy api --from-package` 経路で公開と適用を行い、OTel hook を維持する。
13. 成果物の照合は専用 build の単一 `linux/arm64` に限定する。ローカル image ID、公開先の manifest/config、Deployment の参照と所属する稼働 Pod の `app` container の対応を確認し、rollout 未完了、Pod 不在、未 Ready、不一致、判別不能を成功扱いにしない。各種 digest を同じ値として直接比較せず、通常の manifest と build の attestation に必要な index だけを扱う。同じ内容の再利用は許容するが、専用 task が異なる内容に同じ参照を発行しないことを契約とする。
14. 通常の `azd up` は事前 build を引き継がず、public PyPI を使う API build を維持する。管理対象環境の初回構築は既存順序に沿う明示的な逐次操作、API 更新は専用 build と参照を指定する deploy の2操作とし、独自の全体オーケストレーターを追加しない。Python package index の制限と公開コンテナレジストリの制限は分け、この例外経路は公開コンテナレジストリと組織 ACR を利用できる環境を対象とする。Azure Functions remote build は public PyPI の利用を維持する。操作手順と認証前提は [デプロイ文書](../deployment.md) に集約する。

## Consequences
- 管理対象環境は public lock と bit-identical な依存関係を再現できる。
- 一時requirementsの生成と通常のtask processごとの再同期が必要になる。full レビューでは、隔離先での準備成功を確認したレビュー処理が後続 QA への引渡しを担う。approved-indexが成立条件を満たさない場合、環境構築は失敗する。
- 明示した2つの task は、限定条件に合致する場合に限り `uv.lock` を修復する副作用を持つ。排他に協調しない外部プロセスの書き込みを完全には防げない。
- 成果物の同一性は適用後の確認時点を対象とする。将来の外部操作によるタグの上書きと再 pull まで不変性を保証しない。

## 採用しなかった代替案
- **管理対象環境専用の lock**: 正本が二重化するため採用しない。
- **approved-index への正本移行または public source への fallback**: 公開利用者へ組織固有の設定を課すか、管理対象環境の制約を回避するため採用しない。
- **最新成果物の管理ファイルや暗黙の選択**: deploy 対象が隠れた状態に依存するため、参照の明示受け渡しを選ぶ。
- **digest 固定や署名**: 将来の不変性や出自証明は今回の契約外であり、公開と適用の契約を広げるため採用しない。
- **汎用 registry/runtime resolver**: 単一 Arm64 build の照合に対象を絞り、任意の形式や multi-platform 対応の複雑さを持ち込まない。

## 関連 ADR
- ADR-013: 依存関係同期の該当部分を amend し、workspace 統一を維持する。
- ADR-009、ADR-012: deploy 単位分離を維持する。
