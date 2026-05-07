import logging
import os
from contextlib import suppress
from threading import Lock
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics._internal.instrument import (
    Counter,
    Histogram,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
)
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)


# Thread-safe Once pattern implementation (follows OpenTelemetry standards)
class _Once:
    """Ensures a function is called exactly once across multiple threads."""

    def __init__(self):
        self._called = False
        self._lock = Lock()

    def do_once(self, func, *args, **kwargs):
        """Execute func once and return True if executed, False if already called."""
        if self._called:
            return False

        with self._lock:
            if self._called:
                return False
            func(*args, **kwargs)
            self._called = True
            return True


class ErrorAwareSampler(Sampler):
    """Always-sample for error-prone span names/paths, otherwise delegate to ratio.

    SDK の sampler は span 開始時にしか呼ばれないため、span 終了時の
    ``status=ERROR`` を直接判定できない。本サンプラーは現実解として、
    chaos / error / throw を含む span name や HTTP attribute を持つ
    リクエストを 100% サンプル対象とすることで、障害調査時に該当 trace
    が引けない問題を緩和する。post-hoc な ERROR 保証は Collector 側の
    tail-based sampling に委ねる (docs/workarounds.md 参照)。
    """

    _ALWAYS_PATTERNS: tuple[str, ...] = (
        "/chaos/",
        "/throw",
        "/error",
        "chaos",
        "error",
    )
    _PATH_ATTRIBUTE_KEYS: tuple[str, ...] = (
        "http.target",
        "http.route",
        "url.path",
        "http.url",
    )

    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._ratio = TraceIdRatioBased(rate)

    def should_sample(
        self,
        parent_context: Any,
        trace_id: int,
        name: str,
        kind: Any = None,
        attributes: Any = None,
        links: Any = None,
        trace_state: Any = None,
    ) -> SamplingResult:
        haystack_parts: list[str] = [name or ""]
        if attributes:
            for key in self._PATH_ATTRIBUTE_KEYS:
                val = attributes.get(key)
                if isinstance(val, str):
                    haystack_parts.append(val)
        haystack = " ".join(haystack_parts).lower()
        if any(p in haystack for p in self._ALWAYS_PATTERNS):
            return SamplingResult(
                Decision.RECORD_AND_SAMPLE,
                attributes,
                trace_state,
            )
        return self._ratio.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"ErrorAwareSampler(rate={self._rate})"


# Global telemetry components
_meter: metrics.Meter | None = None
_tracer: trace.Tracer | None = None

# Cached instruments for record_redis_metrics / record_redis_status_only.
# 1 回だけ作成して以降は再利用することで instrument の重複登録を防ぐ。
_redis_status_gauge: Any = None
_redis_latency_hist: Any = None

# Connection status backing state for ObservableGauge callback.
# 1=connected, 0=disconnected, -1=unknown (起動直後で record* 未呼出)。
# ObservableGauge は export interval ごとに callback を呼ぶため、
# アイドル時でも値が継続的に export される (no-data 解消)。
_redis_connected_state: int = -1

# Active requests backing state for chaos_app.active_requests ObservableGauge.
# - HTTP middleware が increment_active_requests / decrement_active_requests
#   経由で更新。
# - ObservableGauge を採用する理由: synchronous UpDownCounter は add() が
#   呼ばれない export interval でデータポイントを出さないため、ノートラフィック
#   時に AMW Prometheus に series が出ない。callback 経由なら毎 interval
#   現在値を export でき、no-data を構造的に解消できる。
# - DELTA temporality 設定を回避するため Gauge を採用 (ObservableUpDownCounter
#   + DELTA は "前回観測値との差分" になり "現在値" にならない)。
# - 標準 semconv `http.server.active_requests` ではなく custom metric
#   `chaos_app.active_requests` を使う。FastAPIInstrumentor が出力しうる
#   標準 metric との衝突 (instrumentation scope の異なる同名 series)
#   による運用上の混乱を避ける。
_active_requests_count: int = 0
_active_requests_lock: Lock = Lock()
_active_requests_gauge: Any = None

# Standard OpenTelemetry Once pattern for preventing duplicate initialization
_setup_once = _Once()
_instrumentation_once = _Once()


def _redis_status_callback(_options: CallbackOptions) -> list[Observation]:
    """ObservableGauge callback returning the latest known Redis status.

    -1 (unknown) は record_* が一度も呼ばれていない状態。値を出さないことで
    "未測定" と "0=disconnected" を区別する。
    """
    if _redis_connected_state < 0:
        return []
    return [Observation(_redis_connected_state)]


