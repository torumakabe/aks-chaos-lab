#!/usr/bin/env bash
set +x
set +a
unset PEM LINE
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ] || { [ "$#" = 2 ] && [ "$2" != "--cleanup-only" ]; }; then
  echo "Usage: bash scripts/import-sre-agent-github-key.sh ENV [--cleanup-only]" >&2
  exit 2
fi

ENV=$1
NAME=sre-github-key-import
OWNER=aks-chaos-lab-github-import
CRYPTO_OFFICER=14b46e9e-c2b7-41b4-b07b-48a6ebf60603
# 旧版で誤って付与したロールも、この専用 identity の撤去時だけ削除する。
LEGACY_CERTIFICATES_OFFICER=a4417e6f-fecd-4de8-b567-7b0420556985
for tool in az azd kubectl kubelogin; do
  command -v "$tool" >/dev/null || { echo "Required command not found: $tool" >&2; exit 1; }
done
SUB=$(azd env get-value AZURE_SUBSCRIPTION_ID -e "$ENV")
RG=$(azd env get-value AZURE_RESOURCE_GROUP -e "$ENV")
AKS=$(azd env get-value AZURE_AKS_CLUSTER_NAME -e "$ENV")
VAULT=$(azd env get-value AZURE_SRE_AGENT_VAULT_NAME -e "$ENV")
for value in "$SUB" "$RG" "$AKS" "$VAULT"; do
  [ -n "$value" ] && [ "$value" != null ] || { echo "Missing azd environment output." >&2; exit 1; }
done
SCOPE="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$VAULT"
WORK=$(mktemp -d)
export KUBECONFIG="$WORK/kubeconfig"
TTY_STATE=
PEM=
LINE=
CLEANUP_ENABLED=false

cleanup_resources() {
  local namespace_info identity_owner principal roles role
  namespace_info=$(kubectl get namespace "$NAME" --ignore-not-found \
    -o 'jsonpath={.metadata.name}:{.metadata.labels.aks-chaos-lab-import}') || return 1
  if [ -n "$namespace_info" ]; then
    if [ "$namespace_info" != "$NAME:$OWNER" ]; then
      echo "Refusing to delete namespace $NAME: ownership label differs." >&2
      return 1
    fi
    kubectl delete namespace "$NAME" --wait=true --timeout=180s || return 1
  fi
  identity_owner=$(az identity list --subscription "$SUB" -g "$RG" \
    --query "[?name=='$NAME'].tags.importOwner | [0]" -o tsv) || return 1
  principal=$(az identity list --subscription "$SUB" -g "$RG" \
    --query "[?name=='$NAME'].principalId | [0]" -o tsv) || return 1
  if [ -n "$principal" ]; then
    if [ "$identity_owner" != "$OWNER" ]; then
      echo "Refusing to delete identity $NAME: ownership tag differs." >&2
      return 1
    fi
    roles=$(az role assignment list --subscription "$SUB" --scope "$SCOPE" \
      --query "[?principalId=='$principal' && (ends_with(roleDefinitionId, '/$CRYPTO_OFFICER') || ends_with(roleDefinitionId, '/$LEGACY_CERTIFICATES_OFFICER'))].id" \
      -o tsv) || return 1
    while IFS= read -r role; do
      [ -z "$role" ] || az role assignment delete --subscription "$SUB" --ids "$role" || return 1
    done <<< "$roles"
    az identity delete --subscription "$SUB" -g "$RG" -n "$NAME" || return 1
  fi
}

