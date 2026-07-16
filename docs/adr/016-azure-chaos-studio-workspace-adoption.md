# ADR-016: Azure Chaos Studio Workspace の採用

## Status

Rejected

## Context

Azure Chaos Studio に Workspace が導入され、既存の `Microsoft.Chaos/experiments` は classic experiments に分類された。本リポジトリでは、現行の AKS Chaos Mesh PodChaos を維持したまま、Chaos 実験の管理を Workspace の Scenario と Configuration へ移行できるかを検討した。

移行には、Workspace が PodChaos を代替する Action をサポートし、管理者がその Action を IaC で定義できるように Microsoft が正式な URN を公開している必要がある。現時点では、Workspace は代替 Action をサポートしておらず、Microsoft はその正式な URN を公開していない。

## Decision

Azure Chaos Studio Workspace を採用せず、AKS Chaos Mesh PodChaos には現行の classic experiments を継続して使用する。

Microsoft が、Workspace で現行の AKS Chaos Mesh PodChaos を代替する Action と、その正式な URN を公開した場合に再検討する。

## Consequences

- Workspace 関連の構成を本リポジトリに追加しない。
- PodChaos を含む classic experiments の構成と運用を維持する。
