# ADR 一覧

Status 列は基本 Status の要約であり、既存の個別注記は補助情報として示す。適用範囲は各 ADR 本文の Status 節と、そこから参照する後続 ADR で確認する。表記の扱いは [判断の変更と Status](README.md#判断の変更と-status) を参照する。

| 番号 | タイトル | Status | 作成日 |
|------|---------|--------|--------|
| 001 | [マネージド NGINX から Gateway API (App Routing Istio) への移行とアプリ層メトリクスの採用](001-gateway-api-migration-and-app-level-metrics.md) | Accepted | 2026-04-01 |
| 002 | [コンテナーネットワークログ（ACNS 保存ログ）の導入](002-container-network-logs.md) | Accepted | 2026-04-02 |
| 003 | [Container Insights データ収集プリセットの Custom 化](003-container-insights-custom-preset.md) | Accepted | 2026-04-03 |
| 004 | [Envoy Gateway メトリクスによる SLO/SLI signal 監視](004-envoy-gateway-metrics-for-slo.md) | Accepted | 2026-04-04 |
| 005 | [AKS 診断ログの Basic テーブル収集](005-aks-diagnostics-basic-logs.md) | Accepted | 2026-04-07 |
| 006 | [Application Insights OTLP 統合とベンダー非依存 OTel 計装への移行](006-otlp-vendor-neutral-otel.md) | Accepted | 2026-04-10 |
| 007 | [ACNS Advanced Network Policies の L7 化と Cilium L7 HTTP 可観測性の導入](007-acns-l7-observability.md) | Accepted | 2026-04-17 |
| 008 | [AKS ノード OS を Ubuntu 24.04 (osSKU: Ubuntu2404) に明示ピン留め](008-node-os-ubuntu2404.md) | Accepted | 2026-04-24 |
| 009 | [Azure Monitor SLI と Prometheus operational alerts の役割分担](009-azure-monitor-sli-and-prometheus-slo.md) | Accepted | 2026-05-01 |
| 010 | [AKS Automatic は本リポジトリで非サポート (Deployment Safeguards が chaos-mesh と非互換)](010-aks-automatic-unsupported-due-to-deployment-safeguards.md) | Accepted | 2026-05-06 |
| 011 | [外形 availability test を Azure Monitor SLI の正本にする](011-external-availability-sli-publisher.md) | Superseded | 2026-05-19 |
| 012 | [Azure Functions direct probe を Azure Monitor SLI の正本にする](012-functions-direct-external-sli-probe.md) | Accepted (Latency 部分は ADR-014 で amend) | 2026-05-20 |
| 013 | [uv workspace でツーリングを統一しつつデプロイ単位は分離維持](013-uv-workspace-unified-tooling.md) | Accepted (root lock 一本化、Docker uv sync、post-edit hook 自動 sync 許可の該当部分は ADR-017 で amend) | 2026-05-21 |
| 014 | [Latency SLI を `le` bucket と `eq` filter で定義する](014-histogram-bucket-latency-sli.md) | Accepted | 2026-05-21 |
| 015 | [Azure リソース名への resourceToken サフィックス付与ルール](015-resource-token-suffix-naming.md) | Accepted | 2026-06-28 |
| 016 | [Azure Chaos Studio Workspace の採用](016-azure-chaos-studio-workspace-adoption.md) | Rejected | 2026-07-16 |
| 017 | [管理対象環境向け approved-index 変換フロー (ADR-013 の一部を amend)](017-approved-index-conversion-for-managed-environments.md) | Accepted (`review-repo-full` の隔離コピーと環境引渡しは ADR-021 で amend) | 2026-08-08 |
| 018 | [NAPをArm64 workloadの追加capacityに採用する](018-adopt-aks-node-auto-provisioning-for-arm64-capacity.md) | Accepted (既定値と未設定時の扱いは ADR-020 で amend) | 2026-08-21 |
| 019 | [AKS Local DNS を既定で採用する](019-adopt-aks-local-dns.md) | Accepted | 2026-09-07 |
| 020 | [Node Auto Provisioning を既定で有効にする](020-enable-node-auto-provisioning-by-default.md) | Accepted | 2026-09-09 |
| 021 | [full レビューを専用 worktree の既存 task で直接実行する](021-run-full-review-directly-in-dedicated-worktree.md) | Accepted | 2026-09-12 |