def _active_requests_callback(_options: CallbackOptions) -> list[Observation]:
    """ObservableGauge callback returning the current in-flight request count.

    Lock 内で値をコピーし、lock 外で Observation を作成する (callback は
    SDK の metric collection thread から呼ばれるため threading.Lock が適切)。
    """
    with _active_requests_lock:
        value = _active_requests_count
    return [Observation(value)]


def setup_telemetry(app: Any) -> None:
    """Configure vendor-neutral OpenTelemetry with OTLP exporter.

    - Respects TELEMETRY_ENABLED setting
    - OTLP endpoint is configured via OTEL_EXPORTER_OTLP_ENDPOINT env var
      (auto-injected by AKS Instrumentation CRD, or set manually)
    - Sampling: env var OTEL_TRACES_SAMPLER (AKS auto-config) を優先。
      未設定時は TELEMETRY_SAMPLING_RATE を base rate にした
      ParentBased(ErrorAwareSampler(rate)) を採用し、parent が sampled なら
      子も sampled、chaos/error 系 path は base rate を無視して 100% sample
      する。
    - Export interval: TELEMETRY_EXPORT_INTERVAL_MS (デフォルト 30s) で
      MeterReader の export 周期を制御し、低トラフィック時の signal 鮮度を
      確保する。
    - Instruments FastAPI (excludes health), Redis, and logging
    - Uses Once pattern to prevent duplicate initialization (thread-safe)
    """

    # Execute core setup once; track whether telemetry was actually enabled
    _telemetry_active = False

    def _setup_core():
        """Core setup function executed only once."""
        nonlocal _telemetry_active
        from app.config import Settings

        settings = Settings()
        if not settings.telemetry_enabled:
            logger.info("Telemetry disabled via TELEMETRY_ENABLED")
            return

        # Skip if no OTLP endpoint is configured (local dev without collector)
        # AKS auto-config injects per-signal endpoints (OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
        # OTEL_EXPORTER_OTLP_METRICS_ENDPOINT) rather than a single OTEL_EXPORTER_OTLP_ENDPOINT.
        has_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
        )
        if not has_endpoint:
            logger.info("No OTEL_EXPORTER_OTLP_ENDPOINT; telemetry disabled")
            return

        try:
            resource = Resource.create(
                {"service.name": "chaos-app", "service.version": "0.1.0"}
            )

            # Sampling: prefer OTEL_TRACES_SAMPLER env var (set by AKS auto-config),
            # fall back to ParentBased(ErrorAwareSampler(rate)).
            sampler: Sampler | None = None
            if not os.getenv("OTEL_TRACES_SAMPLER"):
                sampling_rate = float(settings.telemetry_sampling_rate or 1.0)
                sampler = ParentBased(root=ErrorAwareSampler(sampling_rate))

            # TracerProvider with OTLP/HTTP exporter (binary Protobuf)
            provider_kwargs: dict[str, Any] = {"resource": resource}
            if sampler is not None:
                provider_kwargs["sampler"] = sampler
            tracer_provider = TracerProvider(**provider_kwargs)
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(tracer_provider)

            # MeterProvider with OTLP/HTTP exporter
            # Delta temporality required for Application Insights OTLP
            export_interval_ms = int(settings.telemetry_export_interval_ms)
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    preferred_temporality={
                        Counter: AggregationTemporality.DELTA,
                        UpDownCounter: AggregationTemporality.DELTA,
                        Histogram: AggregationTemporality.DELTA,
                        ObservableCounter: AggregationTemporality.DELTA,
                        ObservableUpDownCounter: AggregationTemporality.DELTA,
                        ObservableGauge: AggregationTemporality.DELTA,
                    }
                ),
                export_interval_millis=export_interval_ms,
            )
            meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )
            metrics.set_meter_provider(meter_provider)

            global _meter, _tracer
            _meter = metrics.get_meter("aks-chaos-lab", "0.1.0")
            _tracer = trace.get_tracer("aks-chaos-lab", "0.1.0")

            # Redis 用 instrument を 1 回だけ作成しモジュールに保持。
            # ObservableGauge は callback 経由で値を export するため、
            # アイドル時 (record_* 未呼出) でも値が継続して観測される。
            global _redis_status_gauge, _redis_latency_hist
            with suppress(Exception):
                _redis_status_gauge = _meter.create_observable_gauge(
                    name="redis_connection_status",
                    description="Redis connection status (1=connected, 0=disconnected)",
                    callbacks=[_redis_status_callback],
                )
            with suppress(Exception):
                _redis_latency_hist = _meter.create_histogram(
                    name="redis_connection_latency_ms",
                    description="Redis connection latency (ms)",
                    unit="ms",
                )

            # Active requests gauge: ノートラフィック時も現在値 (通常 0) を
            # 毎 interval export して AMW Prometheus に series を維持する。
            global _active_requests_gauge
            with suppress(Exception):
                _active_requests_gauge = _meter.create_observable_gauge(
                    name="chaos_app.active_requests",
                    description=(
                        "Number of in-flight HTTP server requests "
                        "(custom metric, excludes /health)"
                    ),
                    unit="{request}",
                    callbacks=[_active_requests_callback],
                )

            logger.info("Telemetry configured (OTel SDK + OTLP exporter)")
            _telemetry_active = True
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to configure telemetry: %s", e)

    def _setup_instrumentation():
        """Instrumentation setup executed only once."""
        try:
            with suppress(Exception):
                FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
            with suppress(Exception):
                RedisInstrumentor().instrument()
            with suppress(Exception):
                LoggingInstrumentor().instrument(set_logging_format=True)
            logger.debug("Instrumentation completed")
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to configure instrumentation: %s", e)

    # Execute core setup once
    was_executed = _setup_once.do_once(_setup_core)
    if was_executed:
        logger.debug("Telemetry core setup executed")
    else:
        logger.debug("Telemetry already configured, skipping duplicate initialization")

    # Execute instrumentation only if telemetry was actually enabled
    if _telemetry_active:
        was_executed = _instrumentation_once.do_once(_setup_instrumentation)
        if was_executed:
            logger.debug("Instrumentation setup executed")
        else:
            logger.debug("Instrumentation already configured, skipping duplicate setup")


