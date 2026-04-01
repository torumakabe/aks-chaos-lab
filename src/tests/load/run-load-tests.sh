#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run-load-tests.sh [baseline|stress|spike]
# Env:
#   BASE_URL       e.g. http://myapp.example.com (optional, auto-detected from Gateway if not set)
#   GATEWAY_NAME   default: chaos-app
#   GATEWAY_NS     default: chaos-lab
#   USERS          default 50
#   SPAWN_RATE     default 5
#   DURATION       default 120s

profile=${1:-baseline}

# Auto-detect BASE_URL when not provided
if [ -z "${BASE_URL:-}" ]; then
    # 1) Prefer azd environment variable AZURE_INGRESS_FQDN (scheme is http)
    if [ -z "${AZURE_INGRESS_FQDN:-}" ] && command -v azd >/dev/null 2>&1; then
        # Try to load azd env values into current shell context
        # Filter to only valid KEY=value lines to avoid eval errors from azd error messages
        _azd_output=$(azd env get-values 2>/dev/null || true)
        if [ -n "${_azd_output}" ]; then
            eval "$(echo "${_azd_output}" | grep -E '^[A-Za-z_][A-Za-z0-9_]*=' || true)"
        fi
        unset _azd_output
    fi

    if [ -n "${AZURE_INGRESS_FQDN:-}" ]; then
        # Normalize: strip scheme/path if provided accidentally
        _fqdn="${AZURE_INGRESS_FQDN}"
        _fqdn="${_fqdn#*://}"
        _fqdn="${_fqdn%%/*}"
        BASE_URL="http://${_fqdn}"
        echo "BASE_URL auto-set from AZURE_INGRESS_FQDN: ${BASE_URL}" >&2
    else
        # 2) Fallback to Gateway API auto-detection
        GATEWAY_NAME=${GATEWAY_NAME:-chaos-app}
        GATEWAY_NS=${GATEWAY_NS:-chaos-lab}

        echo "BASE_URL not set, attempting to auto-detect from Gateway..." >&2
        echo "  Gateway: ${GATEWAY_NAME} in namespace ${GATEWAY_NS}" >&2

        # Check if kubectl is available
        if ! command -v kubectl >/dev/null 2>&1; then
            echo "Error: kubectl not found. Please install kubectl or set BASE_URL manually." >&2
            exit 1
        fi

        # Gateway の自動生成 Service ({gateway-name}-approuting-istio) から LB IP を取得
        GW_SVC="${GATEWAY_NAME}-approuting-istio"
        if ! kubectl get svc -n "${GATEWAY_NS}" "${GW_SVC}" >/dev/null 2>&1; then
            echo "Error: Gateway Service '${GW_SVC}' not found in namespace '${GATEWAY_NS}'." >&2
            echo "Please check your Gateway configuration or set BASE_URL manually." >&2
            exit 1
        fi

        GW_IP=$(kubectl get svc -n "${GATEWAY_NS}" "${GW_SVC}" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

        if [ -z "${GW_IP}" ]; then
            echo "Error: Could not get LoadBalancer IP from Gateway Service '${GW_SVC}'." >&2
            echo "The Gateway might not be ready or may not have a LoadBalancer IP assigned." >&2
            exit 1
        fi

        # Gateway API (App Routing Istio) は HTTP のみ
        BASE_URL="http://${GW_IP}"

        echo "Auto-detected BASE_URL: ${BASE_URL}" >&2
    fi
fi

: "${BASE_URL:?BASE_URL could not be determined}"

case "$profile" in
  smoke)
    USERS=${USERS:-5}
    SPAWN_RATE=${SPAWN_RATE:-2}
    DURATION=${DURATION:-30}
    ;;
  baseline)
    USERS=${USERS:-50}
    SPAWN_RATE=${SPAWN_RATE:-5}
    DURATION=${DURATION:-120}
    ;;
  stress)
    USERS=${USERS:-200}
    SPAWN_RATE=${SPAWN_RATE:-20}
    DURATION=${DURATION:-300}
    ;;
  spike)
    USERS=${USERS:-300}
    SPAWN_RATE=${SPAWN_RATE:-100}
    DURATION=${DURATION:-120}
    ;;
  *)
    echo "Unknown profile: $profile" >&2
    exit 1
    ;;
esac

export TEST_BASE_PATH="${BASE_URL%/}"

# Change to src directory to access pyproject.toml with dev dependencies
cd "$(dirname "$0")/../../" || exit 1

# Log resolved parameters
echo "[load] profile=${profile} users=${USERS} spawn_rate=${SPAWN_RATE}/s duration=${DURATION}s host=${TEST_BASE_PATH}" >&2

# Run locust in headless mode with our locustfile
uv run --group dev locust -f tests/load/locustfile.py \
  --headless \
  -u "$USERS" \
  -r "$SPAWN_RATE" \
  --run-time "$DURATION" \
  --host "$TEST_BASE_PATH"
