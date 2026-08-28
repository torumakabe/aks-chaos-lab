# ADR-017: 管理対象環境向け approved-index 変換フロー (ADR-013 の一部を amend)

## Status

Accepted

- Date: 2026-08-08

## Context
Public GitHub repository では public PyPI source の root `uv.lock` を唯一の正本とする。一方、public PyPI へ直接アクセスできず、organization-approved package index の使用を必須とする管理対象環境では、ADR-013 の依存関係同期方式を適用できない。

## Decision
1. Public GitHub repository では public PyPI source の root `uv.lock` を唯一の正本として維持する。
2. 管理対象環境では、user-level uv configが単一の`[[index]]`と`default = true`を持つことを検査する。index source、find-links、hash検証、TLS検証、projectまたはdependency groupを変更する設定と環境変数がある場合は同期を中止する。exportと後続の`uv run`はroot projectと対象venvの絶対pathへ固定する。
3. 管理対象環境ではpublic lockのsourceを検査し、`uv export --frozen`で未コミットの一時requirementsへ変換する。変換後のrequirementsも検査してdirect URLを拒否し、registry packageへ`--require-hashes`を適用してapproved-indexから環境を構築する。workspace sourceはindexを介さず参照する。通常環境とは別のuv cacheを使い、過去にpublic PyPIから取得したartifactを再利用しない。approved-indexがpublic lockの許可するbit-identical artifactを提供することを成立条件とする。
4. task runnerが有効なapproved-index設定を検出した場合、各task processの最初のworkspaceコマンドを実行する前に、仮想環境を消去してapproved-indexから同期する。同期開始からtask processの終了までは、対象venvの正規化pathから導出したprocess間lockを保持し、別processによる同じvenvの再構築を防ぐ。同じprocess内の後続コマンドだけは、直前に構築した環境を`--no-sync`で使用する。永続的なsync stateは保存しない。同期の開始時と完了時でlockのhashが異なる場合はコマンドを実行せず、public sourceへ自動fallbackしない。
5. task runnerはscriptの絶対pathと`uv run --no-project`で起動し、taskのstate検査より前に別のprojectを探索または同期しないようにする。
6. GitHub Actions、Azure Functions remote build、外部利用者はpublic PyPIを継続して使用する。
7. Docker buildも同じexportとpip syncを使う。管理対象環境のローカルbuildはbuild argumentでapproved-index modeとconfigのSHA-256を明示し、BuildKit secretでuser-level uv configをmountする。Dockerfileはmountしたconfigのhashを照合し、build modeとhashをdependency layerおよびBuildKit cache mountのkeyへ含める。公開CIにはsecretを渡さない。
8. uvのhostはルート`pyproject.toml`で定義する単一minor seriesの互換範囲を許可する。GitHub Actionsとlock更新workflowはsetup-uvに互換範囲を読み取らせ、`lowest` resolution strategyで下限を選択する。`azd package api`はDockerfileを直接buildするため、Dockerのuv versionだけは下限を明記し、taskで`pyproject.toml`との一致を検査する。
9. dependency update時のpublic lockはGitHub Actionsのread-only artifact workflowで生成し、botのcommit permissionまたはwrite permissionを使わない。
10. ADR-013のroot lock一本化、Dockerのuv sync、post-edit hookの自動sync許可を上記方式へamendする。post-edit hookは依存関係の整合性を判定せず、project venvのruffを直接実行する。uv workspaceによる統一とADR-009/012のdeploy単位分離は維持する。

## Consequences
- 管理対象環境は public lock と bit-identical な依存関係を再現できる。
- 一時requirementsの生成とtask processごとの再同期が必要になる。approved-indexが成立条件を満たさない場合、環境構築は失敗する。

## 採用しなかった代替案
- **管理対象環境専用の lock**: 正本が二重化するため採用しない。
- **approved-index への正本移行または public source への fallback**: 公開利用者へ組織固有の設定を課すか、管理対象環境の制約を回避するため採用しない。

## 関連 ADR
- ADR-013: 依存関係同期の該当部分を amend し、workspace 統一を維持する。
- ADR-009、ADR-012: deploy 単位分離を維持する。