def record_span_error(exc: Exception) -> None:
    """Record exception on current span."""
    try:
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.set_status(Status(StatusCode.ERROR, str(exc)))
            current_span.record_exception(exc)
    except Exception as e:  # noqa: BLE001
        logger.debug("record_span_error failed: %s", e)


def reset_telemetry() -> None:
    """Reset telemetry configuration state (for testing).

    Note: This follows OpenTelemetry testing patterns and should only
    be used in test environments to reset Once guards.
    """
    global _setup_once, _instrumentation_once
    global _redis_status_gauge, _redis_latency_hist, _redis_connected_state
    global _active_requests_gauge, _active_requests_count
    _setup_once = _Once()
    _instrumentation_once = _Once()
    _redis_status_gauge = None
    _redis_latency_hist = None
    _redis_connected_state = -1
    _active_requests_gauge = None
    with _active_requests_lock:
        _active_requests_count = 0
    logger.debug("Telemetry state reset for testing")


def increment_active_requests() -> None:
    """Atomically increment the in-flight request count.

    HTTP middleware から request 受信時に呼び出す。GIL 上でも複合更新の
    アトミック性を保証するため threading.Lock で保護する。
    """
    global _active_requests_count
    with _active_requests_lock:
        _active_requests_count += 1


def decrement_active_requests() -> None:
    """Atomically decrement the in-flight request count.

    HTTP middleware の finally ブロックから呼び出す。誤用 (increment 過去無し
    での decrement) や middleware 順序変更時の不整合に備え、count が負値に
    ならないよう underflow 保護を行う。
    """
    global _active_requests_count
    with _active_requests_lock:
        if _active_requests_count <= 0:
            logger.warning(
                "decrement_active_requests called with non-positive count "
                "(=%d); clamping to 0 to avoid underflow",
                _active_requests_count,
            )
            _active_requests_count = 0
        else:
            _active_requests_count -= 1


def record_redis_metrics(connected: bool, latency_ms: int) -> None:
    """Record Redis connection metrics (status + latency).

    実 latency を測定したパスから呼ぶ。アイドル時や latency 未測定パスでは
    record_redis_status_only を使い、histogram に 0ms 等の偽値を入れない。
    instrument は setup_telemetry でキャッシュ済みのものを再利用する。
    """
    # Lazy import to avoid circular dependency
    from app.config import Settings

    settings = Settings()
    if not settings.custom_metrics_enabled or not _meter:
        return
    try:
        global _redis_connected_state
        _redis_connected_state = 1 if connected else 0

        if connected and latency_ms >= 0 and _redis_latency_hist is not None:
            _redis_latency_hist.record(latency_ms)
    except Exception as e:  # noqa: BLE001
        logger.debug("record_redis_metrics failed: %s", e)


def record_redis_status_only(connected: bool) -> None:
    """Record only the connection status without writing latency histogram.

    latency が未測定 (例: メイン処理の最後にまとめて status を記録するパス)
    のとき、0ms 偽値を histogram に書き込むと P50/P95 が大きく歪むため、
    histogram には触れず ObservableGauge の backing state のみ更新する。
    """
    from app.config import Settings

    settings = Settings()
    if not settings.custom_metrics_enabled or not _meter:
        return
    try:
        global _redis_connected_state
        _redis_connected_state = 1 if connected else 0
    except Exception as e:  # noqa: BLE001
        logger.debug("record_redis_status_only failed: %s", e)
