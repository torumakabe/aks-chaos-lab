#!/bin/bash
# Bicep what-if分析スクリプト（azd環境変数を活用）
# Usage: ./whatif-analyze.sh [--raw]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# オプション解析
RAW_OUTPUT=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --raw)
      RAW_OUTPUT=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--raw]"
      echo ""
      echo "Options:"
      echo "  --raw    Output raw what-if results without filtering"
      echo "  -h       Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# azdから環境変数を取得
cd "$REPO_ROOT"
eval "$(azd env get-values 2>/dev/null)" || {
  echo "Error: Failed to get azd environment values." >&2
  echo "Make sure you have initialized the environment with 'azd init' and 'azd env new'." >&2
  exit 1
}

# 必須変数チェック
: "${AZURE_LOCATION:?AZURE_LOCATION is not set. Run 'azd env refresh' first.}"
: "${AZURE_ENV_NAME:?AZURE_ENV_NAME is not set. Run 'azd env refresh' first.}"

# AKS SKU（デフォルト: Base）
AKS_SKU_NAME="${AKS_SKU_NAME:-Base}"

echo "=== Bicep What-If Analysis ===" >&2
echo "Environment: ${AZURE_ENV_NAME}" >&2
echo "Location: $AZURE_LOCATION" >&2
echo "AKS SKU: $AKS_SKU_NAME" >&2
echo "Resource Group: ${AZURE_RESOURCE_GROUP:-will be created}" >&2
echo "" >&2

# what-if引数を構築
# Note: main.parameters.jsonはazd専用プレースホルダーを含むため、
#       パラメータは直接指定する
WHATIF_ARGS=(
  --location "$AZURE_LOCATION"
  --template-file infra/main.bicep
  --parameters location="$AZURE_LOCATION"
  --parameters environment="$AZURE_ENV_NAME"
  --parameters aksSkuName="$AKS_SKU_NAME"
)

# 生出力モード
if [[ "$RAW_OUTPUT" == "true" ]]; then
  echo "Running what-if (raw output)..." >&2
  az deployment sub what-if "${WHATIF_ARGS[@]}"
  exit 0
fi

# フィルタリングモード
echo "Running what-if with noise filtering..." >&2
WHATIF_ARGS+=(--no-pretty-print --only-show-errors -o json)

# 一時ファイルに出力（パイプ処理の問題を回避）
TEMP_FILE=$(mktemp)
trap 'rm -f "$TEMP_FILE"' EXIT

az deployment sub what-if "${WHATIF_ARGS[@]}" 2>/dev/null > "$TEMP_FILE"

# jqでフィルタリング
jq '
  # ノイズパターン（読み取り専用/動的な値）
  def is_noise: . as $path | $path | test("provisioningState|etag|principalId|clientId|tenantId|fqdn|azurePortalFQDN|nodeImageVersion|currentOrchestratorVersion|uniqueId|resourceGuid|powerState\\.code|identityProfile");
  
  # 破壊的変更パターン
  def is_destructive: . as $path | $path | test("networkPlugin|networkPluginMode|subnetId|vnetSubnetID|addressPrefixes|sku\\.name|sku\\.capacity");

  # 変更をフィルタリング
  [.changes[]
  | select(.changeType != "NoChange" and .changeType != "NoEffect" and .changeType != "Ignore")
  | {
      resourceId: .resourceId,
      resourceType: (if .resourceId == null or (.resourceId | startswith("[")) then "dynamic" else (.resourceId | split("/") | .[-2] // "unknown") end),
      resourceName: (if .resourceId == null or (.resourceId | startswith("[")) then "dynamic" else (.resourceId | split("/") | .[-1] // "unknown") end),
      changeType: .changeType,
      unsupportedReason: .unsupportedReason,
      significantChanges: (
        if .delta == null then []
        else [.delta[] | select((.path | is_noise | not) and .propertyChangeType != "NoEffect")]
        end
      ),
      potentiallyDestructive: (
        if .delta == null then false
        else ([.delta[] | select(.path | is_destructive)] | length > 0)
        end
      )
    }
  | select((.significantChanges | length > 0) or .changeType == "Create" or .changeType == "Delete" or .changeType == "Unsupported")]
  | {
      summary: {
        total: length,
        create: [.[] | select(.changeType == "Create")] | length,
        modify: [.[] | select(.changeType == "Modify")] | length,
        delete: [.[] | select(.changeType == "Delete")] | length,
        unsupported: [.[] | select(.changeType == "Unsupported")] | length,
        potentiallyDestructive: [.[] | select(.potentiallyDestructive == true)] | length
      },
      changes: .
    }
' "$TEMP_FILE"

echo "" >&2
echo "=== Analysis Complete ===" >&2
echo "Tip: Use 'jq .changes[]' to see individual changes" >&2
echo "Tip: Use 'jq .summary' to see only the summary" >&2
