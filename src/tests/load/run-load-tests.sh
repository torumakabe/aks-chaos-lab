#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run-load-tests.sh [baseline|stress|spike]
# Env:
#   BASE_URL       e.g. https://myapp.example.com (optional, auto-detected from Ingress if not set)
#   INGRESS_NAME   default: chaos-app
#   INGRESS_NS     default: chaos-lab
#   USERS          default 50
#   SPAWN_RATE     default 5
#   DURATION       default 120s

profile=${1:-baseline}

# Auto-detect BASE_URL when not provided
if [ -z "${BASE_URL:-}" ]; then
    # 1) Prefer azd environment variable AZURE_INGRESS_FQDN (scheme is http)
    if [ -z "${AZURE_INGRESS_FQDN:-}" ] && command -v azd >/dev/null 2>&1; then
        # Try to load azd env values into current shell context
        eval "$(azd env get-values 2>/dev/null || true)"
    fi

    if [ -n "${AZURE_INGRESS_FQDN:-}" ]; then
        # Normalize: strip scheme/path if provided accidentally
        _fqdn="${AZURE_INGRESS_FQDN}"
        _fqdn="${_fqdn#*://}"
        _fqdn="${_fqdn%%/*}"
        BASE_URL="http://${_fqdn}"
        echo "BASE_URL auto-set from AZURE_INGRESS_FQDN: ${BASE_URL}" >&2
    else
        # 2) Fallback to Kubernetes Ingress auto-detection
        INGRESS_NAME=${INGRESS_NAME:-chaos-app}
        INGRESS_NS=${INGRESS_NS:-chaos-lab}

        echo "BASE_URL not set, attempting to auto-detect from Ingress..." >&2
        echo "  Ingress: ${INGRESS_NAME} in namespace ${INGRESS_NS}" >&2

        # Check if kubectl is available
        if ! command -v kubectl >/dev/null 2>&1; then
            echo "Error: kubectl not found. Please install kubectl or set BASE_URL manually." >&2
            exit 1
        fi

        # Check if the ingress exists
        if ! kubectl get ingress -n "${INGRESS_NS}" "${INGRESS_NAME}" >/dev/null 2>&1; then
            echo "Error: Ingress '${INGRESS_NAME}' not found in namespace '${INGRESS_NS}'." >&2
            echo "Please check your ingress configuration or set BASE_URL manually." >&2
            exit 1
        fi

        # Get the ingress IP
        INGRESS_IP=$(kubectl get ingress -n "${INGRESS_NS}" "${INGRESS_NAME}" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

        if [ -z "${INGRESS_IP}" ]; then
            echo "Error: Could not get LoadBalancer IP from Ingress '${INGRESS_NAME}'." >&2
            echo "The ingress might not be ready or may not have a LoadBalancer IP assigned." >&2
            exit 1
        fi

        # Check if TLS is configured (basic check for tls section in spec)
        HAS_TLS=$(kubectl get ingress -n "${INGRESS_NS}" "${INGRESS_NAME}" -o jsonpath='{.spec.tls}' 2>/dev/null || echo "")

        if [ -n "${HAS_TLS}" ] && [ "${HAS_TLS}" != "null" ]; then
            BASE_URL="https://${INGRESS_IP}"
        else
            BASE_URL="http://${INGRESS_IP}"
        fi

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
