# ADR-023: Python 依存の採用を公開から 7 日経過した版に限る

## Status

Accepted

- Date: 2026-09-22

## Context

Python 依存の更新候補には、公開直後の版が含まれる。公開直後の版は、このリポジトリが依存を採用できる package index にまだ揃っていない場合がある。採用できない版で更新候補が作られると、取り込めない Pull Request がノイズとして残る。

[ADR-017](017-approved-index-conversion-for-managed-environments.md) により、public PyPI source の root `uv.lock` が依存関係の唯一の参照元であり、lock は read-only artifact を返す GitHub Actions で生成する。したがって待機期間は、更新候補の提示側と lock の解決側の双方で成立している必要がある。

## Decision

Python 依存の更新候補は、公開から 7 日を経たものだけを採用する。これを二層で実現する。

1. **Renovate 側（直接依存）**: `.github/renovate.json` の pep621 package rule に `minimumReleaseAge` を指定する。security update 経路（`vulnerabilityAlerts`）は既定でこの判定を無視するため、同じ値を明示的に指定して上書きする。
2. **uv 側（推移的依存を含む解決全体）**: `.github/workflows/refresh-uv-lock.yml` が `uv lock --exclude-newer <cutoff>` で解決する。cutoff は `lock-cutoff` task が now-7d を UTC 深夜へ floor して算出する。この値は生成された `uv.lock` の `[options].exclude-newer` に記録され、`check-uv-lock` task が記録値を読んで期間充足を検査したうえで、同じ値で `uv lock --check` を実行する。cutoff が記録されていない `uv.lock` は検査を通らない（fail-closed）。

日数の定義元は `scripts/tasks.py` の `PYTHON_RELEASE_COOLDOWN_DAYS` とし、Renovate 設定の値もそこから導出した契約として `check-version-pins` が検査する。

### 7 日という値

公開されている待機期間は 1 日から 14 日まで幅があり、単一の標準はない。このリポジトリは 7 日で開始し、運用状況に応じて適宜見直す。

## Consequences

- 直接依存と推移的依存の双方について、採用する版が公開から 7 日を経ている。
- 更新候補は 7 日間 Dependency Dashboard に保留され、branch も Pull Request も作られない。見落としにはならないが、適用は遅れる。
- security update も同じ期間を待つため、脆弱性への露出期間が上流の既定より延びる。これを代償として受け入れる。
- `uv.lock` は cutoff を記録した状態でのみ検査を通る。ローカルでの素の `uv lock` は検査に失敗するため、lock の更新は所定の workflow 経由に限られる。
- 期間を変えるときは `PYTHON_RELEASE_COOLDOWN_DAYS` を変更し、Renovate 設定を追随させる。乖離は `check-version-pins` が失敗させる。

## 採用しなかった代替案

- **Renovate 側だけ、または uv 側だけの一層構成**: Renovate の `minimumReleaseAge` は直接依存にしか効かず、推移的依存には届かない。uv 側 cutoff だけでは Renovate が期間内の版で Pull Request を作り、取り込めない候補がノイズとして残る。
- **security update を待機の対象外にする**: 上流の既定はそうなっているが、それでは採用できない版で Pull Request が作られる。露出期間が延びることを代償として受け入れ、同じ期間を適用する。
- **package 単位で期間を短縮する bypass の提供**: 検査の迂回路になるため用意しない。急ぎの場合は、期間と設定値の見直しとして保守者が判断する。
- **cutoff をジョブごとに再計算する**: `uv lock --check` は `uv.lock` に記録された `exclude-newer` と同じ値を渡さないと失敗するため、lock 自体を参照元とする必要がある。

## 関連 ADR

- ADR-017: public lock を唯一の参照元とし、read-only artifact で生成する判断を前提とする。