finish() {
  local rc=$?
  trap - EXIT INT TERM HUP
  set +e
  unset PEM LINE
  if [ -n "$TTY_STATE" ]; then
    stty "$TTY_STATE" </dev/tty || rc=1
    printf '\n' >/dev/tty
  fi
  if [ "$CLEANUP_ENABLED" = true ] && ! cleanup_resources; then
    echo "一時リソースの撤去が未完了です。再実行時に撤去を再試行します。" >&2
    echo "撤去のみ: bash scripts/import-sre-agent-github-key.sh \"$ENV\" --cleanup-only" >&2
    rc=1
  fi
  rm -f "$WORK/kubeconfig" "$WORK/kubeconfig.lock"
  rmdir "$WORK" || rc=1
  if [ "$rc" != 0 ]; then
    echo "処理は完了していません。原因を解消し、同じコマンドを最初から実行してください。" >&2
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

az aks get-credentials --subscription "$SUB" -g "$RG" -n "$AKS" --file "$KUBECONFIG"
kubelogin convert-kubeconfig -l azurecli
kubectl get namespace default -o name >/dev/null
CLEANUP_ENABLED=true

cleanup_resources
if [ "${2-}" = "--cleanup-only" ]; then
  CLEANUP_ENABLED=false
  echo "一時リソースの撤去が完了しました。"
  exit 0
fi

TENANT=$(az account show --subscription "$SUB" --query tenantId -o tsv)
ISSUER=$(az aks show --subscription "$SUB" -g "$RG" -n "$AKS" \
  --query oidcIssuerProfile.issuerUrl -o tsv)
[ -n "$TENANT" ] && [ -n "$ISSUER" ]
az identity create --subscription "$SUB" -g "$RG" -n "$NAME" \
  --tags "importOwner=$OWNER" -o none
CLIENT=$(az identity show --subscription "$SUB" -g "$RG" -n "$NAME" --query clientId -o tsv)
PRINCIPAL=$(az identity show --subscription "$SUB" -g "$RG" -n "$NAME" --query principalId -o tsv)
az role assignment create --subscription "$SUB" --assignee-object-id "$PRINCIPAL" \
  --assignee-principal-type ServicePrincipal --role "$CRYPTO_OFFICER" --scope "$SCOPE" -o none
az identity federated-credential create --subscription "$SUB" -g "$RG" \
  --identity-name "$NAME" --name import --issuer "$ISSUER" \
  --subject "system:serviceaccount:$NAME:import" --audiences api://AzureADTokenExchange -o none

kubectl apply -f - <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: $NAME
  labels:
    aks-chaos-lab-import: $OWNER
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: import
  namespace: $NAME
---
apiVersion: v1
kind: Pod
metadata:
  name: import
  namespace: $NAME
spec:
  serviceAccountName: import
  automountServiceAccountToken: false
  restartPolicy: Never
  containers:
    - name: cli
      image: mcr.microsoft.com/azure-cli:2.77.0
      command: ["/bin/sh", "-c", "sleep 3600"]
      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          memory: 512Mi
      volumeMounts:
        - name: key
          mountPath: /key
        - name: token
          mountPath: /token
          readOnly: true
  volumes:
    - name: key
      emptyDir:
        medium: Memory
    - name: token
      projected:
        sources:
          - serviceAccountToken:
              audience: api://AzureADTokenExchange
              expirationSeconds: 3600
              path: jwt
YAML
kubectl -n "$NAME" wait --for=condition=Ready pod/import --timeout=300s
# shellcheck disable=SC2016 # 変数は Pod 内のシェルで展開する。
kubectl -n "$NAME" exec import -- sh -ceu '
  command -v openssl >/dev/null
  umask 077
  trap "rm -f /key/preflight-error.log" EXIT
  retry() {
    label=$1
    shift
    printf "%sを確認しています（最大12回、再試行間隔10秒）。\n" "$label"
    attempt=1
    while ! "$@" >/dev/null 2>/key/preflight-error.log; do
      if [ "$attempt" -ge 12 ]; then
        printf "%sに失敗しました（12回試行）。詳細:\n" "$label" >&2
        cat /key/preflight-error.log >&2
        return 1
      fi
      printf "%sはまだ成功していません。10秒後に再試行します（次回 %s/12）。手元の再ログインは不要です。\n" \
        "$label" "$((attempt + 1))"
      attempt=$((attempt + 1))
      sleep 10
    done
    printf "%sに成功しました。\n" "$label"
  }
  retry "一時 identity の認証" az login --service-principal --username "$1" --tenant "$2" \
    --federated-token "$(cat /token/jwt)" --allow-no-subscriptions -o none
  retry "Vault の Key 一覧取得" az keyvault key list --vault-name "$3" --query "[].name" -o tsv
' sh "$CLIENT" "$TENANT" "$VAULT"

TTY_STATE=$(stty -g </dev/tty)
stty -echo </dev/tty
printf 'GitHub App の秘密鍵（PEM 形式）を入力します。\n形式: パスフレーズなしの RSA 秘密鍵。PKCS#1（BEGIN RSA PRIVATE KEY）または PKCS#8（BEGIN PRIVATE KEY）。\n1Password などから取り出す場合も、このいずれかの形式を選択してください。OpenSSH、公開鍵、証明書、暗号化された秘密鍵は対象外です。\nBEGIN 行から対応する END 行まで全文を貼り付け、Enter を押してください（入力は非表示、Ctrl+C で中止）:\n' >/dev/tty
COMPLETE=false
while IFS= read -r LINE </dev/tty; do
  LINE=${LINE%$'\r'}
  PEM+="$LINE"$'\n'
  case "$LINE" in
    '-----END RSA PRIVATE KEY-----'|'-----END PRIVATE KEY-----')
      COMPLETE=true
      break
      ;;
  esac
  if [ "${#PEM}" -gt 32768 ]; then
    echo "秘密鍵の入力が上限を超えました。" >&2
    exit 1
  fi
