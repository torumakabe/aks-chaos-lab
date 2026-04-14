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
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
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


# Global telemetry components
_meter: metrics.Meter | None = None
_tracer: trace.Tracer | None = None

# Standard OpenTelemetry Once pattern for preventing duplicate initialization
_setup_once = _Once()
_instrumentation_once = _Once()


def setup_telemetry(app: Any) -> None:
    """Configure vendor-neutral OpenTelemetry with OTLP exporter.

    - Respects TELEMETRY_ENABLED setting
    - OTLP endpoint is configured via OTEL_EXPORTER_OTLP_ENDPOINT env var
      (auto-injected by AKS Instrumentation CRD, or set manually)
    - Sampling is configured via OTEL_TRACES_SAMPLER / OTEL_TRACES_SAMPLER_ARG
      env vars, with fallback to TELEMETRY_SAMPLING_RATE setting
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
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            logger.info("No OTEL_EXPORTER_OTLP_ENDPOINT; telemetry disabled")
            return

        try:
            resource = Resource.create(
                {"service.name": "chaos-app", "service.version": "0.1.0"}
            )

            # Sampling: prefer OTEL_TRACES_SAMPLER env var (set by AKS auto-config),
            # fall back to TELEMETRY_SAMPLING_RATE setting
            sampler = None
            if not os.getenv("OTEL_TRACES_SAMPLER"):
                sampling_rate = float(settings.telemetry_sampling_rate or 1.0)
                if sampling_rate < 1.0:
                    sampler = TraceIdRatioBased(sampling_rate)

            # TracerProvider with OTLP/HTTP exporter (binary Protobuf)
            provider_kwargs: dict[str, Any] = {"resource": resource}
            if sampler is not None:
                provider_kwargs["sampler"] = sampler
            tracer_provider = TracerProvider(**provider_kwargs)
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(tracer_provider)

            # MeterProvider with OTLP/HTTP exporter
            # Delta temporality required for Application Insights OTLP
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
                )
            )
            meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )
            metrics.set_meter_provider(meter_provider)

            global _meter, _tracer
            _meter = metrics.get_meter("aks-chaos-lab", "0.1.0")
            _tracer = trace.get_tracer("aks-chaos-lab", "0.1.0")

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
    _setup_once = _Once()
    _instrumentation_once = _Once()
    logger.debug("Telemetry state reset for testing")


def record_redis_metrics(connected: bool, latency_ms: int) -> None:
    """Record Redis connection metrics if custom metrics enabled."""
    # Lazy import to avoid circular dependency
    from app.config import Settings

    settings = Settings()
    if not settings.custom_metrics_enabled or not _meter:
        return
    try:
        conn_gauge = _meter.create_gauge(
            name="redis_connection_status",
            description="Redis connection status (1=connected, 0=disconnected)",
        )
        conn_gauge.set(1 if connected else 0)

        if connected and latency_ms >= 0:
            lat_hist = _meter.create_histogram(
                name="redis_connection_latency_ms",
                description="Redis connection latency (ms)",
                unit="ms",
            )
            lat_hist.record(latency_ms)
    except Exception as e:  # noqa: BLE001
        logger.debug("record_redis_metrics failed: %s", e)
