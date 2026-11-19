# ADR-027: AKS Standard に Deployment Safeguards を段階的に導入する

## Status

Accepted

- Date: 2026-11-19

## Context

本プロジェクトは AKS Standard 上で Chaos Mesh を使う。Chaos Mesh に必要な特権設定は Pod Security Standards（PSS）の Baseline と競合する。Safeguards の適用範囲を定め、既存ワークロードや Chaos 実験への影響を確認してから拒否モードに移る必要がある。

[ADR-010](010-aks-automatic-unsupported-due-to-deployment-safeguards.md) の AKS Automatic を非サポートとする判断は、引き続き有効とする。

## Decision

- AKS Standard に Deployment Safeguards を導入し、既定のレベルを `Warn`、PSS を `Baseline` にする。
- Chaos Mesh に必要な `chaos-testing` namespace を Safeguards の対象から除外する。実験対象の `chaos-lab` namespace は適用対象とする。
- 本 ADR と Deployment Safeguards の導入を含む PR のマージ時刻から 7 日間（168 時間）を、本プロジェクトの `Warn` チェック期間とする。
- 期間中の既存ワークロードに関する警告と Chaos 実験への影響を確認し、期間終了後、確認結果を基準として `Enforce` の有効化を判断する。警告が未解消の場合、警告の収集や確認が不十分な場合、または Chaos 実験への影響の確認が不十分な場合は `Warn` を維持して調査または修正する。期間の経過だけで自動的に `Enforce` にはしない。
- 導入直後から `Enforce` にする案は、確認前に既存ワークロードの作成や更新が拒否され得るため採用しない。

## Consequences

- `Warn` で既存ワークロードの警告と Chaos 実験への影響を確認してから、拒否モードへの移行を判断できる。確認手順は [デプロイガイド](../deployment.md#aks-deployment-safeguards)を参照する。
- `chaos-testing` は Safeguards による警告や拒否の対象外となる。
- チェック期間中の観測は、すべての操作の安全性を保証しない。
