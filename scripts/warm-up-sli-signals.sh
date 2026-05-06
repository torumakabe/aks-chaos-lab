#!/bin/sh
set -eu

if [ "${SKIP_AZURE_MONITOR_SLI_WARMUP:-false}" = "true" ]; then
  echo "[sli-warmup] skipped by SKIP_AZURE_MONITOR_SLI_WARMUP=true" >&2
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "[sli-warmup] curl is required" >&2
  exit 1
fi

if [ -z "${AZURE_MONITOR_SLI_SERVICE_GROUP_ID:-}" ] && command -v azd >/dev/null 2>&1; then
  AZURE_MONITOR_SLI_SERVICE_GROUP_ID=$(azd env get-value AZURE_MONITOR_SLI_SERVICE_GROUP_ID 2>/dev/null || true)
fi

if [ -z "${AZURE_MONITOR_SLI_SERVICE_GROUP_ID:-}" ]; then
  echo "[sli-warmup] Azure Monitor SLI is not enabled; skipping warm-up" >&2
  exit 0
fi

if [ -z "${AZURE_INGRESS_FQDN:-}" ] && command -v azd >/dev/null 2>&1; then
  AZURE_INGRESS_FQDN=$(azd env get-value AZURE_INGRESS_FQDN 2>/dev/null || true)
fi

if [ -z "${AZURE_INGRESS_PUBLIC_IP:-}" ] && command -v azd >/dev/null 2>&1; then
  AZURE_INGRESS_PUBLIC_IP=$(azd env get-value AZURE_INGRESS_PUBLIC_IP 2>/dev/null || true)
fi

if [ -n "${AZURE_INGRESS_FQDN:-}" ]; then
  ingress_host=${AZURE_INGRESS_FQDN#*://}
  ingress_host=${ingress_host%%/*}
  base_url="http://${ingress_host}"
elif [ -n "${AZURE_INGRESS_PUBLIC_IP:-}" ]; then
  base_url="http://${AZURE_INGRESS_PUBLIC_IP}"
else
  echo "[sli-warmup] AZURE_INGRESS_FQDN or AZURE_INGRESS_PUBLIC_IP is required" >&2
  exit 1
fi

gateway_host_header=${GATEWAY_HOST_HEADER:-example.com}
ready_attempts=${SLI_WARMUP_READY_ATTEMPTS:-60}
ready_sleep_seconds=${SLI_WARMUP_READY_SLEEP_SECONDS:-10}
duration_seconds=${SLI_WARMUP_DURATION_SECONDS:-180}
interval_seconds=${SLI_WARMUP_INTERVAL_SECONDS:-2}
recording_attempts=${SLI_WARMUP_RECORDING_ATTEMPTS:-80}
recording_sleep_seconds=${SLI_WARMUP_RECORDING_SLEEP_SECONDS:-30}
host_header=

request() {
  path=$1
  header=$2

  if [ -n "$header" ]; then
    curl -fsS -o /dev/null --max-time 10 -H "Host: $header" "${base_url}${path}"
  else
    curl -fsS -o /dev/null --max-time 10 "${base_url}${path}"
  fi
}

attempt=1
while [ "$attempt" -le "$ready_attempts" ]; do
  if request "/health" ""; then
    host_header=
    break
  fi

  if request "/health" "$gateway_host_header"; then
    host_header=$gateway_host_header
    break
  fi

  echo "[sli-warmup] waiting for app readiness (${attempt}/${ready_attempts}) at ${base_url}/health" >&2
  attempt=$((attempt + 1))
  sleep "$ready_sleep_seconds"
done

if [ "$attempt" -gt "$ready_attempts" ]; then
  echo "[sli-warmup] app did not become ready at ${base_url}/health" >&2
  exit 1
fi

echo "[sli-warmup] app is ready; warming SLI input signals for ${duration_seconds}s" >&2

elapsed=0
while [ "$elapsed" -lt "$duration_seconds" ]; do
  request "/" "$host_header" || true
  request "/health" "$host_header" || true
  sleep "$interval_seconds"
  elapsed=$((elapsed + interval_seconds))
done

echo "[sli-warmup] completed" >&2

if ! command -v az >/dev/null 2>&1; then
  echo "[sli-warmup] az is required to verify Managed Prometheus recording rules" >&2
  exit 1
fi

if [ -z "${AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID:-}" ] && command -v azd >/dev/null 2>&1; then
  AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID=$(azd env get-value AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID 2>/dev/null || true)
fi

if [ -z "${AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID:-}" ]; then
  echo "[sli-warmup] AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID is required to verify recording rules" >&2
  exit 1
fi

prometheus_endpoint=$(
  az resource show \
    --ids "$AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID" \
    --api-version 2023-04-03 \
    --query properties.metrics.prometheusQueryEndpoint \
    -o tsv
)

check_recording_metric() {
  query=$1

  az rest \
    --method get \
    --resource https://prometheus.monitor.azure.com \
    --url "${prometheus_endpoint}/api/v1/query" \
    --url-parameters query="$query" \
    --query 'length(data.result[?metric.cluster_name])' \
    -o tsv 2>/dev/null
}

recording_attempt=1
while [ "$recording_attempt" -le "$recording_attempts" ]; do
  availability_count=$(check_recording_metric 'gateway:chaos_app:http_request_total')
  latency_count=$(check_recording_metric 'gateway:chaos_app:http_request_duration:p95')

  availability_count=${availability_count:-0}
  latency_count=${latency_count:-0}

  if [ "$availability_count" -gt 0 ] && [ "$latency_count" -gt 0 ]; then
    echo "[sli-warmup] Managed Prometheus recording rules are ready" >&2
    exit 0
  fi

  echo "[sli-warmup] waiting for recording rules (${recording_attempt}/${recording_attempts}): availability=${availability_count}, latency=${latency_count}" >&2
  request "/" "$host_header" || true
  request "/health" "$host_header" || true
  request "/" "$host_header" || true
  request "/health" "$host_header" || true
  request "/" "$host_header" || true
  recording_attempt=$((recording_attempt + 1))
  sleep "$recording_sleep_seconds"
done

echo "[sli-warmup] recording rules did not expose cluster_name before timeout" >&2
exit 1
