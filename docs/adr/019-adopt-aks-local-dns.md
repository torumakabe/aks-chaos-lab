# ADR-019: AKS Local DNS を既定で採用する

## Status

Accepted

- Date: 2026-09-07

## Context

名前解決をノード内でキャッシュし、上流 DNS への問い合わせを減らす機能をラボで評価する。

## Decision

- System pool と [NAP](018-adopt-aks-node-auto-provisioning-for-arm64-capacity.md) が作成するノードで AKS Local DNS を使用し、プロジェクトの既定を `Required` にする。明示的な `Disabled` 指定による無効化と切り戻しを維持する。
- CoreDNS を上流として維持し、Cilium による DNS 名の制限と Hubble の可視性を継続する。

## Consequences

- キャッシュや stale 応答により古い DNS 情報が残り得る。
- CoreDNS のメトリクスだけでは DNS 問い合わせの全量を表さなくなる。Local DNS、CoreDNS、Hubble を分けて観測する。詳細は [可観測性ガイド](../observability.md#local-dns) を参照する。

有効化と無効化の手順は [構築手順](../deployment.md#local-dns) に記載する。
