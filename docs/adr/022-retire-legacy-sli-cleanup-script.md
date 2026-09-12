# ADR-022: 旧 SLI リソース移行完了後に専用 cleanup スクリプトを廃止する

## Status

Accepted

[ADR-011](011-external-availability-sli-publisher.md) の legacy resources 削除部分を廃止する。

## Context

ADR-011 は、旧 SLI 入力方式から移行するため、既存環境に残るリソースを `scripts/cleanup-legacy-sli-sources.py` で削除する判断を含んでいた。その後、SLI 入力方式は [ADR-012](012-functions-direct-external-sli-probe.md) と [ADR-014](014-histogram-bucket-latency-sli.md) の request-based SLI に移行した。

このリポジトリに対応する subscription 内の azd deployment scope は `eval` のみである。現行環境には、旧 Prometheus rule group、旧 availability test、旧 smart detector、AKS 内 synthetic traffic 関連リソースが存在しない。一方、現行の request-based Availability SLI と Latency SLI は存在する。専用 cleanup スクリプトが担う一度限りの移行は完了している。

## Decision

- このリポジトリが管理する現行 deployment scope では、ADR-011 の legacy resources 移行を完了とする。
- `scripts/cleanup-legacy-sli-sources.py` と、その実行を前提とする運用文書を廃止する。完了済みの移行処理を継続的な保守機能として保持する案は採用しない。
- 現行の request-based Availability SLI と Latency SLI は削除対象に含めない。
- 将来、別の旧リソースが見つかった場合は、その時点の設計と管理範囲に基づいて対応を判断する。廃止した専用スクリプトの再利用を既定としない。

## Consequences

- リポジトリの運用文書と保守対象は、現行の request-based SLI 構成に限定される。
- ADR-011 の移行時の判断と削除対象は履歴として残り、移行完了後の扱いは本 ADR から追跡できる。
- この判断の適用範囲は、確認済みの subscription と `eval` deployment scope である。別の subscription または管理外環境に同じ移行完了状態があることは保証しない。
