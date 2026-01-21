#!/bin/bash
# Bicep what-if分析スクリプト（azd環境変数を活用）
# Usage: ./whatif-analyze.sh [--raw] [--template <path>] [--parameters <key=value>]...
#
# 任意のazdプロジェクトで使用可能な汎用スクリプト。
# azdプロジェクトのルートディレクトリ（azure.yamlがある場所）で実行してください。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# オプション解析
RAW_OUTPUT=false
TEMPLATE_FILE=""
EXTRA_PARAMS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --raw)
      RAW_OUTPUT=true
      shift
      ;;
    --template|-t)
      TEMPLATE_FILE="$2"
      shift 2
      ;;
    --parameters|-p)
      EXTRA_PARAMS+=("$2")
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--raw] [--template <path>] [--parameters <key=value>]..."
      echo ""
      echo "Options:"
      echo "  --raw, -r              Output raw what-if results without filtering"
      echo "  --template, -t <path>  Bicep template file (default: auto-detect from azure.yaml)"
      echo "  --parameters, -p <kv>  Additional parameters (can be specified multiple times)"
      echo "  -h, --help             Show this help message"
      echo ""
      echo "Examples:"
      echo "  $0                              # Auto-detect template, filtered output"
      echo "  $0 --raw                        # Raw output without noise filtering"
      echo "  $0 --template infra/main.bicep  # Explicit template path"
      echo "  $0 -p vmSize=Standard_D2s_v3 -p nodeCount=3"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# カレントディレクトリをazdプロジェクトルートとして扱う
PROJECT_ROOT="$(pwd)"

# azure.yamlの存在確認
if [[ ! -f "$PROJECT_ROOT/azure.yaml" ]]; then
  echo "Error: azure.yaml not found in current directory." >&2
  echo "Please run this script from your azd project root directory." >&2
  exit 1
fi

# azdから環境変数を取得
eval "$(azd env get-values 2>/dev/null)" || {
  echo "Error: Failed to get azd environment values." >&2
  echo "Make sure you have initialized the environment with 'azd init' and 'azd env new'." >&2
  exit 1
}

# 必須変数チェック
: "${AZURE_LOCATION:?AZURE_LOCATION is not set. Run 'azd env refresh' first.}"
: "${AZURE_ENV_NAME:?AZURE_ENV_NAME is not set. Run 'azd env refresh' first.}"

# テンプレートファイルの自動検出
if [[ -z "$TEMPLATE_FILE" ]]; then
  # azure.yamlからinfra.pathを検出（デフォルト: ./infra）
  INFRA_PATH=$(grep -E "^\s*path:" "$PROJECT_ROOT/azure.yaml" | head -1 | sed 's/.*path:\s*//' | tr -d '"' || echo "")
  if [[ -z "$INFRA_PATH" ]]; then
    INFRA_PATH="./infra"
  fi

  # main.bicepを探す
  if [[ -f "$PROJECT_ROOT/$INFRA_PATH/main.bicep" ]]; then
    TEMPLATE_FILE="$INFRA_PATH/main.bicep"
  elif [[ -f "$PROJECT_ROOT/infra/main.bicep" ]]; then
    TEMPLATE_FILE="infra/main.bicep"
  else
    echo "Error: Could not auto-detect Bicep template file." >&2
    echo "Use --template option to specify the path." >&2
    exit 1
  fi
fi

echo "=== Bicep What-If Analysis ===" >&2
echo "Project: $PROJECT_ROOT" >&2
echo "Environment: ${AZURE_ENV_NAME}" >&2
echo "Location: $AZURE_LOCATION" >&2
echo "Template: $TEMPLATE_FILE" >&2
echo "Resource Group: ${AZURE_RESOURCE_GROUP:-will be created}" >&2
echo "" >&2

# Bicepテンプレートから許可されたパラメータ名を抽出
ALLOWED_PARAMS=$(grep -E "^param\s+\w+" "$TEMPLATE_FILE" | sed 's/^param \([a-zA-Z_][a-zA-Z0-9_]*\).*/\1/' || true)

# what-if引数を構築
WHATIF_ARGS=(
  --location "$AZURE_LOCATION"
  --template-file "$TEMPLATE_FILE"
  --parameters location="$AZURE_LOCATION"
  --parameters environmentName="$AZURE_ENV_NAME"
)

# azd環境変数からBicepパラメータに一致するもののみを自動検出
# Bicepで定義されたパラメータ名とマッチするものだけを渡す（出力変数を除外）
while IFS='=' read -r key value; do
  # クォートを除去
  value="${value%\"}"
  value="${value#\"}"
  # Bicepで定義されたパラメータのみ渡す
  if echo "$ALLOWED_PARAMS" | grep -qw "$key"; then
    # location と environmentName は既に設定済みなのでスキップ
    if [[ "$key" != "location" && "$key" != "environmentName" ]]; then
      WHATIF_ARGS+=(--parameters "$key=$value")
    fi
  fi
done < <(azd env get-values 2>/dev/null || true)

# 追加パラメータを追加
for param in "${EXTRA_PARAMS[@]}"; do
  WHATIF_ARGS+=(--parameters "$param")
done

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
  # 一般的なAzureリソースで共通するノイズプロパティ
  def is_noise: . as $path | $path | test("provisioningState|etag|principalId|clientId|tenantId|fqdn|azurePortalFQDN|nodeImageVersion|currentOrchestratorVersion|uniqueId|resourceGuid|powerState\\.code|identityProfile|createdAt|modifiedAt|createdBy|lastModifiedBy|systemData");

  # 破壊的変更パターン（リソースの再作成が必要になる可能性があるプロパティ）
  def is_destructive: . as $path | $path | test("networkPlugin|networkPluginMode|subnetId|vnetSubnetID|addressPrefixes|sku\\.name|sku\\.tier|sku\\.capacity|kind|location");

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
