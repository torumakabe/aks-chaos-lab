# Chaos 実験ガイド

このドキュメントは、Azure Chaos Studio から実行する Chaos Mesh 実験の種類と実行方法をまとめます。ラボの前提構築は [deployment.md](deployment.md)、観測シグナルは [observability.md](observability.md) を参照してください。

## 実験一覧

既定の Arm64 構成で利用する実験は次のとおりです。

| 実験種類 | 障害内容 | 実験リソース名 |
|----------|----------|----------------|
| PodChaos | Pod 障害 (unavailable) | `exp-aks-pod-failure` |
| NetworkChaos | ネットワーク遅延 | `exp-aks-network-delay` |
| NetworkChaos | ネットワーク停止 (ブラックホール / 100% loss) | `exp-aks-network-loss` |
| StressChaos | CPU / メモリストレス | `exp-aks-stress` |
| TimeChaos | システム時刻操作 | `exp-aks-time` |
| HTTPChaos | HTTP 通信障害 | `exp-aks-http` |
| DNSChaos | DNS 解決障害 | `exp-aks-dns` |

## 既定環境で利用できない実験

- **KernelChaos**: Chaos Mesh の[既知の不具合](https://github.com/chaos-mesh/chaos-mesh/issues/4059)により、既定設定では実験リソースを作成しません。
- **IOChaos**: Chaos Mesh は [Arm64 での IOChaos をサポートしていません](https://github.com/chaos-mesh/chaos-mesh/issues/5015)。KernelChaos と同様に既定で無効にし、実験リソース `exp-aks-io` を作成しません。Arm64 構成では有効化しないでください。

## Safeguards 有効時の Pod failure

Pod failure を `Enforce` で実行する前に、Chaos Mesh の `controllerManager.podChaos.podFailure.pauseImage` が `latest` 以外の固定タグを指定していることを確認してください。Safeguards は `latest` タグのイメージへの置き換えを拒否します。本プロジェクトでは `infra/helm/chaos-mesh-values.yaml` に `registry.k8s.io/pause:3.10` を設定しています。変更した場合は Chaos Mesh の Helm chart を再配備してから実験してください。

`chaos-testing` には、障害注入のため各ノード上で privileged 動作する Chaos Mesh の `chaos-daemon` があります。この Pod を動かすため、`chaos-testing` を Deployment Safeguards と PSS の対象外にします。アプリケーション workload は配置しません。実験対象の `chaos-lab` への適用は[Deployment Safeguards の設定](deployment.md#aks-deployment-safeguards)を参照してください。

Chaos Studio が実行結果を `Success` と表示しても、障害が注入されたとは限りません。実験中に PodChaos の `status.experiment.containerRecords` の `phase`、`injectedCount` とイベントを確認します。

```bash
kubectl get podchaos --namespace chaos-testing --output yaml
kubectl get events --namespace chaos-testing --sort-by=.lastTimestamp
```

## Chaos Mesh の更新

Helm は chart の upgrade 時に CRD を更新しません。Chaos Mesh の chart 版を変更するときだけ、更新先の版の CRD bundle を適用してから Helm release を更新してください。同じ chart 版の再デプロイでは CRD の適用は不要です。

```bash
VERSION=2.8.8 # azure.yaml の chart version と一致させる
set -o pipefail
curl -fsSL "https://mirrors.chaos-mesh.org/v${VERSION}/crd.yaml" | kubectl apply --server-side -f -
azd deploy chaos-mesh
```

詳しくは [Chaos Mesh の Helm 更新手順](https://chaos-mesh.org/docs/production-installation-using-helm/#upgrade-chaos-mesh)を参照してください。

## 実行方法

Azure Portal の Chaos Studio、または Azure CLI で実行します。

Pod 障害実験の開始 / 停止例:

```bash
az rest --method post \
  --url "/subscriptions/{subscription-id}/resourceGroups/{rg}/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure/start?api-version=2025-01-01"

az rest --method post \
  --url "/subscriptions/{subscription-id}/resourceGroups/{rg}/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure/cancel?api-version=2025-01-01"
```

## 観察の進め方

1. `uv run --no-project "${PWD}/scripts/tasks.py" load-baseline` で負荷をかける。
2. 別ターミナルで Chaos 実験を開始する。
3. [observability.md](observability.md) の `gateway:chaos_app:*`、Application Insights、Container Insights、Container network logs を確認する。
4. 実験停止後、回復時間と SLI / operational alerts の反応を確認する。

負荷をかけずに実験すると no-traffic 扱いのシグナルが混ざりやすいため、挙動確認では smoke / baseline のどちらかを流した状態で開始してください。

## Local DNS の比較検証

[構築手順](deployment.md#local-dns)で Local DNS を切り替え、アプリの Pod から許可済みの名前を同じ条件で問い合わせて、[キャッシュと DNS の指標](observability.md#local-dns)を比較します。

DNS 障害注入には既存の `exp-aks-dns` を使います。DNSChaos は Pod の resolver を Chaos DNS Server へ切り替えるため、Local DNS の上流停止や stale 応答の継続性を評価する実験ではありません。Redis 接続の再利用で HTTP エラーが出ない場合もあるため、DNS 応答と接続状態を区別してください。