done
stty "$TTY_STATE" </dev/tty
TTY_STATE=
printf '\n' >/dev/tty
[ "$COMPLETE" = true ] || { echo "秘密鍵の END 行を読み取れませんでした。" >&2; exit 1; }

# 応答を失った場合も、再実行時は公開鍵を比較して重複登録を防ぐ。
# shellcheck disable=SC2016
printf '%s' "$PEM" | kubectl -n "$NAME" exec -i import -- sh -ceu '
  umask 077
  trap "rm -f /key/app.pem /key/existing.pem" EXIT
  cat > /key/app.pem
  openssl rsa -in /key/app.pem -passin pass: -check -noout </dev/null >/dev/null
  key=sre-agent-github-app-key
  names=$(az keyvault key list --vault-name "$1" --query "[].name" -o tsv)
  exists=false
  for name in $names; do
    [ "$name" != "$key" ] || exists=true
  done
  if [ "$exists" = true ]; then
    az keyvault key download --vault-name "$1" --name "$key" --file /key/existing.pem
    expected=$(openssl rsa -in /key/app.pem -passin pass: -pubout 2>/dev/null)
    actual=$(openssl pkey -pubin -in /key/existing.pem -pubout)
    if [ "$expected" != "$actual" ]; then
      echo "別の秘密鍵が登録済みです。ローテーションは別途実施してください。" >&2
      exit 1
    fi
  else
    az keyvault key import --vault-name "$1" --name "$key" \
      --pem-file /key/app.pem --ops sign verify -o none
  fi
  metadata=$(az keyvault key show --vault-name "$1" --name "$key" \
    --query "[[key.kty, contains(key.keyOps, \`sign\`), contains(key.keyOps, \`verify\`), attributes.enabled]]" -o tsv)
  expected_metadata=$(printf "RSA\tTrue\tTrue\tTrue")
  [ "$metadata" = "$expected_metadata" ] || {
    echo "登録済み Key の種類、操作、または有効状態が想定と異なります。" >&2
    exit 1
  }
  az keyvault key show --vault-name "$1" --name "$key" --query key.kid -o tsv
' sh "$VAULT"
unset PEM LINE
cleanup_resources
CLEANUP_ENABLED=false
echo "秘密鍵の登録確認と一時リソースの撤去が完了しました。"
