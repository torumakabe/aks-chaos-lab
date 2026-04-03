# ADR-002: コンテナーネットワークログ（ACNS 保存ログ）の導入

## Status

Accepted

## Context

AKS コンテナーネットワークログが 2026年3月に GA した。本プロジェクトは ACNS + Cilium が有効済みで前提条件を満たしている。Chaos Engineering 実験（NetworkChaos, DNSChaos）でネットワークフローの可視性が不足しており、ADR-001 で記録した「HTTPChaos は Envoy 層で注入されるためアプリ層メトリクスでは観測不可」の課題の延長として、ネットワーク層の可観測性向上が求められた。

## Decision

- **CRD ベースの保存ログ（ContainerNetworkLog）を採用**: ACNS の保存ログモードを使用。CRD で対象 Pod・プロトコル・判定を宣言的に指定し、eBPF でキャプチャしたフローログをノード上に書き出す。Hubble CLI（オンデマンドモード）は mTLS 証明書のセットアップが煩雑で、リアルタイム観測の即時的な必要性も低いため却下。
- **Log Analytics 連携（DCR ストリーム + DCE）を追加**: ContainerNetworkLogs テーブルへの長期保存と KQL 分析を可能にするため、Container Insights の DCR に `Microsoft-ContainerNetworkLogs` ストリームを追加。高ログ量に対応するため Data Collection Endpoint（DCE）を作成。ノードローカルログのみでは Chaos 実験後の事後分析に不便なため。
- **L7 フローログは対象外**: ACNS の L7 ポリシーは Istio managed addon との併用不可。本プロジェクトは Gateway API (approuting-istio) を使用しており、L7 ログを有効化できない。現在の `advancedNetworkPolicies: 'FQDN'` は L4 FQDN であり L7 ではない。
- **CRD のフィルタは chaos-lab namespace の chaos-app Pod に限定**: ログ量とコストを制御するため、全 Pod ではなく実験対象 Pod に絞る。プロトコルは tcp/udp/dns、判定は forwarded/dropped の両方を記録。DNS フローは FQDN ネットワークポリシーの存在により記録される。

## Consequences

- **利点**: NetworkChaos（遅延・ロス）、DNSChaos 実験時のネットワークフローが Log Analytics で検索可能になる。ADR-001 の「Envoy 層で注入される障害はアプリ層で観測不可」の課題に対し、ネットワーク層からの補完情報を提供する。
- **制約**: CRD の apiVersion (`acn.azure.com/v1alpha1`) はドキュメントがまだプレビュー表記であり、GA 後に変更される可能性がある。L7 フローログは Istio 併用の制約により利用不可。Log Analytics への収集量に応じたコスト増がある（CRD フィルタで軽減）。AKS の `containerNetworkLogs` プロパティは GA Bicep スキーマに未収録のため `#disable-next-line BCP081` で抑制が必要（値は文字列 `'Enabled'`、オブジェクトではない）。
- **命名の経緯**: Nov 2025 に Retina → Container Network Logs へのリネームが行われた。CRD 名 (`ContainerNetworkLog`)、CLI フラグ (`--enable-container-network-logs`)、DCR ストリーム名 (`Microsoft-ContainerNetworkLogs`)、LA テーブル名 (`ContainerNetworkLogs`) はすべて新名で動作することを検証済み。旧名（`Microsoft-RetinaNetworkFlowLogs` / `RetinaNetworkFlowLogs`）も互換性のため動作するが、新名を採用する。ドキュメントは一部旧名のままだが、Azure CLI のソースコード（`addonconfiguration.py`）は新名を使用している。
