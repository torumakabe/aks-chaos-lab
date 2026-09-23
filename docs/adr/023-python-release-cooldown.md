# ADR-023: 開発端末に新しい版を要求する更新を公開から 7 日経過した版に限る

## Status

Accepted

- Date: 2026-09-22
- Amended: 2026-09-23（適用対象を Python 依存から開発端末に版を要求する更新へ広げ、uv の `required-version` を下限だけにする）

## Context

依存や tool の更新候補には、公開直後の版が含まれる。公開直後の版は、このリポジトリが依存を採用できる package index にまだ揃っていない場合がある。また、開発端末は brew、mise、winget などの tool manager で tool を最新版に保つが、tool manager が新しい release を提供するまでには遅れがある。公開直後の版を採用する更新や、公開直後の版を下限として要求する更新が入ると、取り込めない Pull Request がノイズとして残るか、最新版を維持している端末でも workspace のコマンドを実行できなくなる。

[ADR-017](017-approved-index-conversion-for-managed-environments.md) により、public PyPI source の root `uv.lock` が依存関係の唯一の参照元であり、lock は read-only artifact を返す GitHub Actions で生成する。したがって Python 依存の待機期間は、更新候補の提示側と lock の解決側の双方で成立している必要がある。

当初の判断は Python 依存だけを対象としていた。uv と azd の下限も同じ理由で端末を使えなくするため、対象を判断基準として定め直す。あわせて、[ADR-017 の Decision 8](017-approved-index-conversion-for-managed-environments.md#decision) が uv に許可していた単一 minor series の互換範囲を、下限だけに変更する。上限があると、新しい minor の公開と同時に tool manager の最新版を使う端末の uv が範囲外になる。待機期間と、Renovate が書き換えられない上限の人手による修正が済むまで、その端末では workspace を使えなくなるためである。

## Decision

### 適用対象の基準

公開から 7 日の待機期間は、「このリポジトリの更新によって、開発端末があらかじめ新しい版を持っていなければならなくなるもの」に適用する。現在の対象は次の 3 つである。

- Python 依存（`uv.lock`）
- uv の `required-version` の下限（`pyproject.toml` と `src/api/Dockerfile` の uv image）
- azd の `azure.yaml` `requiredVersions.azd` の下限

GitHub Actions、CI や cluster で使う image、Helm chart、Bicep の API バージョンなど、端末に版を要求しないものは対象外とする。新しい下限を追加するときは、この基準で対象かどうかを判断する。

### Python 依存

Python 依存の更新候補は、公開から 7 日を経たものだけを採用する。これを二層で実現する。

1. **Renovate 側（直接依存）**: `.github/renovate.json` の pep621 package rule に `minimumReleaseAge` を指定する。security update（`vulnerabilityAlerts`）は既定でこの判定を無視するため、同じ値を明示的に指定して上書きする。
2. **uv 側（推移的依存を含む解決全体）**: `.github/workflows/refresh-uv-lock.yml` が `uv lock --exclude-newer <cutoff>` で解決する。cutoff は `lock-cutoff` task が now-7d を UTC 深夜へ floor して算出する。この値は生成された `uv.lock` の `[options].exclude-newer` に記録され、`check-uv-lock` task が記録値を読んで期間充足を検査したうえで、同じ値で `uv lock --check` を実行する。cutoff が記録されていない `uv.lock` は検査を通らない（fail-closed）。

日数の定義元は `scripts/tasks.py` の `RELEASE_COOLDOWN_DAYS` とし、Renovate 設定の値もそこから導出した契約として `check-version-pins` が検査する。

### uv と azd の下限

uv の `required-version` と azd の `requiredVersions.azd` の下限を引き上げる Renovate の更新にも、同じ待機期間を適用する。下限の引き上げを遅らせ、tool manager が新しい release を提供するまでの遅れを吸収する。

### 端末に要求する版は下限だけにする

開発端末は tool manager で tool を最新版に保つ原則を維持する。そのため端末に要求する版は下限だけにし、上限を置かない。CI と Docker は引き続き下限の版に固定するため、build と CI の再現性は変わらない。

### 7 日という値

公開されている待機期間は 1 日から 14 日まで幅があり、単一の標準はない。このリポジトリは 7 日で開始し、運用状況に応じて適宜見直す。

## Consequences

- 直接依存と推移的依存の双方について、採用する版が公開から 7 日を経ている。
- 更新候補は 7 日間 Dependency Dashboard に保留され、branch も Pull Request も作られない。見落としにはならないが、適用は遅れる。
- security update も同じ期間を待つため、脆弱性への露出期間が上流の既定より延びる。これを代償として受け入れる。
- uv の `required-version` と azd の `requiredVersions.azd` の下限は、公開から 7 日を経た版にだけ引き上げられる。
- 上限を置かないため、未検証の新しい minor の uv や azd での挙動差を端末側では検出できない。新しい minor の公開によって端末で workspace を使えなくなる事態を避けるため、これを代償として受け入れる。CI と Docker は下限の版で検証を続ける。
- 端末に新しい版を要求する下限を追加するときは、基準に照らして待機期間の対象にするかを判断する必要がある。
- `uv.lock` は cutoff を記録した状態でのみ検査を通る。ローカルでの素の `uv lock` は検査に失敗するため、lock の更新は所定の workflow に限られる。
- 期間を変えるときは `RELEASE_COOLDOWN_DAYS` を変更し、Renovate 設定を追随させる。乖離は `check-version-pins` が失敗させる。

## 採用しなかった代替案

- **Renovate 側だけ、または uv 側だけの一層構成**: Renovate の `minimumReleaseAge` は直接依存にしか効かず、推移的依存には届かない。uv 側 cutoff だけでは Renovate が期間内の版で Pull Request を作り、取り込めない候補がノイズとして残る。
- **security update を待機の対象外にする**: 上流の既定はそうなっているが、それでは採用できない版で Pull Request が作られる。露出期間が延びることを代償として受け入れ、同じ期間を適用する。
- **package 単位で期間を短縮する bypass の提供**: 検査の迂回路になるため用意しない。急ぎの場合は、期間と設定値の見直しとして保守者が判断する。
- **cutoff をジョブごとに再計算する**: `uv lock --check` は `uv.lock` に記録された `exclude-newer` と同じ値を渡さないと失敗するため、lock 自体を参照元とする必要がある。
- **uv の単一 minor series の互換範囲を維持する**: 端末での未検証 minor の使用を防げるが、新しい minor の公開と同時に最新版を使う端末が範囲外になる。Renovate は上限を書き換えられないため、待機期間の経過に加えて人手の修正が済むまで端末で workspace を使えない。
- **端末に版を要求しない依存にも待機期間を適用する**: GitHub Actions、CI や cluster の image、Helm chart、Bicep の API バージョンは端末の tool manager の遅れに影響されないため、適用の遅れだけが生じる。

## 関連 ADR

- ADR-017: public lock を唯一の参照元とし、read-only artifact で生成する判断を前提とする。Decision 8 の uv の互換範囲を下限だけに変更する。
