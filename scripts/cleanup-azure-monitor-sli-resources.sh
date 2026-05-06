#!/bin/sh
set -eu

log() {
  echo "[sli-cleanup] $*" >&2
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "$1 is required"
    exit 1
  fi
}

get_env_value() {
  name=$1
  current=$(printenv "$name" 2>/dev/null || true)
  if [ -n "$current" ]; then
    case "$current" in
      ERROR:*|*"key not found"*|*"Suggestion:"*) ;;
      *) printf '%s' "$current" ;;
    esac
    return 0
  fi

  if command -v azd >/dev/null 2>&1; then
    value=$(azd env get-value "$name" 2>/dev/null || true)
    if [ -n "$value" ]; then
      case "$value" in
        ERROR:*|*"key not found"*|*"Suggestion:"*) ;;
        *) printf '%s' "$value" ;;
      esac
    fi
  fi
}

delete_service_group_sli_resources() {
  service_group_id=$1
  service_group_url=$(resource_url "$service_group_id")

  sli_ids=$(
    az rest \
      --method get \
      --url "${service_group_url}/providers/Microsoft.Monitor/slis?api-version=2025-03-01-preview" \
      --query 'value[].id' \
      -o tsv 2>/dev/null || true
  )

  if [ -n "$sli_ids" ]; then
    for sli_id in $sli_ids; do
      log "deleting SLI ${sli_id}"
      az rest \
        --method delete \
        --url "$(resource_url "$sli_id")?api-version=2025-03-01-preview" \
        --output none
    done
  else
    log "no Service Group scoped SLI resources found"
  fi

  log "deleting Service Group ${service_group_id}"
  az rest \
    --method delete \
    --url "${service_group_url}?api-version=2024-02-01-preview" \
    --output none >/dev/null 2>&1 || log "Service Group is already deleted or inaccessible"
}

