# Chaos 実験ガイド

このドキュメントは、Azure Chaos Studio から実行する Chaos Mesh 実験の種類と実行方法をまとめます。ラボの前提構築は [deployment.md](deployment.md)、観測シグナルは [observability.md](observability.md) を参照してください。

## 利用可能な実験

| 実験種類 | 障害内容 | 実験リソース名 |
|----------|----------|----------------|
| PodChaos | Pod 障害 (unavailable) | `exp-aks-pod-failure` |
| NetworkChaos | ネットワーク遅延 | `exp-aks-network-delay` |
| NetworkChaos | ネットワーク停止 (ブラックホール / 100% loss) | `exp-aks-network-loss` |
| StressChaos | CPU / メモリストレス | `exp-aks-stress` |
| IOChaos | ファイル I/O 遅延 | `exp-aks-io` |
| TimeChaos | システム時刻操作 | `exp-aks-time` |
| HTTPChaos | HTTP 通信障害 | `exp-aks-http` |
| DNSChaos | DNS 解決障害 | `exp-aks-dns` |

KernelChaos は Chaos Mesh の既知不具合により除外しています。詳細は [chaos-mesh/chaos-mesh#4059](https://github.com/chaos-mesh/chaos-mesh/issues/4059) を参照してください。

## 実行方法

Azure Portal の Chaos Studio、または Azure CLI で実行します。

Pod 障害実験の開始 / 停止例:

```bash
az rest --method post \
  --url "/subscriptions/{subscription-id}/resourceGroups/{rg}/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure/start?api-version=2025-01-01"

az rest --method post \
  --url "/subscriptions/{subscription-id}/resourceGroups/{rg}/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure/stop?api-version=2025-01-01"
```

## 観察の進め方

1. `cd src && make load-baseline` で負荷をかける。
2. 別ターミナルで Chaos 実験を開始する。
3. [observability.md](observability.md) の `gateway:chaos_app:*`、Application Insights、Container Insights、Container network logs を確認する。
4. 実験停止後、回復時間と SLI / operational alerts の反応を確認する。

負荷をかけずに実験すると no-traffic 扱いのシグナルが混ざりやすいため、挙動確認では smoke / baseline のどちらかを流した状態で開始してください。
