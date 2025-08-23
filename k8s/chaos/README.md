# Chaos Mesh は azd の Helm 統合で導入・削除できます。
# `azure.yaml` の `services.chaos-mesh` に `k8s.namespace: chaos-testing` を指定すると、azd がNSを自動作成します。
# 手動導入を行う場合のみ、`namespace.yaml` を適用してから `helm install` を実行してください。