resource_url() {
  resource_id=$1

  case "$resource_id" in
    https://management.azure.com/*) printf '%s' "$resource_id" ;;
    /*) printf 'https://management.azure.com%s' "$resource_id" ;;
    *) printf '%s' "$resource_id" ;;
  esac
}

discover_service_group_ids() {
  env_name=$1

  if [ -z "$env_name" ]; then
    return 0
  fi

  service_group_prefix="sg-aks-chaos-lab-${env_name}-"
  tab=$(printf '\t')

  az rest \
    --method get \
    --url 'https://management.azure.com/providers/Microsoft.Management/serviceGroups?api-version=2024-02-01-preview' \
    --query "value[?starts_with(name, '${service_group_prefix}')].[name,id]" \
    -o tsv 2>/dev/null \
    | while IFS="$tab" read -r service_group_name service_group_id; do
        suffix=${service_group_name#"$service_group_prefix"}
        case "$suffix" in
          ""|*"-"*) continue ;;
        esac
        printf '%s\n' "$service_group_id"
      done || true
}

delete_otlp_app_insights_dcra() {
  resource_group=$1
  aks_cluster_name=$2

  if [ -z "$resource_group" ] || [ -z "$aks_cluster_name" ]; then
    log "AZURE_RESOURCE_GROUP or AZURE_AKS_CLUSTER_NAME is not set; skipping OTLP DCRA cleanup"
    return 0
  fi

  aks_cluster_id=$(
    az resource show \
      --resource-group "$resource_group" \
      --resource-type Microsoft.ContainerService/managedClusters \
      --name "$aks_cluster_name" \
      --query id \
      -o tsv 2>/dev/null || true
  )

  if [ -z "$aks_cluster_id" ]; then
    log "AKS cluster ${resource_group}/${aks_cluster_name} is already deleted or inaccessible"
    return 0
  fi

  association_id="${aks_cluster_id}/providers/Microsoft.Insights/dataCollectionRuleAssociations/OtlpAppInsightsExtension"
  association_url="$(resource_url "$association_id")?api-version=2024-03-11"

  if ! az rest \
    --method get \
    --url "$association_url" \
    --query id \
    -o tsv >/dev/null 2>&1; then
    log "OTLP App Insights DCRA is already deleted"
    return 0
  fi

  log "deleting OTLP App Insights DCRA ${association_id}"
  az rest \
    --method delete \
    --url "$association_url" \
    --output none

  attempt=1
  while [ "$attempt" -le 12 ]; do
    if ! az rest \
      --method get \
      --url "$association_url" \
      --query id \
      -o tsv >/dev/null 2>&1; then
      log "OTLP App Insights DCRA deleted"
      return 0
    fi

    sleep 5
    attempt=$((attempt + 1))
  done

  log "OTLP App Insights DCRA still exists after delete request"
  exit 1
}

delete_orphan_otlp_managed_rgs() {
  env_name=$1

  if [ -z "$env_name" ]; then
    log "AZURE_ENV_NAME is not set; skipping orphan OTLP managed RG cleanup"
    return 0
  fi

  candidate_rgs=$(
    az group list \
      --query "[?starts_with(name, 'ai_appi-') && ends_with(name, '_managed') && tags.\"azd-env-name\"=='${env_name}'].name" \
      -o tsv 2>/dev/null || true
  )

  if [ -z "$candidate_rgs" ]; then
    log "no orphan OTLP App Insights managed RG found for env ${env_name}"
    return 0
  fi

  subscription_id=$(az account show --query id -o tsv 2>/dev/null || true)
  if [ -z "$subscription_id" ]; then
    log "could not determine subscription id"
    exit 1
  fi

  api_version="2024-03-01"
  force_types="Microsoft.Insights%2FdataCollectionEndpoints,Microsoft.Insights%2FdataCollectionRules"

  for rg in $candidate_rgs; do
    log "force-deleting orphan OTLP App Insights managed RG ${rg}"

    if ! az rest \
      --method delete \
      --url "https://management.azure.com/subscriptions/${subscription_id}/resourcegroups/${rg}?api-version=${api_version}&forceDeletionResourceTypes=${force_types}" \
      --output none 2>/dev/null; then
      log "force delete request failed for ${rg}; skipping"
      continue
    fi

    attempt=1
    last_exists="True"
    while [ "$attempt" -le 60 ]; do
      last_exists=$(az group exists --name "$rg" 2>/dev/null || echo "Unknown")
      if [ "$last_exists" = "False" ]; then
        log "${rg} deleted"
        break
      fi
      if [ "$attempt" -eq 1 ] || [ $((attempt % 6)) -eq 0 ]; then
        log "${rg} still deleting (attempt ${attempt}/60)"
      fi
      sleep 10
      attempt=$((attempt + 1))
    done

    if [ "$last_exists" != "False" ]; then
      log "${rg} still exists after force delete timeout; manual cleanup required"
      exit 1
    fi
  done
}

delete_sli_layer_deployment_records() {
  env_name=$1

  if [ "${AZURE_MONITOR_SLI_FIX_SLI_VOID:-true}" != "true" ]; then
    log "AZURE_MONITOR_SLI_FIX_SLI_VOID=false; skipping SLI layer void prevention (evidence-collection mode)"
    return 0
  fi

  if [ -z "$env_name" ]; then
    log "AZURE_ENV_NAME is not set; skipping SLI sub-scope deployment record cleanup"
    return 0
  fi

  sli_deployments=$(
    az deployment sub list \
      --query "[?tags.\"azd-env-name\"=='${env_name}' && tags.\"azd-layer-name\"=='sli'].name" \
      -o tsv 2>/dev/null || true
  )

  if [ -z "$sli_deployments" ]; then
    log "no SLI sub-scope deployment record found for env ${env_name}"
    return 0
  fi

  for deployment_name in $sli_deployments; do
    log "deleting SLI sub-scope deployment record ${deployment_name}"
    if ! az deployment sub delete --name "$deployment_name" --output none 2>/dev/null; then
      log "failed to delete deployment record ${deployment_name}; continuing"
    fi
  done
}

delete_base_resource_group_sync() {
  env_name=$1
  resource_group=$2

  if [ "${AZURE_MONITOR_SLI_FIX_BASE_VOID:-true}" != "true" ]; then
    log "AZURE_MONITOR_SLI_FIX_BASE_VOID=false; skipping base RG sync delete (evidence-collection mode)"
    return 0
  fi

  if [ -z "$resource_group" ] && [ -n "$env_name" ]; then
    resource_group="rg-aks-chaos-lab-${env_name}"
  fi

  if [ -z "$resource_group" ]; then
    log "AZURE_RESOURCE_GROUP and AZURE_ENV_NAME are not set; skipping base RG sync delete"
    return 0
  fi

  exists=$(az group exists --name "$resource_group" --output tsv 2>/dev/null || echo "unknown")
  case "$exists" in
    true|True|TRUE) ;;
    *)
      log "base resource group ${resource_group} is already deleted or inaccessible (exists=${exists})"
      return 0
      ;;
  esac

  log "deleting base resource group ${resource_group} (synchronous)"
  if ! az group delete --name "$resource_group" --yes --no-wait --output none 2>/dev/null; then
    log "failed to initiate base RG deletion for ${resource_group}"
    exit 1
  fi

  attempt=1
  while [ "$attempt" -le 240 ]; do
    last_exists=$(az group exists --name "$resource_group" --output tsv 2>/dev/null || echo "unknown")
    case "$last_exists" in
      false|False|FALSE)
        log "base resource group ${resource_group} deleted (attempt ${attempt})"
        return 0
        ;;
    esac
    if [ "$attempt" -eq 1 ] || [ $((attempt % 12)) -eq 0 ]; then
      log "${resource_group} still deleting (attempt ${attempt}/240, exists=${last_exists})"
    fi
    sleep 15
    attempt=$((attempt + 1))
  done

  log "base resource group ${resource_group} still exists after delete timeout; manual cleanup required"
  exit 1
}

delete_base_layer_deployment_records() {
  env_name=$1

  if [ "${AZURE_MONITOR_SLI_FIX_BASE_VOID:-true}" != "true" ]; then
    log "AZURE_MONITOR_SLI_FIX_BASE_VOID=false; skipping base layer void prevention (evidence-collection mode)"
    return 0
  fi

  if [ -z "$env_name" ]; then
    log "AZURE_ENV_NAME is not set; skipping base sub-scope deployment record cleanup"
    return 0
  fi

  base_deployments=$(
    az deployment sub list \
      --query "[?tags.\"azd-env-name\"=='${env_name}' && tags.\"azd-layer-name\"=='base'].name" \
      -o tsv 2>/dev/null || true
  )

  if [ -z "$base_deployments" ]; then
    log "no base sub-scope deployment record found for env ${env_name}"
    return 0
  fi

  for deployment_name in $base_deployments; do
    log "deleting base sub-scope deployment record ${deployment_name}"
    if ! az deployment sub delete --name "$deployment_name" --output none 2>/dev/null; then
      log "failed to delete base deployment record ${deployment_name}"
      exit 1
    fi
  done
}

require_command az

phase=${1:-pre}

case "$phase" in
  pre|post) ;;
  *)
    log "unknown cleanup phase: ${phase} (expected 'pre' or 'post')"
    exit 1
    ;;
esac

if [ "${CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES:-false}" != "true" ]; then
  if [ "${AZURE_MONITOR_SLI_CLEANUP_SKIP_UNCONFIRMED:-false}" = "true" ]; then
    log "CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES is not true; skipping cleanup"
    exit 0
  fi
  log "set CONFIRM_DELETE_AZURE_MONITOR_SLI_RESOURCES=true to delete resources"
  exit 1
fi

env_name=$(get_env_value AZURE_ENV_NAME)

if [ "$phase" = "post" ]; then
  delete_orphan_otlp_managed_rgs "$env_name"
  exit 0
fi

service_group_id=$(get_env_value AZURE_MONITOR_SLI_SERVICE_GROUP_ID)
service_group_name=$(get_env_value AZURE_MONITOR_SLI_SERVICE_GROUP_NAME)
resource_group=$(get_env_value AZURE_RESOURCE_GROUP)
aks_cluster_name=$(get_env_value AZURE_AKS_CLUSTER_NAME)

if [ -z "$service_group_id" ] && [ -n "$service_group_name" ]; then
  service_group_id="/providers/Microsoft.Management/serviceGroups/${service_group_name}"
fi

if [ -z "$service_group_id" ]; then
  service_group_id=$(discover_service_group_ids "$env_name")
fi

if [ -z "$resource_group" ] && [ -n "$env_name" ]; then
  resource_group="rg-aks-chaos-lab-${env_name}"
fi

if [ -z "$aks_cluster_name" ] && [ -n "$env_name" ]; then
  aks_cluster_name="aks-aks-chaos-lab-${env_name}"
fi

if [ -n "$service_group_id" ]; then
  for id in $service_group_id; do
    delete_service_group_sli_resources "$id"
  done
else
  log "AZURE_MONITOR_SLI_SERVICE_GROUP_ID is not set; skipping Service Group scoped cleanup"
fi

delete_otlp_app_insights_dcra "$resource_group" "$aks_cluster_name"

# Eliminate the void deployment polling 404 risk by short-circuiting both layers'
# Destroy paths. azd down's Destroy enters voidSubscriptionDeploymentState only when
# CompletedDeployments returns a matching record. Deleting the sub-scope deployment
# records here causes CompletedDeployments to surface ErrDeploymentsNotFound, which
# down.go handles as "No Azure resources were found." and skips voiding entirely.
#
# Order matters:
#   1. SLI record first (sub-scope only, no RG) — graceful skip for SLI layer
#   2. Base RG sync delete — base layer's actual resources gone before record removal
#   3. Base record last — graceful skip for base layer
#
# Without this preventive sequence, a stochastic ARM read-after-write inconsistency
# during the void POST GET poll surfaces as `DeploymentNotFound` and exits non-zero
# even though the deployment record is `Succeeded` in ARM. See docs/workarounds.md D-2.
delete_sli_layer_deployment_records "$env_name"
delete_base_resource_group_sync "$env_name" "$resource_group"
delete_base_layer_deployment_records "$env_name"
