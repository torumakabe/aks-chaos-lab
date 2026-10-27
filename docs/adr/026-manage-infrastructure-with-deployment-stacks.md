# ADR-026: Deployment Stacks でインフラの削除を管理する

## Status

Accepted

- Date: 2026-10-24

## Context

incremental deployment では、opt-in を無効にしてテンプレートから専用リソースを除外しても、作成済みリソースが残る。[ADR-025](025-azure-sre-agent-diagnostics-and-alert-response.md) の Agent 廃止方法を変更し、Service Group と SLI を含む専用リソースの作成と削除を同じ管理対象から判断できるようにする。

## Decision

- base と sli を別々の subscription scope Deployment Stacks で管理する。テンプレートから外れた管理対象リソースと環境専用 Resource Group は削除する。既存環境は構成を変えずに取り込み、その後に opt-in を無効化する。
- Service Group と SLI の tenant scope リソースも Stack で管理する。環境削除は sli、base の順で行い、Stack 管理対象の削除は azd に任せる。`existing` 参照で利用する外部リソースは取り込まない。Agent 無効化時は共有 VNet と予約済み subnet を定義に残す。
- deny assignment は作成しない。ラボの運用を制限せず、Stack what-if が予測する削除と管理状態の変更を人が確認してから適用する。通常の ARM what-if は管理除外による削除を評価しないため、代替にしない。

## Consequences

- opt-in を無効にした後の配備で、Stack が管理する専用リソースを削除できる。取り込み前に定義から外れていたリソースは対象外となる。
- azd の alpha 機能への依存が増える。無効のまま配備しないよう hook で停止させる。移行、権限反映待ち、機能の無効化と環境削除の手順は [deployment guide](../deployment.md#deployment-stacks-の有効化と既存環境の移行) に置く。
- Key Vault の定義除外による削除は soft-delete となる。環境削除時の purge は azd の標準機能を使う。
- Stack 内部の deployment records は履歴メタデータとして保持し、[Azure の標準 grooming](https://learn.microsoft.com/azure/azure-resource-manager/templates/deployment-history-deletions)に任せる。
